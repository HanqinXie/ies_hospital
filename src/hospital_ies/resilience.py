"""v3: research-grade *prototype* of four fault-stress stages for DOE hospital IES.

Steady energy-hub MILP, independently dispatched turbines/HRSGs, hierarchical
load-curtailment, approximate GT ramp, diesel reserve, fault schedule, and
explicit *hypothetical* delayed-response overload trip model. NO electrical
transients, true relay/ATS logic, hospital clinical certification or validated
cascading-fault probabilities. All unsupported assumptions are externalized.
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
import math
from pathlib import Path
import json
import numpy as np
import pandas as pd
from scipy.optimize import Bounds, LinearConstraint
from .solver_backend import milp
from .model import Plant, gt_level_properties, illustrative_tariff, RT_TO_KW, KWH_PER_MMBTU
from .idf_parameters import zone_parameter_table

ZONE_PRIORITY = {
    'ICU_FLR_2':0,'ER_TRAUMA1_FLR_1':0,'ER_TRAUMA2_FLR_1':0,
    'OR1_FLR_2':0,'OR2_MULT5_FLR_2':0,'OR3_FLR_2':0,'OR4_FLR_2':0,
    'ER_EXAM1_MULT4_FLR_1':1,'ER_EXAM3_MULT4_FLR_1':1,
    'ICU_NURSESTN_LOBBY_FLR_2':1,'IC_PATROOM1_MULT5_FLR_2':1,
    'IC_PATROOM2_FLR_2':1,'IC_PATROOM3_MULT6_FLR_2':1,
    'RADIOLOGY_FLR_4':2,
}
GROUPS=('P0','P1','P2','P3')
CARRIERS=('e','h','c')


@dataclass
class RuntimeState:
    gt_prev: np.ndarray
    diesel_energy_remaining_kWh: float
    extra_faulted: set[str]
    overload_minutes: np.ndarray
    thermal_delta: dict[str,float]
    last_grid_import_kW: float = 0.0
    last_diesel_kW: float = 0.0
    # P1A execution ledger, never future demand or optimizer loss by itself.
    auxiliary_request_kW: float = 0.0
    cooling_reference_kW: float | None = None
    disconnected_loads: np.ndarray = field(default_factory=lambda: np.zeros((4,3)))
    diesel_status: str = 'OFF'
    diesel_start_minute: float | None = None
    topology_mode: str = 'legacy_explicit_permission'


def build_zone_groups(zone_df:pd.DataFrame, hourly:pd.DataFrame)->pd.DataFrame:
    """Vectorized and conservative priority decomposition of v2 8760h totals.

    The zone data are proxies, not actual metered critical circuits. Values
    are never added on top of the DOE energy totals; any excess zone proxy
    is rescaled, and residual demand goes to P3.
    """
    d=zone_df.copy()
    d['zone_name']=d.zone_name.str.upper()
    names=set(d.zone_name.unique())
    if names!=set(ZONE_PRIORITY):
        raise ValueError(f'Critical-zone mapping mismatch: {set(ZONE_PRIORITY)^names}')
    d['priority']=d.zone_name.map(ZONE_PRIORITY)
    mult=d.zone_multiplier.to_numpy(dtype=float)
    d['e_raw']=(d.interior_equipment_kW+d.interior_lighting_kW).to_numpy()*mult
    d['h_raw']=d.associated_reheat_coil_heating_kW.clip(lower=0).to_numpy()*mult
    d['c_raw']=d.zone_sensible_cooling_kW.clip(lower=0).to_numpy()*mult
    grouped=d.groupby(['hour_of_year','priority'],sort=False)[['e_raw','h_raw','c_raw']].sum()
    keys=pd.MultiIndex.from_product([hourly.hour_of_year.to_numpy(),[0,1,2]],names=['hour_of_year','priority'])
    data=grouped.reindex(keys,fill_value=0).to_numpy().reshape(len(hourly),3,3)
    total=np.stack([
       (hourly.P_e_nonplant_kW+hourly.P_e_unresolved_kW+hourly.get('electric_extra_demand_kW',0)).to_numpy(),
       hourly.Q_h_demand_kW.to_numpy(),hourly.Q_c_demand_kW.to_numpy()],axis=1)
    summed=data.sum(axis=1)
    scale=np.minimum(1.,np.divide(total,summed,out=np.ones_like(summed),where=summed>1e-12))
    values=np.zeros((len(hourly),4,3))
    values[:,:3,:]=data*scale[:,None,:]
    values[:,3,:]=np.maximum(0.,total-values[:,:3,:].sum(axis=1))
    if not np.allclose(values.sum(axis=1),total,atol=1e-6):raise ValueError('Priority load decomposition not conservative')
    out=pd.DataFrame({'hour_of_year':hourly.hour_of_year.to_numpy()})
    for i,g in enumerate(GROUPS):
        for j,k in enumerate(CARRIERS):out[f'{g}_{k}_kW']=values[:,i,j]
    return out


def loads_for_step(base:dict,shock:dict[str,float],priority_shocks:dict|None=None)->np.ndarray:
    a=np.array([[float(base[f'{g}_{k}_kW']) for k in CARRIERS] for g in GROUPS])
    for j,k in enumerate(CARRIERS):
        delta=float(shock.get(k,0))
        if delta < -1 or not math.isfinite(delta):raise ValueError('Invalid shock')
        a[:,j]*=1+delta
    for g,cfg in (priority_shocks or {}).items():
        if g not in GROUPS:raise ValueError('Unknown priority '+g)
        for k,value in cfg.items():
            if k not in CARRIERS or not 0<=float(value)<=4:raise ValueError('Invalid priority shock')
            a[GROUPS.index(g),CARRIERS.index(k)]*=1+float(value)
    return a


def empty_faults()->dict[str,float]:
    out={f'GT_{i+1}':1.0 for i in range(3)}
    out.update({f'HRSG_{i+1}':1.0 for i in range(3)})
    out.update({f'EC_{i+1}':1.0 for i in range(3)})
    out.update({f'BOILER_{i+1}':1.0 for i in range(3)})
    out.update({'ABS':1.0,'TOWER':1.0,'GRID':1.0,'DIESEL':1.0})
    out.update({f'TOWER_{i+1}':1.0 for i in range(4)})
    return out


def validate_scenario(s:dict)->dict:
    s=dict(s)
    if s.get('stage') not in (1,2,3,4):raise ValueError('stage must be 1..4')
    if not 1<=int(s.get('start_hour',0))<=8760:raise ValueError('start_hour outside 8760')
    if int(s.get('duration_steps',0))<1:raise ValueError('duration_steps invalid')
    for k in ('fault_start_step','shock_start_step'):
        if not 0<=int(s.get(k,0))<int(s['duration_steps']):raise ValueError(k+' outside scenario')
    dt=float(s.get('step_minutes',15))
    if not math.isfinite(dt) or not 0<dt<=60:raise ValueError('step_minutes must be finite in (0,60]')
    for kind in ('fault','shock'):
        for part,plural in (('start','minute'),('duration','minutes')):
            key=f'{kind}_{part}_{plural}'
            if key in s:
                if f'{kind}_{part}_step'+('s' if part=='duration' else '') in s:
                    raise ValueError('Ambiguous minute and step event fields: '+key)
                value=float(s[key])
                if not math.isfinite(value) or value<0 or (part=='start' and value>=int(s['duration_steps'])*dt):
                    raise ValueError('Invalid event time: '+key)
    if 'response_delay_minutes' in s and 'response_delay_steps' in s:raise ValueError('Ambiguous response delay')
    for key in ('response_delay_minutes','response_delay_steps'):
        if key in s and (not math.isfinite(float(s[key])) or float(s[key])<0):raise ValueError('Invalid response delay')
    if s['stage'] in (1,3) and (s.get('shock') or s.get('shock_by_priority')):raise ValueError('Load shocks reserved for L2/L4')
    # P2A schema adapter only: L4b composes the EXISTING independent fault
    # and shock clocks. Unversioned L4 retains its demand-only meaning.
    event_class=s.get('event_class',f"L{s['stage']}")
    if event_class not in ({1:('L1',),2:('L2',),3:('L3',),4:('L4','L4a','L4b')}[s['stage']]):
        raise ValueError('event_class disagrees with stage')
    if event_class in ('L4a','L4b') and s.get('event_schema_version')!='p2a.events.v1':
        raise ValueError('L4a/L4b require p2a.events.v1')
    if event_class=='L4b' and (not s.get('faults') or not (s.get('shock') or s.get('shock_by_priority'))):
        raise ValueError('L4b requires exogenous fault and demand shock')
    if s['stage'] in (2,4) and s.get('faults') and event_class!='L4b':
        raise ValueError('Initially faulted equipment reserved for L1/L3 or versioned L4b')
    if s['stage'] in (3,4) and not s.get('enable_propagation',False):raise ValueError('L3/L4 require enable_propagation')
    if s['stage'] in (1,2) and s.get('enable_propagation',False):raise ValueError('L1/L2 disable propagation')
    known=set(empty_faults())
    for key,v in s.get('faults',{}).items():
        if key not in known or not 0<=float(v)<=1:raise ValueError(f'Invalid device availability: {key}')
    for k,v in s.get('shock',{}).items():
        if k not in CARRIERS or not 0<=float(v)<=3:raise ValueError(f'Invalid shock: {k}')
    for g,shocks in s.get('shock_by_priority',{}).items():
        if g not in GROUPS or not isinstance(shocks,dict):raise ValueError('Invalid priority shock group')
        for k,v in shocks.items():
            if k not in CARRIERS or not 0<=float(v)<=4:raise ValueError('Invalid priority shock')
    return s


def scenario_faults(s:dict,step:int,state:RuntimeState)->dict[str,float]:
    f=empty_faults()
    dt=float(s.get('step_minutes',15));minute=float(step)*dt
    fault_start=float(s.get('fault_start_minute',float(s.get('fault_start_step',0))*dt))
    duration=float(s.get('fault_duration_minutes',float(s.get('fault_duration_steps',s['duration_steps']))*dt))
    if fault_start<=minute<fault_start+duration:
        for key,value in s.get('faults',{}).items():f[key]=float(value)
    for name in state.extra_faulted:f[name]=0.0
    return f


def lex_lock_tolerance(priority:int,objective_value:float,config:dict|None=None)->float:
    """Absolute/relative lock width; P0-P2 defaults preserve the old lock."""
    cfg=config or {}
    abs_tols=cfg.get('lex_lock_abs_tol_by_priority',[1e-7,1e-7,1e-7,1e-5])
    rel_tols=cfg.get('lex_lock_rel_tol_by_priority',[0.,0.,0.,1e-8])
    if len(abs_tols)!=4 or len(rel_tols)!=4:
        raise ValueError('Lexicographic lock tolerances must have four priority values')
    a=float(abs_tols[priority]);r=float(rel_tols[priority])
    if a<0 or r<0 or not math.isfinite(a+r):raise ValueError('Invalid lexicographic lock tolerance')
    return max(a,r*abs(float(objective_value)))


def p0_failure_classification(loss:np.ndarray,loads:np.ndarray,temp_violation:bool,
                               config:dict,dt_h:float,
                               cumulative_energy_kWh:dict|None=None)->dict:
    """Separate raw P0 energy loss, temperature, and final service failure.

    P0 cooling interruption is reported as energy loss, but is a final
    guarantee failure only if the thermal proxy crosses its limit. Electric
    and heating interruption are final failures directly. Raw solver outputs
    are returned unchanged; feasibility tolerance only affects classification.
    """
    solver_tol=float(config.get('p0_solver_feasibility_tol_kW',1e-3))
    abs_kw=float(config.get('p0_unserved_abs_tol_kW',0.01))
    rel=float(config.get('p0_unserved_rel_tol',0.001))
    abs_kwh=float(config.get('p0_unserved_abs_tol_kWh',0.01))
    if min(solver_tol,abs_kw,rel,abs_kwh)<0:raise ValueError('P0 thresholds must be nonnegative')
    prior=cumulative_energy_kWh or {}
    names=('e','h','c');raw={k:float(loss[0,i]) for i,k in enumerate(names)}
    material={};cumulative={}
    for i,k in enumerate(names):
        demand=max(0.,float(loads[0,i]));short=max(0.,raw[k])
        rel_short=short/demand if demand>solver_tol else 0.
        material[k]=(short>solver_tol and (short>=abs_kw or rel_short>=rel))
        effective=short if short>solver_tol else 0.
        cumulative[k]=float(prior.get(k,0.))+effective*dt_h
    energy={k:bool(material[k] or
                   (float(prior.get(k,0.))<abs_kwh and cumulative[k]>=abs_kwh))
            for k in names}
    energy_loss=any(energy.values())
    temperature=bool(temp_violation)
    guarantee=bool(energy['e'] or energy['h'] or temperature)
    return {'p0_energy_loss':energy_loss,'p0_temperature_violation':temperature,
            'p0_guarantee_failure':guarantee,'material_unserved_by_carrier':energy,
            'cumulative_effective_energy_kWh':cumulative,
            'raw_P0_e_unserved_kW':raw['e'],'raw_P0_h_unserved_kW':raw['h'],
            'raw_P0_c_unserved_kW':raw['c']}


def _solve_milp(row,plant:Plant,loads:np.ndarray,availability:dict[str,float],state:RuntimeState,
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
    nvars=26+len(targets)
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
    for i in range(3):
        highs[i]=rated*availability[f'GT_{i+1}']
        highs[3+i]=1 if availability[f'GT_{i+1}']>0 else 0
    highs[ia]=plant.absorption_rt*availability['ABS']
    highs[ie]=np.array(plant.electric_chillers_rt)*np.array([availability[f'EC_{i+1}'] for i in range(3)])
    highs[ib]=plant.boiler_steam_lbph/3*sum(availability[f'BOILER_{i+1}'] for i in range(3))
    highs[ig]=grid_import_limit_kW*availability['GRID']
    highs[idg]=min(plant.emergency_diesel_kW*availability['DIESEL'],state.diesel_energy_remaining_kWh/dt_h) if diesel_start_available else 0.0
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
        highs[ia]=min(highs[ia],execution_action['absorption_cooling_kW']/RT_TO_KW)
        for i,index in enumerate(ie):
            highs[index]=min(highs[index],execution_action['electric_chiller_cooling_kW'][i]/RT_TO_KW)
    for i in range(4):
        for j in range(3):highs[idx(i,j)]=max(0,loads[i,j])
    integral=np.zeros(nvars,dtype=int);integral[go]=1
    constraints=[LinearConstraint(Aeq,beq,beq),LinearConstraint(np.asarray(A),np.asarray(low),np.asarray(high))]
    # Opt-in snapshots preserve exact solver arrays before any clipping. The
    # collector is disabled during ordinary execution and changes no argument.
    from .solver_diagnostics import active_collector,solve_layer
    row_metadata=[dict(name=f'balance.{carrier}',unit=unit,source='energy hub balance',kind='physical')
                  for carrier,unit in (('electric','kW'),('steam','lb/h'),('cooling','kW'))]+cap_rows
    variable_names=[f'GT_{i+1}.electric' for i in range(3)]+[f'GT_{i+1}.on' for i in range(3)]+[
        'absorption.cooling','EC_1.cooling','EC_2.cooling','EC_3.cooling','boiler.steam','grid.import','diesel.electric','unused.steam']+[
        f'{g}.unserved.{carrier}' for g in GROUPS for carrier in CARRIERS]+[f'tracking.{name}.absolute_error' for name in targets]
    units=['kW']*3+['binary']*3+['RT']*4+['lb/h','kW','kW','lb/h']+['kW']*12+[
        'RT' if value[0]==ia else 'kW' for value in targets.values()]
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
    for group in range(5):
        c=np.zeros(nvars) if group<4 else c_cost.copy()
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
            from .execution import ModelConstraintInfeasible,SolverExecutionError
            error=ModelConstraintInfeasible if res.status==2 else SolverExecutionError
            failure=error(f'v3 MILP failure: rank={group}, status={res.status}, message={res.message}')
            failure.solver_backend_attempts=backend_layers
            raise failure
        x=res.x
        if group<4:
            val=float(c@x);lex_values.append(val)
            tol=lex_lock_tolerance(group,val,config)
            constraints.append(LinearConstraint(c,-np.inf,val+tol))
            locks.append(dict(source_rank=group,objective=c.copy(),objective_constant=0.,incumbent_raw_objective=val,
                              tolerance=tol,upper=val+tol,relation='<=',unit='normalized carrier ENS',source='raw incumbent c@x'))
            row_metadata.append(dict(name=f'priority.{GROUPS[group]}.normalized_ENS.upper',unit='dimensionless',
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
    # Tracking deviations are artificial controller penalties, not actual
    # gas/electricity/diesel expenditure. Keep them separate for publications.
    security_penalty=float(security_weight*dt_h*gts.sum())
    operating_cost=float(c_cost[:26]@x[:26])-security_penalty
    tracking_penalty=float(c_cost[26:]@x[26:])
    return {'action':action,'lost':lost,'served':served,'lex_values':lex_values,
            'cost_USD_step':operating_cost,'tracking_penalty_USD_step':tracking_penalty,
            'optimization_objective_USD_step':operating_cost+tracking_penalty+security_penalty,
            'security_objective_penalty_USD_step':security_penalty,
            'energy_balance_residuals':residuals,
            'steam_balance_residual_lbph':steam_residual_lbph,'optimizer_warning':optimizer_warning,
            'solver_backend_layers':backend_layers}


def thermal_data(root:Path,zones:pd.DataFrame,mass_fraction:float=0.5,effective_U:float=1.5):
    table=zone_parameter_table(root/'inputs/energyplus/Hospital_SanDiego.idf',mass_fraction,effective_U)
    params={r['zone_name']:r for r in table if r['zone_name'] in ZONE_PRIORITY}
    if set(params)!=set(ZONE_PRIORITY):raise ValueError('Incomplete IDF critical zones')
    by_hour={}
    for h,df in zones.groupby('hour_of_year',sort=False):
        by_hour[int(h)]={str(r.zone_name).upper():r for r in df.itertuples(index=False)}
    return params,by_hour


def update_thermal(state:RuntimeState,params:dict,hour_zones:dict,loss:np.ndarray,loads:np.ndarray,dt_h:float,margin_C:float,attribution=None):
    max_temp_by_priority={g:-np.inf for g in GROUPS}
    violation_by_priority={g:False for g in GROUPS}
    for name,p in params.items():
        g=ZONE_PRIORITY[name];r=hour_zones[name];q_need=loads[g,2]
        fraction=float(loss[g,2]/q_need) if q_need>1e-9 else 0.
        shock=fraction*max(0.,float(r.zone_sensible_cooling_kW))  # representative zone, no multiplier
        if attribution is not None:
            # Equal fractional service allocation is explicit. Process demand
            # contributes NO zone heat; sensible W -> kW is already caller's unit.
            shock=fraction*(max(0.,float(r.zone_sensible_cooling_kW))+attribution['sensible_kW'].get(name,0.))
        ua=p['assumed_UA_kW_K'];C=p['effective_C_kWh_K']
        decay=math.exp(-ua/C*dt_h)
        state.thermal_delta[name]=state.thermal_delta.get(name,0)*decay+(-math.expm1(-ua/C*dt_h))*shock/ua
        T=float(r.zone_mean_air_temperature_C)+state.thermal_delta[name]
        target=float(r.thermostat_cooling_setpoint_C)+margin_C
        key=GROUPS[g]
        max_temp_by_priority[key]=max(max_temp_by_priority[key],T)
        if T>target+1e-7:violation_by_priority[key]=True
    return max_temp_by_priority,violation_by_priority


def pre_control_cascade(state:RuntimeState,availability:dict[str,float],loads:np.ndarray,
                        plant:Plant, config:dict,dt_minutes:int,controlled:bool,
                        grid_import_limit_kW:float|None=None,
                        diesel_response_kW:float=0.,apply_trips:bool=True):
    """Uncalibrated pre-control proxy with finite support and trip persistence.

    The response budget counts incremental grid headroom, diesel response passed
    by the caller, and modeled P3/P2/P1 shedding before distributing any
    remaining transient deficit across online GTs. Nameplate output, temporary
    overload allowance, and trip threshold are separate parameters. This is not
    a network, frequency, relay, or manufacturer-protection simulation.
    """
    short_limit=float(config.get('gt_short_term_overload_multiple',config.get('gt_transient_trip_multiple',1.05)))
    threshold=float(config.get('gt_protection_trip_multiple',config.get('gt_transient_trip_multiple',1.05)))
    hold=float(config['gt_overload_hold_minutes'])
    running=[i for i in range(3) if availability[f'GT_{i+1}']>0 and state.gt_prev[i]>1e-4]
    surviving=float(sum(state.gt_prev[i] for i in running))
    # Event deficit is reduced by immediate grid headroom, available diesel,
    # and explicitly executable lower-priority load shedding before it is
    # assigned to surviving GTs.
    # Cooling surges can increase electrical demand via EC; the fraction is
    # an UNVALIDATED sensitivity parameter, not a hydraulic plant model.
    reference=(state.cooling_reference_kW if state.cooling_reference_kW is not None
               else float(config.get('pre_event_cooling_demand_kW',loads[:,2].sum())))
    cold_growth=max(0.0,float(loads[:,2].sum())-reference)
    cold_proxy=cold_growth*(plant.ec_electric_kW_per_rt/RT_TO_KW)*float(config['precontrol_cooling_to_el_fraction'])
    grid_cap=float(config.get('grid_import_limit_kW',0.) if grid_import_limit_kW is None else grid_import_limit_kW)
    grid_available_cap=grid_cap*float(availability['GRID'])
    lost_grid_support=max(0.,state.last_grid_import_kW-grid_available_cap)
    grid_headroom=max(0.,grid_available_cap-state.last_grid_import_kW)
    diesel_available_cap=max(0.,float(diesel_response_kW))
    lost_diesel_support=max(0.,state.last_diesel_kW-diesel_available_cap)
    diesel_headroom=max(0.,diesel_available_cap-state.last_diesel_kW)
    shed_kW=0.
    if bool(config.get('precontrol_load_shed_enabled',True)):
        ec_ratio=(plant.ec_electric_kW_per_rt/RT_TO_KW)*float(config['precontrol_cooling_to_el_fraction'])
        shares=config.get('precontrol_sheddable_fraction_by_priority',{'P3':1.,'P2':1.,'P1':1.})
        for priority in (3,2,1):
            share=float(shares.get(GROUPS[priority],0.))
            if not 0.<=share<=1.:raise ValueError('Invalid controllable shedding share')
            shed_kW+=share*(max(0.,float(loads[priority,0]))+max(0.,float(loads[priority,2]))*ec_ratio)
    # Reconcile connected demand with executed supply. Fixed pre-event demand
    # is not added again to an output that has already responded to the event.
    gross_deficit=max(0.,float(loads[:,0].sum())+state.auxiliary_request_kW+cold_proxy
        -surviving-min(state.last_grid_import_kW,grid_available_cap)
        -min(state.last_diesel_kW,diesel_available_cap))
    grid_used=min(gross_deficit,grid_headroom)
    diesel_used=min(max(0.,gross_deficit-grid_used),diesel_headroom)
    shed_used=min(max(0.,gross_deficit-grid_used-diesel_used),shed_kW)
    residual=max(0.,gross_deficit-grid_used-diesel_used-shed_used)
    request=residual/max(1,len(running))
    stressed={};new_trips=[]
    protection_exceeded={};short_term_exceeded={}
    for i in range(3):
        if i not in running:
            state.overload_minutes[i]=0
            protection_exceeded[f'GT_{i+1}_protection_threshold_exceeded']=False
            short_term_exceeded[f'GT_{i+1}_short_term_allowance_exceeded']=False
            continue
        requested=state.gt_prev[i]+request
        ratio=requested/plant.gt_rated_kW
        stressed[f'GT_{i+1}']=ratio
        protection_exceeded[f'GT_{i+1}_protection_threshold_exceeded']=bool(ratio>threshold+1e-9)
        short_term_exceeded[f'GT_{i+1}_short_term_allowance_exceeded']=bool(ratio>short_limit+1e-9)
        if ratio>threshold+1e-9:
            state.overload_minutes[i]+=dt_minutes
            if state.overload_minutes[i]>=hold-1e-9:
                new_trips.append(f'GT_{i+1}')
        else:state.overload_minutes[i]=0
    if apply_trips:
        for k in new_trips:state.extra_faulted.add(k)
    response={'precontrol_grid_support_available_kW':grid_headroom,
              'precontrol_grid_support_kW':grid_used,
              'precontrol_lost_grid_support_kW':lost_grid_support,
              'precontrol_diesel_support_available_kW':diesel_headroom,
              'precontrol_diesel_support_kW':diesel_used,
              'precontrol_lost_diesel_support_kW':lost_diesel_support,
              'precontrol_load_shed_available_kW':shed_kW,
              'precontrol_load_shed_kW':shed_used,
              'precontrol_GT_transfer_kW':residual}
    response.update(protection_exceeded);response.update(short_term_exceeded)
    return new_trips,stressed,response


def simulate_scenario(root:Path,s:dict,plant:Plant,hourly:pd.DataFrame,group_loads:pd.DataFrame,
                      zones:pd.DataFrame,config:dict,thermal_cache=None,execution_contract=None)->tuple[pd.DataFrame,pd.DataFrame,dict]:
    """Legacy table interface over the SAME P1A environment/event kernel.

    Columns retain interval-average kW semantics; event and segment traces are
    attached to the summary. No pre-dispatch trip mutation exists in this path.
    """
    from .control_env import HospitalIESEnv
    env=HospitalIESEnv(root,config=config,thermal_cache=thermal_cache,execution_contract=execution_contract)
    env.plant=plant;env.hourly=hourly;env.groups=group_loads.set_index('hour_of_year')
    env.zones=zones
    env.reset(scenario=s)
    for _ in range(int(s['duration_steps'])):env.step({})
    trace,details=env.traces()
    trace['policy']=s.get('policy','safe')
    trace['temp_P0_limit_violation']=trace.p0_temperature_violation
    details['fault_zone_temp_C']=details.temperature_C
    for i in (1,2,3):
        col=f'precontrol_GT_{i}_estimated_overload_ratio'
        trace[f'GT_{i}_overload_ratio_estimate']=trace[col] if col in trace else 0.
    first=lambda column: float(trace.loc[trace[column]>0,'minute'].iloc[0]) if trace[column].any() else None
    balances=['balance_electric_residual_kW','balance_cooling_residual_kW','balance_steam_equivalent_residual_kW']
    summary=dict(id=str(s.get('id','unnamed')),stage=int(s['stage']),policy=s.get('policy','safe'),
        duration_h=len(trace)*env.dt_h,p0_failure_any=bool(trace.p0_failed.any()),
        p0_energy_loss_any=bool(trace.p0_energy_loss.any()),p0_temperature_violation_any=bool(trace.p0_temperature_violation.any()),
        p0_guarantee_failure_any=bool(trace.p0_guarantee_failure.any()),
        first_p0_failure_minute=first('p0_failed'),first_p0_energy_loss_minute=first('p0_energy_loss'),
        first_p0_temperature_violation_minute=first('p0_temperature_violation'),
        first_failure_time_semantics='start of affected reporting interval; inspect segment trace',
        secondary_fault_count=len(env.state.extra_faulted),secondary_faults=sorted(env.state.extra_faulted),
        max_P0_delta_temp_C=float(details.loc[details.priority==0,'delta_temp_C'].max()),
        energy_balance_verified=bool(trace[balances].abs().to_numpy().max()<=1e-4 and trace.balance_steam_residual_lbph.abs().max()<=1e-4),
        max_abs_energy_balance_residual_kW=float(trace[balances].abs().to_numpy().max()),
        max_abs_steam_balance_residual_lbph=float(trace.balance_steam_residual_lbph.abs().max()),
        cost_USD_total=float(trace.cost_USD_step.sum()),trip_events=env.trip_events,
        event_trace=env.event_history,segment_trace=env.segment_history,solver_errors=env.solver_errors)
    for g in GROUPS:
        for k in CARRIERS:summary[f'{g}_{k}_ENS_kWh']=float(trace[f'{g}_{k}_unserved_kW'].sum()*env.dt_h)
    for k,name in zip(CARRIERS,('electric','heating','cooling')):
        summary[f'P0_effective_{name}_ENS_kWh']=env.cumulative_p0[k]
    return trace,details,summary
