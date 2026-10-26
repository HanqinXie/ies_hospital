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
    m23=(config or {}).get('_model_correctness_v23')
    if m23 is not None and '_m23_boiler_domain' not in (config or {}):
        from tools.model_correctness_v23.domains import solve_execution
        return solve_execution(dict(row=row,plant=plant,loads=loads,availability=availability,state=state,dt_h=dt_h,
            grid_import_limit_kW=grid_import_limit_kW,diesel_start_available=diesel_start_available,
            gt_ramp_kW_per_h=gt_ramp_kW_per_h,solver_time_limit_sec=solver_time_limit_sec,config=config,
            tracking_targets=tracking_targets,security_gt_penalty_USD_per_kWh=security_gt_penalty_USD_per_kWh,
            execution_action=execution_action,execution_contract=execution_contract),solve_physical)
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
    # Explicit opt-in M22 extensions. Historical M15--M21 calls retain their
    # original layout, objective order and physical equations.
    from tools.model_closure_v22.physical import current_context,bridge_withdrawal_without_debt_kWh
    closure=current_context(config)
    # Explicit V25 operating preference; absent on every historical route.
    from tools.model_correction_v25.reserve import validate_solver_context
    sustained_reserve=validate_solver_context(config)
    if sustained_reserve is not None and (closure is None or storage_indices is None or not closure['preserve_bridge']):
        raise ValueError('V25 reserves require current finite storage and bridge layer')
    boiler_on=bridge_debt=recovery_cold=starter_on=boiler_restart_on=None
    cold_reserve_debt=None
    if closure is not None:
        boiler_on=nvars;recovery_cold=nvars+1;starter_on=nvars+2;boiler_restart_on=nvars+3;nvars+=4
        if storage_indices is not None and closure['preserve_bridge']:
            bridge_debt=nvars;nvars+=1
        if sustained_reserve is not None:cold_reserve_debt=nvars;nvars+=1
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
    if closure is not None:
        Aeq[0,ib]-=closure['boiler_kW_per_kWsteam']*plant.steam_kWh_per_lb
        Aeq[0,boiler_on]=-closure['boiler_fixed_kW']
        # A requested starter is either fully supplied or refused. It cannot
        # turn a lawful total outage into an algebraic model failure.
        Aeq[0,starter_on]=-closure['gt_start_kW']
        Aeq[0,boiler_restart_on]=-closure['boiler_restart_kW']
        Aeq[0,recovery_cold]=-(closure['recovery_pump_kW_per_kW']+closure['recovery_fan_kW_per_kW'])
        Aeq[2,recovery_cold]=-1.
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
        if closure is not None:
            pump_available=float(availability.get('AUX_CHW_PUMP',1.))
            highs[cd]*=pump_available;highs[cc]*=pump_available
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
    if closure is not None:
        if not closure['station_ac_available'] or not closure['boiler_ready']:highs[ib]=0.
        highs[boiler_on]=float(closure['station_ac_available'] and closure['boiler_ready'])
        highs[starter_on]=float(closure['station_ac_available'] and closure['gt_start_kW']>0.)
        highs[boiler_restart_on]=float(closure['station_ac_available'] and not closure['boiler_ready'] and closure['boiler_restart_kW']>0.)
        r=np.zeros(nvars);r[ib]=1.;r[boiler_on]=-highs[ib]
        cap(r,0.,name='m22.boiler_requires_station_enabled')
        if m23 is not None:
            # Boiler steam must serve actual building heat or absorption.
            # The inherited unused-steam port remains available for HRSG,
            # but cannot be a destination invented for boiler operation.
            r=np.zeros(nvars);r[ib]=1.;r[ia]=-plant.abs_steam_lb_per_rt
            for g in range(4):r[idx(g,1)]=1./(plant.steam_kWh_per_lb*plant.exchanger_eff)
            cap(r,float(loads[:,1].sum())/(plant.steam_kWh_per_lb*plant.exchanger_eff),name='m23.boiler_has_useful_steam_destination',unit='lb/h')
            domain=(config or {})['_m23_boiler_domain']
            if domain=='off':highs[ib]=highs[boiler_on]=0.
            elif domain=='run':
                if highs[boiler_on]<=0.:
                    from hospital_ies.execution import ModelConstraintInfeasible
                    raise ModelConstraintInfeasible('M23 run domain lacks current station/boiler qualification')
                lows[boiler_on]=1.
            else:raise ValueError('Unknown M23 boiler execution domain')
        r=np.zeros(nvars);r[ib]=closure['boiler_kW_per_kWsteam']*plant.steam_kWh_per_lb
        r[boiler_on]=closure['boiler_fixed_kW'];r[gp]=-1.;r[ig]=r[idg]=-1.
        r[starter_on]=closure['gt_start_kW']
        r[boiler_restart_on]=closure['boiler_restart_kW']
        cap(r,0.,name='m22.station_sources_exclude_building_UPS')
        paths=min(availability.get('AUX_FAN_P0',1.),availability.get('AUX_CHW_PUMP',1.))
        highs[recovery_cold]=closure['recovery_cooling_request_kW']*paths
        if bridge_debt is not None:
            if sustained_reserve is None:
                r=np.zeros(nvars);r[ud]=dt_h/closure['ups_discharge_efficiency'];r[bridge_debt]=-1.
                # Recharge occurs only after service on the historical route.
                cap(r,bridge_withdrawal_without_debt_kWh(closure),name='m22.existing_bridge_withdrawal',unit='kWh')
            else:
                for name,discharge,charge,debt in (('ups',ud,uc,bridge_debt),('cold',cd,cc,cold_reserve_debt)):
                    asset=sustained_reserve[name];r=np.zeros(nvars)
                    r[discharge]=dt_h/asset['discharge_efficiency'];r[charge]=-dt_h*asset['charge_efficiency'];r[debt]=-1.
                    cap(r,asset['retained_energy_kWh']-asset['target_kWh'],name=f'm25.{name}.uncapped_end_inventory_shortfall',unit='kWh')
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
            if closure is not None and index==ib:
                # A source-side power interlock may stop the held boiler;
                # it may not invent a new steam setpoint. When on, its
                # already-arrived steam setting remains exactly the same.
                highs[index]=actual
                if actual<=0.:highs[boiler_on]=0.
                if m23 is None:
                    r=np.zeros(nvars);r[ib]=1.;r[boiler_on]=-actual
                    cap(r,0.,0.,name='m22.boiler_arrived_setting_or_station_trip',unit='lb/h')
            else:lows[index]=highs[index]=actual
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
    service_ceiling=(config or {}).get('_m24_charge_service_ceiling_kW')
    if service_ceiling is not None:
        ceiling=np.asarray(service_ceiling,float)
        if ceiling.shape!=(4,3) or not np.isfinite(ceiling).all() or np.any(ceiling<0.):raise ValueError('Invalid M24 charge service safeguard')
        for g in range(4):
            for carrier in range(3):
                r=np.zeros(nvars);r[idx(g,carrier)]=1.
                cap(r,float(ceiling[g,carrier]),name=f'm24.charge_preserves.P{g}.{CARRIERS[carrier]}',source='conservative recharge-only service safeguard')
    auxiliary_required=None
    if sci_dependencies:
        from tools.sci_engineering.service import add_dependency_constraints,fan_independent_heat_bound
        def add_dependency(coefficients,lower,upper,name):
            vector=np.zeros(nvars)
            for index,value in coefficients.items():vector[index]=value
            cap(vector,upper,lower,name=name,source='M21 necessary building auxiliary electricity')
        auxiliary_required=add_dependency_constraints(row,loads,availability,auxiliary_indices,
            np.arange(iu,iu+12).reshape(4,3),add_dependency,highs)
    integral=np.zeros(nvars,dtype=int);integral[go]=1
    if closure is not None:integral[boiler_on]=integral[starter_on]=integral[boiler_restart_on]=1
    constraints=[LinearConstraint(Aeq,beq,beq),LinearConstraint(np.asarray(A),np.asarray(low),np.asarray(high))]
    # Opt-in snapshots preserve exact solver arrays before any clipping. The
    # collector is disabled during ordinary execution and changes no argument.
    from hospital_ies.solver_diagnostics import active_collector,solve_layer
    if m23 is not None and closure is not None and highs[recovery_cold]>0.:
        from tools.model_correctness_v23.numerics import solve_layer
    row_metadata=[dict(name=f'balance.{carrier}',unit=unit,source='energy hub balance',kind='physical')
                  for carrier,unit in (('electric','kW'),('steam','lb/h'),('cooling','kW'))]+cap_rows
    variable_names=[f'GT_{i+1}.electric' for i in range(3)]+[f'GT_{i+1}.on' for i in range(3)]+[
        'absorption.cooling','EC_1.cooling','EC_2.cooling','EC_3.cooling','boiler.steam','grid.import','diesel.electric','unused.steam']+[
        f'{g}.unserved.{carrier}' for g in GROUPS for carrier in CARRIERS]+[f'tracking.{name}.absolute_error' for name in targets]
    units=['kW']*3+['binary']*3+['RT']*4+['lb/h','kW','kW','lb/h']+['kW']*12+[
        'RT' if value[0]==ia else 'kW' for value in targets.values()]
    if sci_dependencies:
        from tools.sci_engineering.service import KINDS
        variable_names += [f'P{i}.{kind}.electric_fraction' for i in range(4) for kind in KINDS]
        units += ['fraction']*12
    if storage_indices is not None:
        variable_names += list(storage_names);units += ['kW']*4
    if closure is not None:
        variable_names+=['m22.boiler.on','m22.recovery_cooling','m22.starter.fully_supplied','m22.boiler.restart_supplied'];units+=['binary','kW','binary','binary']
        if bridge_debt is not None:variable_names+=['m22.UPS.bridge_withdrawal'];units+=['kWh']
        if cold_reserve_debt is not None:variable_names+=['m25.cold.reserve_shortfall'];units+=['kWh']
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
    ordered=[0,1]+(['start'] if closure is not None and max(highs[starter_on],highs[boiler_restart_on])>0. else [])+(['bridge'] if bridge_debt is not None else [])+[2,3]
    continuity_active=m23 is None and closure is not None and execution_action is not None and float(execution_action.get('boiler_steam_lbph',0.))>0.
    if continuity_active:ordered+=['boiler_continuity']
    if closure is not None and storage_indices is not None and highs[ud]>0.:
        # A bridge store may improve locked current service, but it is not
        # a free substitute for an already adequate real source's fuel.
        ordered+=['ups_current_service_only']
    if charge_active:ordered+=['charge']
    if closure is not None and highs[recovery_cold]>0.:ordered+=['recovery']
    ordered+=['cost']
    objective_hierarchy=[]
    for rank,group in enumerate(ordered):
        service_layer=isinstance(group,int)
        charging_layer=group=='charge'
        bridge_layer=group=='bridge'
        recovery_layer=group=='recovery'
        start_layer=group=='start'
        continuity_layer=group=='boiler_continuity'
        ups_service_layer=group=='ups_current_service_only'
        c=np.zeros(nvars) if service_layer or charging_layer or bridge_layer or recovery_layer or start_layer or continuity_layer or ups_service_layer else c_cost.copy()
        if ups_service_layer:c[ud]=1.
        if continuity_layer:c[boiler_on]=-1.
        if charging_layer:
            c[uc]=-1.;c[cc]=-1.
        if bridge_layer:
            c[bridge_debt]=1. if sustained_reserve is None else 1./max(1.,sustained_reserve['ups']['target_kWh'])
            if sustained_reserve is not None:c[cold_reserve_debt]=1./max(1.,sustained_reserve['cold']['target_kWh'])
        if recovery_layer:c[recovery_cold]=-1.
        if start_layer:
            c[starter_on]=-closure['gt_start_kW'];c[boiler_restart_on]=-closure['boiler_restart_kW']
        if service_layer:
            for k in range(3):c[idx(group,k)]=1/max(1.,float(loads[:,k].sum()))
        # Native backend retains explicit five layers and the unchanged locks.
        # Equivalent positive row scaling is uniform; finite numerical recovery
        # is registered separately and never grants an execution capability.
        res=solve_layer(milp,c,integrality=integral,bounds=Bounds(lows,highs),constraints=constraints,
            options={'time_limit':solver_time_limit_sec,'mip_rel_gap':1e-6,'presolve':False},rank=rank,variables=variables,
            rows=row_metadata,locks=locks,input_context=diagnostic_input,row_scaling=True)
        backend_layers.append(dict(rank=rank,layer=group,backend=getattr(res,'backend','injected'),
            numerical_version=getattr(res,'numerical_version',None),status=int(res.status),
            attempts=getattr(res,'attempts',[])))
        if res.x is None or res.status!=0:
            from hospital_ies.execution import ModelConstraintInfeasible,SolverExecutionError
            error=ModelConstraintInfeasible if res.status==2 else SolverExecutionError
            failure=error(f'v3 MILP failure: rank={group}, status={res.status}, message={res.message}')
            failure.solver_backend_attempts=backend_layers
            raise failure
        x=res.x
        if m23 is not None:
            objective_hierarchy.append((group,c.copy(),lex_lock_tolerance(group,float(c@x),config) if service_layer else 0. if ups_service_layer or group=='cost' else 1e-7))
        if sci_dependencies:
            from tools.sci_engineering.service import validate_auxiliary_kW_bounds
            try:
                backend_layers[-1]['original_auxiliary_kW_bound_validation']=validate_auxiliary_kW_bounds(
                    x,auxiliary_indices,auxiliary_required,highs)
            except Exception as failure:
                failure.solver_backend_attempts=backend_layers
                raise
        if service_layer or charging_layer or bridge_layer or recovery_layer or start_layer or continuity_layer or ups_service_layer:
            val=float(c@x)
            if service_layer or charging_layer:lex_values.append(val)
            tol=lex_lock_tolerance(group,val,config) if service_layer else 0. if ups_service_layer else 1e-7
            constraints.append(LinearConstraint(c,-np.inf,val+tol))
            if service_layer:
                # This diagnostic list is specifically nonnegative ENS locks.
                # The recharge lock remains an explicit constraint/metadata
                # row, not an ENS implication for the backend preconditioner.
                locks.append(dict(source_rank=rank,objective=c.copy(),objective_constant=0.,incumbent_raw_objective=val,
                                  tolerance=tol,upper=val+tol,relation='<=',unit='normalized carrier ENS',source='raw incumbent c@x'))
            name=('storage.recharge_after_all_service' if charging_layer else ('m22.preserve_existing_bridge_after_critical' if sustained_reserve is None else 'm25.normalized_end_inventory_shortfall_after_critical_and_restart') if bridge_layer
                else 'm22.UPS_discharge_only_for_current_service_after_all_service' if ups_service_layer
                else 'm22.held_boiler_continuity_after_all_service' if continuity_layer
                else 'm22.actual_recovery_after_all_service' if recovery_layer else 'm22.real_start_power_after_critical' if start_layer
                else f'priority.{GROUPS[group]}.normalized_ENS.upper')
            row_metadata.append(dict(name=name,unit=('kWh' if sustained_reserve is None else 'dimensionless') if bridge_layer else 'kW' if recovery_layer or start_layer or ups_service_layer else 'dimensionless',
                                     source=f'raw rank {group} objective plus original tolerance',kind='priority_lock'))
            if service_layer and (config or {}).get('_m24_exact_service_guard',False):
                # An additional recharge-witness constraint, never a relaxed
                # original service lock. It removes the amount that the
                # ordinary numerical lock would otherwise lend to charging.
                constraints.append(LinearConstraint(c,-np.inf,val))
                row_metadata.append(dict(name=f'm24.recharge_witness.P{group}.no_service_slack',unit='dimensionless',
                    source='pure no-charge witness for conservative recharge safeguard',kind='physical'))
    auxiliary_bound_validation=None
    if sci_dependencies:
        from tools.sci_engineering.service import validate_auxiliary_kW_bounds
        try:auxiliary_bound_validation=validate_auxiliary_kW_bounds(x,auxiliary_indices,auxiliary_required,highs)
        except Exception as failure:
            failure.solver_backend_attempts=backend_layers
            raise
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
    boiler_electric=0.;actual_recovery=0.;starter_electric=0.
    if closure is not None:
        from tools.model_closure_v22.physical import boiler_auxiliary_kW
        boiler_electric=boiler_auxiliary_kW(float(x[ib]),x[boiler_on],plant.steam_kWh_per_lb,closure)
        actual_recovery=float(x[recovery_cold])
        starter_electric=closure['gt_start_kW']*float(x[starter_on])
        e_actual-=boiler_electric+starter_electric+closure['boiler_restart_kW']*float(x[boiler_restart_on])+(closure['recovery_pump_kW_per_kW']+closure['recovery_fan_kW_per_kW'])*actual_recovery
        c_actual-=actual_recovery
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
    if closure is not None:
        action.update(boiler_auxiliary_kW=float(boiler_electric),gt_start_electric_kW=starter_electric,
            boiler_restart_electric_kW=closure['boiler_restart_kW']*float(x[boiler_restart_on]),
            station_auxiliary_kW=float(boiler_electric+starter_electric+closure['boiler_restart_kW']*float(x[boiler_restart_on])),
            recovery_cooling_kW=actual_recovery,
            recovery_pump_electric_kW=closure['recovery_pump_kW_per_kW']*actual_recovery,
            recovery_fan_electric_kW=closure['recovery_fan_kW_per_kW']*actual_recovery)
        action['boiler_station_running']=bool(x[boiler_on]>.5)
    # Tracking deviations are artificial controller penalties, not actual
    # gas/electricity/diesel expenditure. Keep them separate for publications.
    security_penalty=float(security_weight*dt_h*gts.sum())
    operating_cost=float(c_cost[:26]@x[:26])-security_penalty
    tracking_penalty=float(c_cost[26:26+len(targets)]@x[26:26+len(targets)])
    return {'action':action,'lost':lost,'served':served,'lex_values':lex_values,
            **({'_m23_objective_hierarchy':[dict(name=name,value=float(objective@x),lock_tolerance=tolerance) for name,objective,tolerance in objective_hierarchy]} if m23 is not None else {}),
            'cost_USD_step':operating_cost,'tracking_penalty_USD_step':tracking_penalty,
            'optimization_objective_USD_step':operating_cost+tracking_penalty+security_penalty,
            'security_objective_penalty_USD_step':security_penalty,
            'energy_balance_residuals':residuals,
            'steam_balance_residual_lbph':steam_residual_lbph,'optimizer_warning':optimizer_warning,
            'solver_backend_layers':backend_layers,
            **({'service_dependencies':dict(auxiliary_required_kW=auxiliary_required.tolist(),
                auxiliary_bound_validation=auxiliary_bound_validation,
                fan_independent_heat=fan_independent_heat_bound(row,loads),
                auxiliary_delivered_kW=(auxiliary_required*np.asarray(x[auxiliary_indices])).tolist(),
                direct_electric_delivered_kW=(served[:,0]-np.sum(auxiliary_required*x[auxiliary_indices],axis=1)).tolist(),
                semantics='aggregate air-served heat/cold need fan power; identified P3 non-air heat has a maximum fan-independent allowance, not a DHW delivery meter; all thermal delivery retains distribution/source constraints; existing electricity reallocated')}
                if sci_dependencies else {})}
