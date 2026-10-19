"""P2A observable views, units, masks and family grouping; no physics mutation.

All internal device/protection/thermal observations are explicitly idealized.
Full event scripts are intentionally absent from decision_snapshot().
"""
from __future__ import annotations
import copy
import math
import numpy as np
from .dataset_manifest import digest
from .model import gt_level_properties,RT_TO_KW
from .resilience import GROUPS,CARRIERS,ZONE_PRIORITY
from .state_transition import clean

SCHEMA_VERSIONS=dict(feature='p2a.observation.v1',label='p2a.first_observed_failure.v1',
    legacy_label='v42.future_t_plus_1_to_H',action='p2a.six_targets.v1',
    graph='p2a.functional_dependencies.v1',scenario='p2a.scenario.v1',
    split='p2a.family_components.v1',event='p2a.events.v1')
ACTION_SCHEMA={k:dict(unit='kW',meaning='requested setpoint, subject to existing MILP projection and execution queue',
                    supported=True) for k in ('gt_1','gt_2','gt_3','grid_import','diesel','absorption_cooling_kW')}

def validate_action(action):
    if not isinstance(action,dict) or set(action)-{'targets','security'}:raise ValueError('Unsupported action mapping')
    targets=action.get('targets',{});security=action.get('security',{})
    if not isinstance(targets,dict) or set(targets)-set(ACTION_SCHEMA):raise ValueError('Unsupported target key')
    if any(not math.isfinite(float(v)) or float(v)<0 for v in targets.values()):raise ValueError('Invalid target value')
    if not isinstance(security,dict) or set(security)-{'gt_loading_penalty_USD_per_kWh'}:raise ValueError('Unsupported security parameter')
    if any(not math.isfinite(float(v)) or not 0<=float(v)<=10 for v in security.values()):raise ValueError('Invalid security weight')
    return copy.deepcopy(action)

def first_failure_label(t,horizon,first,observed_until,status):
    """First OBSERVED frozen-classifier failure in (t,t+H], never row offsets.

    Positive events prove a positive window even if a later error truncates it.
    Negative windows need a complete contiguous observation prefix.
    """
    if horizon<=0 or t<0 or observed_until<0:raise ValueError('Invalid label times')
    if first is not None and (first<0 or first>observed_until+1e-9):raise ValueError('Unobserved first failure')
    at_risk=first is None or first>t
    full=observed_until>=t+horizon-1e-9
    positive=at_risk and first is not None and first<=t+horizon
    valid=at_risk and (positive or full)
    reason=(None if valid else 'post_first_failure' if not at_risk else
        {'completed':'administrative_end','model_constraint_infeasible':'model_error',
         'solver_error':'solver_error','unsupported_model_regime':'unsupported_model_regime',
         'year_end':'year_end'}.get(status,'unknown_termination'))
    return dict(value=int(positive) if valid else None,valid=bool(valid),at_risk=bool(at_risk),
        full_horizon=bool(full),observed_window_minutes=max(0.,min(horizon,observed_until-t)),
        unknown_reason=reason,administrative_censoring=reason in ('administrative_end','year_end'),
        model_error_truncated=status in ('model_constraint_infeasible','solver_error','unsupported_model_regime'),
        first_failure_observed_minute=first,window_left_open=True,window_right_closed=True)

def family_splits(rows,seed=20261013,embargo_minutes=60):
    """Connected components of lineage AND overlapping raw hourly load windows.

    Policies, neighboring variants and model versions preserve lineage IDs.
    A stress member keeps its ENTIRE connected component out of training.
    Development rows cannot be laundered into a blind test by re-splitting.
    """
    if embargo_minutes<0:raise ValueError('Negative embargo')
    by_id={}
    for row in rows:
        key=row['scenario_id']
        if key in by_id and by_id[key]!=row:raise ValueError('Inconsistent scenario lineage')
        by_id[key]=row
    items=[by_id[k] for k in sorted(by_id)];parent=list(range(len(items)))
    def find(i):
        while parent[i]!=i:
            parent[i]=parent[parent[i]];i=parent[i]
        return i
    def union(i,j):
        a,b=find(i),find(j)
        if a!=b:parent[max(a,b)]=min(a,b)
    for i,a in enumerate(items):
        for j,b in enumerate(items[:i]):
            related=any(a.get(k) is not None and a.get(k)==b.get(k)
                        for k in ('family_id','parent_template_id','base_state_id','exogenous_stream_id'))
            overlap=('load_start_minute' in a and 'load_start_minute' in b and
                a['load_start_minute']<b['load_end_minute']+embargo_minutes and
                b['load_start_minute']<a['load_end_minute']+embargo_minutes)
            if related or overlap:union(i,j)
    groups={}
    for i,a in enumerate(items):groups.setdefault(find(i),[]).append(a)
    result={}
    for group in groups.values():
        keys=sorted(x['scenario_id'] for x in group);gid='family_component:'+digest(keys)
        if any(x.get('sampling_cohort')=='stress_ood' for x in group):split='stress_ood'
        elif any(x.get('purpose')=='development' for x in group):split='development'
        else:
            u=int(digest([seed,gid])[:8],16)/2**32
            split='train' if u<.7 else 'validation' if u<.85 else 'test'
        for x in group:result[x['scenario_id']]=dict(group_id=gid,split=split,
            fit_allowed=split=='train',embargo_minutes=embargo_minutes)
    return result

def require_single_contract(rows):
    contracts={r['execution_contract_id'] for r in rows}
    if len(contracts)>1:raise ValueError('Mixed execution contracts require separate evaluation groups')
    return next(iter(contracts),None)

def validate_statistical_edges(edges):
    for edge in edges:
        if edge.get('edge_type')!='statistical':raise ValueError('Statistical layer cannot masquerade as physical')
        if edge.get('fit_split')!='train' or not edge.get('fit_dataset_id'):raise ValueError('Statistical edges need a train-only fitted dataset provenance')

def graph_view(env,obs):
    """Known aggregate energy conversion dependencies, not a pipe/AC network."""
    p=env.plant;f=obs['availability'];t=float(obs['time_minute'])
    pg,sg,_=gt_level_properties(p,obs['outdoor_temperature_C'])
    nodes=[]
    for dev,av in f.items():
        cap=None;unit='kW';output=None;state='AVAILABLE' if av>0 else 'FAILED'
        if dev.startswith('GT_'):
            i=int(dev[-1])-1;cap=float(pg[-1])*av;output=float(obs['gt_prev_kW'][i]);state=obs['gt_status'][i]
        elif dev.startswith('HRSG_'):
            i=int(dev[-1])-1;cap=float(sg[-1])*av;unit='lb/h'
            output=float(obs['gt_prev_kW'][i])*float(sg[-1]/pg[-1])*av
        elif dev.startswith('BOILER_'):cap=p.boiler_steam_lbph/3*av;unit='lb/h'
        elif dev.startswith('EC_'):cap=p.electric_chillers_rt[int(dev[-1])-1]*RT_TO_KW*av
        elif dev=='ABS':cap=p.absorption_rt*RT_TO_KW*av
        elif dev=='GRID':cap=obs['grid_import_limit_kW']*av;output=obs['grid_import_prev_kW']
        elif dev=='DIESEL':cap=p.emergency_diesel_kW*av;output=env.state.last_diesel_kW;state=env.state.diesel_status
        elif dev=='TOWER':cap=p.cooling_tower_rejection_kW*av*sum(f[f'TOWER_{i}'] for i in (1,2,3,4))/4
        elif dev.startswith('TOWER_'):cap=p.cooling_tower_rejection_kW/4*av*f['TOWER']
        nodes.append(dict(id=dev,availability=float(av),effective_capacity=cap,capacity_unit=unit,
            prior_actual_output=output,output_unit=unit,device_state=state,available_time_min=t,
            measurement='ideal_observation_assumption',source='P1B algebraic constraints and current RuntimeState'))
    for hub in ('electric_service','steam_service','cooling_service'):
        nodes.append(dict(id=hub,node_type='aggregate_service',available_time_min=t))
    edges=[]
    def edge(a,b,meaning):edges.append(dict(source=a,target=b,edge_type='functional_dependency',
        meaning=meaning,provenance='resilience._solve_milp',available_time_min=t,fit_dataset_id=None))
    for i in (1,2,3):
        edge(f'GT_{i}','electric_service','generation');edge(f'GT_{i}',f'HRSG_{i}','exhaust heat')
        edge(f'HRSG_{i}','steam_service','steam conversion');edge(f'BOILER_{i}','steam_service','steam generation')
        edge('electric_service',f'EC_{i}','auxiliary electricity');edge(f'EC_{i}','cooling_service','cooling')
        edge('TOWER',f'EC_{i}','shared rejection capacity')
    edge('steam_service','ABS','steam input');edge('electric_service','ABS','auxiliary electricity')
    edge('ABS','cooling_service','cooling');edge('TOWER','ABS','shared rejection capacity')
    for i in (1,2,3,4):edge(f'TOWER_{i}','TOWER','quarter-bank availability, common-mode multiplier')
    edge('GRID','electric_service','nonnegative import under frozen port contract')
    if env.state.diesel_status=='CONNECTED':
        edge('DIESEL','electric_service','current permission: '+env.state.topology_mode)
        edges[-1]['allowed_priorities']=(env.execution_contract['diesel_allowed_priorities']
            if env.state.topology_mode=='emergency_bus' else list(GROUPS))
    return dict(version=SCHEMA_VERSIONS['graph'],graph_kind='aggregate_functional_dependency_not_power_flow',
        nodes=nodes,edges=edges,statistical_edges=[])

def decision_snapshot(env):
    """Read-only; caller must process current boundary before invoking."""
    obs=env.core.observe();t=float(obs['time_minute']);state=env.state
    features={};catalog={}
    def feature(key,value,unit,source,measurement='ideal_observation_assumption',valid_time=None):
        features[key]=value
        catalog[key]=dict(unit=unit,source=source,available_time_min=t,
            valid_time_min=t if valid_time is None else valid_time,measurement=measurement)
    for k in ('time_minute','grid_outage_elapsed_minutes'):feature(k,float(obs[k]),'min','current state clock','observed')
    feature('hour_of_year',obs['hour_of_year'],'hour','current calendar','observed')
    feature('outdoor_temperature_C',obs['outdoor_temperature_C'],'degC','hourly boundary','observed')
    for k in ('grid_import_limit_kW','arrived_grid_import_limit_kW','grid_import_prev_kW'):
        feature(k,float(obs[k]),'kW','arrived setting / actual previous port')
    feature('diesel_prev_kW',float(state.last_diesel_kW),'kW','RuntimeState.last_diesel_kW')
    feature('diesel_energy_kWh',float(state.diesel_energy_remaining_kWh),'kWh','RuntimeState remaining budget')
    feature('diesel_status',state.diesel_status,'category','RuntimeState.diesel_status')
    feature('topology_mode',state.topology_mode,'category','execution contract and current connection')
    feature('control_available',bool(obs['control_available']),'boolean','current response window status')
    graph=graph_view(env,obs)
    for node in graph['nodes']:
        if 'availability' not in node:continue
        dev=node['id']
        feature('availability_'+dev,node['availability'],'fraction','current device availability')
        feature('capacity_'+dev,node['effective_capacity'],node['capacity_unit'],'frozen physical constraint upper bound')
        feature('state_'+dev,node['device_state'],'category','current device/permission state')
    for i in range(3):
        feature(f'pre_GT_{i+1}_kW',float(state.gt_prev[i]),'kW','actual previous GT port')
        feature(f'overload_minutes_GT_{i+1}',float(state.overload_minutes[i]),'min','residual-stress protection accumulator')
    for gi,g in enumerate(GROUPS):
        for ki,k in enumerate(CARRIERS):
            feature(f'{g}_{k}_demand_kW',float(obs['loads_kW'][gi,ki]),'kW','current connected + disconnected service demand')
            feature(f'{g}_{k}_disconnected_kW',float(state.disconnected_loads[gi,ki]),'kW','actual previous disconnected loads')
    _,byhour=env.thermal_cache
    for zone,row in byhour[obs['hour_of_year']].items():
        delta=float(state.thermal_delta.get(zone,0.))
        feature('zone_delta_'+zone,delta,'degC','P1B thermal internal state')
        margin=float(row.thermostat_cooling_setpoint_C)+float(env.config['thermal_setpoint_margin_C'])-float(row.zone_mean_air_temperature_C)-delta
        feature('zone_margin_'+zone,margin,'degC','setpoint + frozen margin - observed baseline - model delta')
    # These plans were generated by this controller in the past. Actual future
    # arrival is NOT asserted here; it appears only in ex-post event records.
    queue=[dict(command_id=c['id'],generated_minute=c['generated_minute'],
                planned_arrival_minute=c['arrival_minute'],request=copy.deepcopy(c['request']),
                permitted_dispatch=copy.deepcopy(c['dispatch'])) for c in env.core.pending_commands
           if c['generated_minute']<=t]
    return clean(dict(version=SCHEMA_VERSIONS['feature'],decision_time_min=t,observation_time_min=t,
        history_start_min=0.,history_end_min=t,features=features,feature_metadata=catalog,
        known_command_queue=queue,arrived_command_id=env.core.active_command['id'] if env.core.active_command else None,
        graph=graph))

def attach_labels(decisions,segments,status,observed_until,horizons=(15,30,60)):
    failed=[s for s in segments if s['classification']['p0_guarantee_failure']]
    first=failed[0]['end_minute'] if failed else None
    first_interval=[failed[0]['start_minute'],failed[0]['end_minute']] if failed else None
    for row in decisions:
        t=row['decision_time_min']
        row['labels']={str(h):first_failure_label(t,h,first,observed_until,status) for h in horizons}
        row['first_failure_observation_interval']=first_interval
        previous=[s for s in segments if s['end_minute']<=t+1e-9]
        row['failure_already_observed']=first is not None and first<=t
        row['recovered_at_decision']=(first is not None and first<=t and bool(previous)
            and not previous[-1]['classification']['p0_guarantee_failure'])
        row['outcome_mask']=bool(row.get('post_outcome') is not None)
    return first,first_interval
