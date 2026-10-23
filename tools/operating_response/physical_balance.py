"""Versioned terminal accounting; only the explicit M15 DG port is a ceiling.

Bounded fork of hospital_ies.resilience._solve_milp. All original balances,
capacities, priority locks, converter bounds and backend recovery remain.
The caller must validate Pe against finite local mechanical/rotor dynamics.
"""
from __future__ import annotations
from dataclasses import asdict
import math
import numpy as np
from scipy.optimize import Bounds,LinearConstraint
from hospital_ies.solver_backend import milp
from hospital_ies.model import Plant,gt_level_properties,illustrative_tariff,RT_TO_KW,KWH_PER_MMBTU
from hospital_ies.resilience import GROUPS,CARRIERS,lex_lock_tolerance

def solve_physical(row,plant:Plant,loads:np.ndarray,availability:dict[str,float],state:RuntimeState,
                dt_h:float,grid_import_limit_kW:float,diesel_start_available:bool,
                gt_ramp_kW_per_h:float,solver_time_limit_sec:float=6.0,
                config:dict|None=None, tracking_targets:dict|None=None,
                security_gt_penalty_USD_per_kWh:float=0.0,
                execution_action:dict|None=None,execution_contract:dict|None=None)->dict:
    """Small GT-unitized MILP with 3 GT on/off binaries + continuous partload.

    v2 retains original four-point turbine performance, while THIS fault
    optimizer uses a full-load heat-rate/steam slope approximation for speed.
    Running GT may operate 40%-100% rated, with ramp-limited output changes.
    Lexicographic P0/P1/P2/P3 service is optimized before operating cost.
    """
    assert len(plant.electric_chillers_rt)==3 and plant.gt_count==3
    # [GT_i kW 0..2, GT_i on 3..5, absorption RT 6, EC RT 7..9,
    #  boiler lbph 10, grid 11, diesel 12, unused steam 13,
    #  group unmet e,h,c 14..25]
    gp=np.arange(0,3);go=np.arange(3,6);ia=6;ie=np.arange(7,10)
    ib=10;ig=11;idg=12;iw=13;iu=14
    # Optional v4 safety-projected action: absolute tracking error variables.
    # All four priority ENS objectives remain unchanged; preferences only
    # influence the final tie-breaking dispatch.
    targets={}
    accepted={'gt_1':0,'gt_2':1,'gt_3':2,'grid_import':ig,'diesel':idg,'absorption_cooling_kW':ia}
    for key,value in (tracking_targets or {}).items():
        if key not in accepted or not math.isfinite(float(value)) or float(value)<0:
            raise ValueError(f'Invalid v4 action target {key}={value}')
        targets[key]=(accepted[key],float(value)/(RT_TO_KW if key=='absorption_cooling_kW' else 1.))
    sci_dependencies=bool((config or {}).get('sci_service_dependencies',False))
    auxiliary_indices=None
    nvars=26+len(targets)
    if sci_dependencies:
        auxiliary_indices=np.arange(nvars,nvars+12).reshape(4,3)
        nvars+=12
    storage_ports=(config or {}).get('_sci_storage_ports')
    storage_indices=None
    if storage_ports is not None:
        storage_names=('ups_discharge_kW','ups_charge_kW','cold_discharge_kW','cold_charge_kW')
        allowed=set(storage_names)|{'cold_pump_kW_per_kW'}
        if not isinstance(storage_ports,dict) or set(storage_ports)-allowed:
            raise ValueError('Unknown finite storage port')
        if any(not math.isfinite(float(v)) or float(v)<0. for v in storage_ports.values()):
            raise ValueError('Invalid finite storage port')
        storage_indices=dict(zip(storage_names,range(nvars,nvars+4)))
        nvars+=4
        if any(min(float(storage_ports.get(d,0.)),float(storage_ports.get(c,0.)))>1e-9
               for d,c in (storage_names[:2],storage_names[2:])):
            raise ValueError('Storage mode must exclude simultaneous charge/discharge')
    idx=lambda i,k:iu+i*3+k
    ppoints,spoints,fpoints=gt_level_properties(plant,float(row.outdoor_temperature_C))
    rated=float(ppoints[-1]);steam_per_kW=float(spoints[-1]/rated);fuel_per_kW=float(fpoints[-1]/rated)
    tariff=illustrative_tariff(row.timestamp,plant)
    c_cost=np.zeros(nvars)
    c_cost[gp]=fuel_per_kW*plant.gas_price_usd_per_mmbtu*dt_h
    c_cost[ib]=plant.steam_kWh_per_lb/plant.boiler_eff/KWH_PER_MMBTU*plant.gas_price_usd_per_mmbtu*dt_h
    c_cost[ig]=tariff*dt_h
    c_cost[idg]=0.30*dt_h # assumption, NOT a measured diesel tariff
    c_cost[iw]=1e-8
    # Scenario-independent, interpretable security proxy: prefer dispatch
    # with lower GT loading AFTER all P0-P3 ENS priorities have been fixed.
    # It is NOT a trip probability nor a manufacturer-certified margin.
    security_weight=float(security_gt_penalty_USD_per_kWh)
    if not math.isfinite(security_weight) or not 0 <= security_weight <= 10.0:
        raise ValueError('security_gt_penalty_USD_per_kWh must be in [0,10]')
    c_cost[gp]+=security_weight*dt_h
    # Tracking in kW-equivalent units, behind hard physics and ENS priority.
    for k, _ in enumerate(targets):c_cost[26+k]=float((config or {}).get('action_tracking_penalty_USD_per_kW_step',0.20))*dt_h
    # P_e + unmet_e = base building/clinical loads + chiller auxiliaries.
    # Steam and cold equations use original v2 heat demand boundary.
    Aeq=np.zeros((3,nvars))
    beq=np.array([loads[:,0].sum(),loads[:,1].sum()/(plant.steam_kWh_per_lb*plant.exchanger_eff),loads[:,2].sum()])
    Aeq[0,gp]=1;Aeq[0,ia]=-plant.abs_electric_kW_per_rt;Aeq[0,ie]=-plant.ec_electric_kW_per_rt
    Aeq[0,ig]=1;Aeq[0,idg]=1
    for i in range(3):Aeq[1,i]=steam_per_kW*availability[f'HRSG_{i+1}']
    Aeq[1,ib]=1;Aeq[1,ia]=-plant.abs_steam_lb_per_rt;Aeq[1,iw]=-1
    Aeq[2,ia]=RT_TO_KW;Aeq[2,ie]=RT_TO_KW
    if storage_indices is not None:
        ud,uc,cd,cc=(storage_indices[name] for name in storage_names)
        pump=float(storage_ports.get('cold_pump_kW_per_kW',0.))
        Aeq[0,ud]=1.;Aeq[0,uc]=-1.;Aeq[0,cd]=Aeq[0,cc]=-pump
        Aeq[2,cd]=1.;Aeq[2,cc]=-1.
    for i in range(4):
        Aeq[0,idx(i,0)]=1
        Aeq[1,idx(i,1)]=1/(plant.steam_kWh_per_lb*plant.exchanger_eff)
        Aeq[2,idx(i,2)]=1
    A=[];low=[];high=[];cap_rows=[]
    def cap(row,upper,lower=-np.inf,name=None,unit='kW',source='physical constraint'):
        A.append(row);low.append(lower);high.append(upper)
        cap_rows.append(dict(name=name or f'physical_row_{len(A)-1}',unit=unit,source=source,kind='physical'))
    if (execution_contract or {}).get('diesel_mode')=='emergency_bus':
        allowed=execution_contract['diesel_allowed_priorities']
        r=np.zeros(nvars);r[idg]=1.
        for i,g in enumerate(GROUPS):
            if g in allowed:r[idx(i,0)]=1.
        cap(r,sum(loads[i,0] for i,g in enumerate(GROUPS) if g in allowed),name='diesel.allowed_bus')
    for i in range(3):
        avail=availability[f'GT_{i+1}']
        # Online unit minimum stable operating point and upper capacity.
        r=np.zeros(nvars);r[i]=1;r[3+i]=-rated*avail;cap(r,0,name=f'GT_{i+1}.capacity')
        r=np.zeros(nvars);r[i]=-1;r[3+i]=0.4*rated*avail;cap(r,0,name=f'GT_{i+1}.minimum_stable')
        if avail>0 and execution_action is None:
            change=gt_ramp_kW_per_h*dt_h
            r=np.zeros(nvars);r[i]=1
            # A loss of capacity forces output down before ordinary ramping.
            reachable_base=min(float(state.gt_prev[i]),rated*avail)
            cap(r,reachable_base+change,max(0.,reachable_base-change),name=f'GT_{i+1}.ordinary_ramp')
    tower=np.zeros(nvars)
    tower[ia]=RT_TO_KW+plant.abs_steam_lb_per_rt*plant.steam_kWh_per_lb
    tower[ie]=RT_TO_KW+plant.ec_electric_kW_per_rt
    tower_bank=sum(availability.get(f'TOWER_{j+1}',1.) for j in range(4))/4.
    cap(tower,plant.cooling_tower_rejection_kW*availability['TOWER']*tower_bank,name='tower.rejection')
    for k, (name, (pindex, desired)) in enumerate(targets.items()):
        unit='RT' if pindex==ia else 'kW'
        r=np.zeros(nvars);r[pindex]=1;r[26+k]=-1;cap(r,desired,name=f'tracking.{name}.positive',unit=unit,source='arrived action target preference')
        r=np.zeros(nvars);r[pindex]=-1;r[26+k]=-1;cap(r,-desired,name=f'tracking.{name}.negative',unit=unit,source='arrived action target preference')
    lows=np.zeros(nvars);highs=np.full(nvars,np.inf)
    if storage_indices is not None:
        for name,index in storage_indices.items():highs[index]=float(storage_ports.get(name,0.))
        for port,carrier in ((ud,0),(cd,2)):
            r=np.zeros(nvars);r[port]=1.
            for g in (0,1):r[idx(g,carrier)]=1.
            cap(r,float(loads[:2,carrier].sum()),name=f'storage.P0_P1_{carrier}_eligibility')
    for i in range(3):
        highs[i]=rated*availability[f'GT_{i+1}']
        highs[3+i]=1 if availability[f'GT_{i+1}']>0 else 0
    highs[ia]=plant.absorption_rt*availability['ABS']
    highs[ie]=np.array(plant.electric_chillers_rt)*np.array([availability[f'EC_{i+1}'] for i in range(3)])
    highs[ib]=plant.boiler_steam_lbph/3*sum(availability[f'BOILER_{i+1}'] for i in range(3))
    highs[ig]=grid_import_limit_kW*availability['GRID']
    highs[idg]=min(plant.emergency_diesel_kW*availability['DIESEL'],state.diesel_energy_remaining_kWh/dt_h) if diesel_start_available else 0.0
    if execution_action is not None and (execution_contract or {}).get('diesel_dispatch_mode')=='arrived_ceiling':
        # M15's finite shaft-work preview locates actual exhaustion. An
        # arbitrary trial duration cannot spread the remaining fuel over Pe.
        highs[idg]=plant.emergency_diesel_kW*availability['DIESEL'] if diesel_start_available else 0.
    if (execution_contract or {}).get('diesel_mode')=='emergency_bus' and availability['GRID']>0:
        highs[idg]=0.
    if execution_action is not None:
        # Physical accounting of an already executed command. Electrical
        # source setpoints are equalities: no hidden redispatch during delay.
        # Heat/cold converters cannot exceed held commands; reduced delivery
        # reflects demand/resource/capacity limitation, not new control power.
        fixed={**{i:execution_action['gt_electric_kW'][i] for i in range(3)},
               ig:execution_action['grid_import_kW'],idg:execution_action['diesel_kW'],
               ib:execution_action['boiler_steam_lbph']}
        for index,value in fixed.items():
            actual=min(highs[index],max(0.,float(value)))
            lows[index]=highs[index]=actual
        if (execution_contract or {}).get('grid_import_mode')=='arrived_ceiling':
            lows[ig]=0. # local port can import up to the ALREADY arrived ceiling
        if (execution_contract or {}).get('diesel_dispatch_mode')=='arrived_ceiling':
            # M15 only: the arrived port is an enable/ceiling. Pe must still
            # balance real connected demand; finite Pm/rotor energy is
            # validated by the separate local dynamic execution core.
            lows[idg]=0.
        highs[ia]=min(highs[ia],execution_action['absorption_cooling_kW']/RT_TO_KW)
        for i,index in enumerate(ie):
            highs[index]=min(highs[index],execution_action['electric_chiller_cooling_kW'][i]/RT_TO_KW)
    for i in range(4):
        for j in range(3):highs[idx(i,j)]=max(0,loads[i,j])
    auxiliary_required=None
    if sci_dependencies:
        from tools.sci_engineering.service import add_dependency_constraints
        def add_dependency(coefficients,lower,upper,name):
            vector=np.zeros(nvars)
            for index,value in coefficients.items():vector[index]=value
            cap(vector,upper,lower,name=name,source='M21 necessary building auxiliary electricity')
        auxiliary_required=add_dependency_constraints(row,loads,availability,auxiliary_indices,
            np.arange(iu,iu+12).reshape(4,3),add_dependency,highs)
    integral=np.zeros(nvars,dtype=int);integral[go]=1
    constraints=[LinearConstraint(Aeq,beq,beq),LinearConstraint(np.asarray(A),np.asarray(low),np.asarray(high))]
    # Opt-in snapshots preserve exact solver arrays before any clipping. The
    # collector is disabled during ordinary execution and changes no argument.
    from hospital_ies.solver_diagnostics import active_collector,solve_layer
    row_metadata=[dict(name=f'balance.{carrier}',unit=unit,source='energy hub balance',kind='physical')
                  for carrier,unit in (('electric','kW'),('steam','lb/h'),('cooling','kW'))]+cap_rows
    variable_names=[f'GT_{i+1}.electric' for i in range(3)]+[f'GT_{i+1}.on' for i in range(3)]+[
        'absorption.cooling','EC_1.cooling','EC_2.cooling','EC_3.cooling','boiler.steam','grid.import','diesel.electric','unused.steam']+[
        f'{g}.unserved.{carrier}' for g in GROUPS for carrier in CARRIERS]+[f'tracking.{name}.absolute_error' for name in targets]
    units=['kW']*3+['binary']*3+['RT']*4+['lb/h','kW','kW','lb/h']+['kW']*12+[
        'RT' if value[0]==ia else 'kW' for value in targets.values()]
    if sci_dependencies:
        from tools.sci_engineering.service import KINDS
        variable_names += [f'P{i}.{kind}.actual_electric' for i in range(4) for kind in KINDS]
        units += ['kW']*12
    if storage_indices is not None:
        variable_names += list(storage_names);units += ['kW']*4
    variables=[dict(index=i,name=name,unit=units[i]) for i,name in enumerate(variable_names)]
    diagnostic_input={}
    if active_collector() is not None:
        diagnostic_input=dict(row=dict(timestamp=str(row.timestamp),outdoor_temperature_C=float(row.outdoor_temperature_C)),
            plant=plant.as_dict(),loads_kW=loads,availability=availability,state=asdict(state),dt_h=dt_h,
            grid_import_limit_kW=grid_import_limit_kW,diesel_start_available=diesel_start_available,
            gt_ramp_kW_per_h=gt_ramp_kW_per_h,config=config,tracking_targets=tracking_targets,
            security_gt_penalty_USD_per_kWh=security_weight,execution_action=execution_action,execution_contract=execution_contract)
    locks=[]
    lex_values=[];x=None;optimizer_warning='';backend_layers=[]
    charge_active=storage_indices is not None and (highs[uc]>0. or highs[cc]>0.)
    for group in range(6 if charge_active else 5):
        charging_layer=charge_active and group==4
        c=np.zeros(nvars) if group<4 or charging_layer else c_cost.copy()
        if charging_layer:
            c[uc]=-1.;c[cc]=-1.
        if group<4:
            for k in range(3):c[idx(group,k)]=1/max(1.,float(loads[:,k].sum()))
        # Native backend retains explicit five layers and the unchanged locks.
        # Equivalent positive row scaling is uniform; finite numerical recovery
        # is registered separately and never grants an execution capability.
        res=solve_layer(milp,c,integrality=integral,bounds=Bounds(lows,highs),constraints=constraints,
            options={'time_limit':solver_time_limit_sec,'mip_rel_gap':1e-6,'presolve':False},rank=group,variables=variables,
            rows=row_metadata,locks=locks,input_context=diagnostic_input,row_scaling=True)
        backend_layers.append(dict(rank=group,backend=getattr(res,'backend','injected'),
            numerical_version=getattr(res,'numerical_version',None),status=int(res.status),
            attempts=getattr(res,'attempts',[])))
        if res.x is None or res.status!=0:
            from hospital_ies.execution import ModelConstraintInfeasible,SolverExecutionError
            error=ModelConstraintInfeasible if res.status==2 else SolverExecutionError
            failure=error(f'v3 MILP failure: rank={group}, status={res.status}, message={res.message}')
            failure.solver_backend_attempts=backend_layers
            raise failure
        x=res.x
        if group<4 or charging_layer:
            val=float(c@x);lex_values.append(val)
            tol=1e-7 if charging_layer else lex_lock_tolerance(group,val,config)
            constraints.append(LinearConstraint(c,-np.inf,val+tol))
            if not charging_layer:
                # This diagnostic list is specifically nonnegative ENS locks.
                # The recharge lock remains an explicit constraint/metadata
                # row, not an ENS implication for the backend preconditioner.
                locks.append(dict(source_rank=group,objective=c.copy(),objective_constant=0.,incumbent_raw_objective=val,
                                  tolerance=tol,upper=val+tol,relation='<=',unit='normalized carrier ENS',source='raw incumbent c@x'))
            row_metadata.append(dict(name='storage.recharge_after_all_service' if charging_layer else f'priority.{GROUPS[group]}.normalized_ENS.upper',unit='dimensionless',
                                     source=f'raw rank {group} objective plus original tolerance',kind='priority_lock'))
    gts=np.maximum(0.,np.asarray(x[gp]))
    lost=np.maximum(0.,np.asarray([[x[idx(i,k)] for k in range(3)] for i in range(4)]))
    if active_collector() is not None:
        processed_x=x.copy();processed_x[gp]=gts
        for i in range(4):
            for k in range(3):processed_x[idx(i,k)]=lost[i,k]
        active_collector().postprocess(processed_x)
    served=loads-lost
    e_actual=float(gts.sum()+x[ig]+x[idg]-x[ia]*plant.abs_electric_kW_per_rt-x[ie].sum()*plant.ec_electric_kW_per_rt)
    c_actual=float((x[ia]+x[ie].sum())*RT_TO_KW)
    if storage_indices is not None:
        e_actual+=float(x[ud]-x[uc]-pump*(x[cd]+x[cc]))
        c_actual+=float(x[cd]-x[cc])
    steam_actual=sum(gts[i]*steam_per_kW*availability[f'HRSG_{i+1}'] for i in range(3)) +x[ib]-x[ia]*plant.abs_steam_lb_per_rt-x[iw]
    steam_residual_lbph=steam_actual-float(served[:,1].sum())/(plant.steam_kWh_per_lb*plant.exchanger_eff)
    steam_residual_kW=steam_residual_lbph*plant.steam_kWh_per_lb*plant.exchanger_eff
    residuals=[e_actual-float(served[:,0].sum()),c_actual-float(served[:,2].sum()),steam_residual_kW]
    if max(map(abs,residuals))>1e-4 or abs(steam_residual_lbph)>1e-4:
        raise RuntimeError(f'v3 balance error kW={residuals}, steam_lbph={steam_residual_lbph}')
    action={'gt_electric_kW':gts.tolist(),'grid_import_kW':float(x[ig]),'diesel_kW':float(x[idg]),
            'absorption_cooling_kW':float(x[ia]*RT_TO_KW),
            'electric_chiller_cooling_kW':(np.asarray(x[ie])*RT_TO_KW).tolist(),
            'boiler_steam_lbph':float(x[ib]),
            'hrsg_steam_lbph':float(sum(gts[i]*steam_per_kW*availability[f'HRSG_{i+1}'] for i in range(3))),
            'tower_rejection_kW':float(tower@x),'gt_fuel_MMBtu_h':float(gts.sum()*fuel_per_kW)}
    if storage_indices is not None:
        action.update({name:float(x[index]) for name,index in storage_indices.items()})
        action['cold_storage_pump_kW']=float(pump*(x[cd]+x[cc]))
    # Tracking deviations are artificial controller penalties, not actual
    # gas/electricity/diesel expenditure. Keep them separate for publications.
    security_penalty=float(security_weight*dt_h*gts.sum())
    operating_cost=float(c_cost[:26]@x[:26])-security_penalty
    tracking_penalty=float(c_cost[26:26+len(targets)]@x[26:26+len(targets)])
    return {'action':action,'lost':lost,'served':served,'lex_values':lex_values,
            'cost_USD_step':operating_cost,'tracking_penalty_USD_step':tracking_penalty,
            'optimization_objective_USD_step':operating_cost+tracking_penalty+security_penalty,
            'security_objective_penalty_USD_step':security_penalty,
            'energy_balance_residuals':residuals,
            'steam_balance_residual_lbph':steam_residual_lbph,'optimizer_warning':optimizer_warning,
            'solver_backend_layers':backend_layers,
            **({'service_dependencies':dict(auxiliary_required_kW=auxiliary_required.tolist(),
                auxiliary_delivered_kW=np.asarray(x[auxiliary_indices]).tolist(),
                direct_electric_delivered_kW=(served[:,0]-np.sum(x[auxiliary_indices],axis=1)).tolist(),
                semantics='aggregate fan and distribution power are necessary for actual thermal delivery; existing electricity reallocated')}
                if sci_dependencies else {})}
