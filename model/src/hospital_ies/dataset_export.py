"""Finite, failure-preserving dataset generation over the frozen P1B simulator.

This module exports interfaces, not trained algorithms. Checkpoints are per
attempted trajectory, including errors and valid partial subintervals.
"""
from __future__ import annotations
import copy
from dataclasses import asdict
import json
import math
from pathlib import Path
import time
import traceback
import numpy as np
import pandas as pd
from .control_env import HospitalIESEnv
from .execution import execution_contract as resolve_contract,ModelConstraintInfeasible
from .data_contract import (SCHEMA_VERSIONS,ACTION_SCHEMA,decision_snapshot,attach_labels,
                            family_splits,validate_action)
from .dataset_manifest import (digest,build_manifest,open_manifest,checkpoint_path,
                               save_checkpoint,load_checkpoint,verify_execution_source)
from .state_transition import clean,event_window
from .training_schema import build_features,build_decision_features
from .resilience import GROUPS,CARRIERS
from .method_registry import registry

POLICIES=('priority_milp','secure_milp')

def json_clean(v):
    v=clean(v)
    if isinstance(v,dict):return {k:json_clean(x) for k,x in v.items()}
    if isinstance(v,list):return [json_clean(x) for x in v]
    if isinstance(v,float) and not math.isfinite(v):return None
    return v

def write_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(json_clean(value),indent=2,ensure_ascii=False,allow_nan=False),encoding='utf-8')

def normalize_scenario(s,seed):
    s=copy.deepcopy(s)
    s.setdefault('family_id',s.get('design_family',s['id']))
    s.setdefault('parent_template_id',s['family_id'])
    s.setdefault('design_family',s['family_id'])
    s.setdefault('event_class',f"L{s['stage']}")
    s.setdefault('sampling_method','fixed_development_regression')
    s.setdefault('distribution_id','fixed_development_no_probability')
    s.setdefault('sampling_cohort','main')
    s.setdefault('event_schema_version','legacy.events.v1')
    s.setdefault('base_state_id','initial:'+digest([s['start_hour'],s.get('pre_event_warmup_steps',0),
        s.get('pre_event_grid_limit_kW',s.get('grid_limit_kW'))]))
    s.setdefault('exogenous_stream_id','fixed:'+digest([seed,{k:v for k,v in s.items()
        if k in ('start_hour','faults','shock','shock_by_priority') or k.startswith(('fault_','shock_'))}]))
    s['purpose']='development'
    s['q_design']=s['distribution_id'];s['p_reference']=None;s['importance_weight']=None
    s['actual_p0_load_multiplier']={k:(1+float(s.get('shock',{}).get(k,0)))*
        (1+float(s.get('shock_by_priority',{}).get('P0',{}).get(k,0))) for k in CARRIERS}
    return s

def split_rows(scenarios):
    rows=[]
    for s in scenarios:
        first=(int(s['start_hour'])-1)*60
        # The simulator reads hourly blocks; overlapping raw source hours,
        # not merely exact simulation minute windows, must remain grouped.
        duration=int(s['duration_steps'])*float(s.get('step_minutes',15))
        rows.append(dict(scenario_id=s['id'],family_id=s['family_id'],parent_template_id=s['parent_template_id'],
            base_state_id=s['base_state_id'],exogenous_stream_id=s['exogenous_stream_id'],
            load_start_minute=first,load_end_minute=first+math.ceil(duration/60)*60,
            sampling_cohort=s['sampling_cohort'],purpose='development'))
    return rows

def _identities(s,policy,manifest):
    return dict(scenario_id=s['id'],family_id=s['family_id'],parent_template_id=s['parent_template_id'],
        base_state_id=s['base_state_id'],exogenous_stream_id=s['exogenous_stream_id'],
        policy_id=policy,continuation_policy_id=policy,distribution_id=s['distribution_id'],
        execution_contract_id=manifest['execution_contract_id'],assumption_set_id=manifest['assumption_set_id'],
        simulator_hash=manifest['simulator_hash'],manifest_signature=manifest['signature'],
        schemas=manifest['schemas'],**manifest['splits'][s['id']])

def observed_events(env,s,observed_until):
    events=[]
    for raw in env.event_history:
        row=copy.deepcopy(raw)
        row['origin']=('exogenous' if row['kind']=='shock_changed' else
            'state_observation' if row['kind']=='availability_changed' else
            'control' if row['kind'].startswith('command_') else
            'endogenous_protection' if row['kind'] in ('protection_trip','no_export_interlock','minimum_output_interlock') else
            'local_response_or_state_observation')
        row['cause_class']={'protection_trip':'residual_stress_protection',
            'no_export_interlock':'no_export_isolation','minimum_output_interlock':'minimum_output_interlock'}.get(row['kind'],row['kind'])
        row['event_id']='core:'+str(row['event_id']);row['parent_event_id']=None
        events.append(row)
    # P1B does not log initial availability or initial shock at t=0. Export
    # observed exogenous boundaries without inventing causal links or trips.
    for kind in ('fault','shock'):
        active=s.get('faults') if kind=='fault' else s.get('shock') or s.get('shock_by_priority')
        if not active:continue
        start,end=event_window(s,kind)
        for label,t in (('start',start),('end',end)):
            if t>observed_until+1e-9:continue
            events.append(dict(event_id=f'exo:{kind}:{label}',time_minute=t,
                kind=f'exogenous_{kind}_{label}',cause_class='exogenous_device_fault' if kind=='fault' else 'exogenous_demand_shock',
                origin='exogenous',common_cause=kind=='fault' and 'TOWER' in s.get('faults',{}),
                parent_event_id=None,details=copy.deepcopy(s.get('faults') if kind=='fault' else
                    dict(shock=s.get('shock',{}),shock_by_priority=s.get('shock_by_priority',{}))),
                provenance='scenario boundary applied by unchanged P1B event clock'))
    return sorted(events,key=lambda x:(x['time_minute'],x['event_id']))

def run_trajectory(env,s,policy,seed,security_weight,manifest,horizons):
    identity=_identities(s,policy,manifest);decisions=[];legacy=[];error=None;initial=None
    status='completed';reset_completed=False
    try:
        env.reset(scenario=s,seed=seed)
        reset_completed=True
        initial=json_clean(dict(state=asdict(env.state),executed=env.core.executed))
        for _ in range(int(s['duration_steps'])):
            env.core.boundary()
            snapshot=decision_snapshot(env)
            snapshot.update(identity)
            action={} if policy=='priority_milp' else {'security':{'gt_loading_penalty_USD_per_kWh':security_weight}}
            validate_action(action)
            snapshot.update(requested_command=copy.deepcopy(action),permitted_command=None,
                command_generated=None,arrived_commands=[],actual_execution=None,post_outcome=None)
            decisions.append(snapshot)
            t0=time.perf_counter();before=env.core.observe()
            _,_,done,truncated,info=env.step(action)
            snapshot['decision_ms']=1000*(time.perf_counter()-t0)
            command=next(e['command'] for e in env.event_history
                if e['kind']=='command_generated' and e['command']['id']==info['command_id'])
            snapshot['command_generated']=copy.deepcopy(command)
            snapshot['permitted_command']=copy.deepcopy(command['dispatch'])
            snapshot['actual_execution']=copy.deepcopy(info['executed_action'])
            snapshot['arrived_commands']=[copy.deepcopy(e) for e in env.event_history
                if e['kind'] in ('command_arrived','command_executed','command_superseded')
                and snapshot['decision_time_min']<=e['time_minute']<env.core.time]
            snapshot['post_outcome']=dict(end_minute=env.core.time,
                p0_guarantee_failure=info['p0_guarantee_failure'],unserved_kW=info['unserved_kW'],
                cost_USD_step=info['cost_USD_step'],segment_count=len(info['segments']))
            rec=copy.deepcopy(env.history[-1])
            rec.update(policy=policy,episode_id=s['id']+'__'+policy,episode_family=s['id'],
                design_family=s['design_family'],sampling_cohort=s['sampling_cohort'],
                sample_stratum=s.get('stratum',''),sampling_method=s['sampling_method'],
                decision_ms=snapshot['decision_ms'],execution_contract_id=manifest['execution_contract_id'])
            # Legacy feature columns describe the observation, never the
            # interval-average demand (which may include a future subevent).
            current=before['loads_kW'];h=int(before['hour_of_year'])
            for j,k in enumerate(CARRIERS):
                normal=sum(float(env.groups.loc[h,f'{g}_{k}_kW']) for g in GROUPS)
                rec[f'demand_shock_{k}']=float(current[:,j].sum()/normal-1) if normal>1e-9 else 0.
                for gi,g in enumerate(GROUPS):rec[f'{g}_{k}_demand_kW']=float(current[gi,j])
            rec['active_faults']=';'.join(k for k,v in before['availability'].items() if v<1-1e-8)
            for key,value in zip(('balance_e_kW','balance_c_kW','balance_steam_kW'),info['energy_balance_residuals']):rec[key]=float(value)
            legacy.append(rec)
            if done or truncated:break
    except Exception as exc:
        category=getattr(exc,'category','solver_exception')
        status='model_constraint_infeasible' if category=='model_constraint_infeasible' else 'solver_error'
        if not reset_completed and isinstance(exc,ValueError) and not hasattr(exc,'simulation_diagnostic'):
            status='unsupported_model_regime';category='invalid_scenario_schema'
        # Only the explicitly registered missing-transient negative control;
        # preserve underlying mathematical infeasibility alongside this tag.
        if (status=='model_constraint_infeasible' and s.get('diagnostic_regime')=='positive_delay_no_absorption'
            and env.execution_contract['surplus_interlock_delay_minutes']>0):status='unsupported_model_regime'
        error=dict(type=type(exc).__name__,message=str(exc),underlying_category=category,
            classification=status,traceback=traceback.format_exc(),
            diagnostic=copy.deepcopy(getattr(exc,'simulation_diagnostic',None)))
    segments=copy.deepcopy(getattr(env,'segment_history',[])) if reset_completed else []
    observed_until=float(segments[-1]['end_minute']) if segments else 0.
    first,interval=attach_labels(decisions,segments,status,observed_until,horizons)
    valid_core=getattr(env,'core',None) if reset_completed else None
    events=observed_events(env,s,float(env.core.time)) if valid_core is not None else []
    # A physical solve may fail AFTER a command was generated or arrived.
    # Preserve those real lifecycle stages even without a completed step row.
    for decision in decisions:
        t=decision['decision_time_min'];end=t+env.dt_minutes
        generated=[e['command'] for e in env.event_history if e['kind']=='command_generated'
            and e['command']['generated_minute']==t]
        if generated:
            decision['command_generated']=copy.deepcopy(generated[-1])
            decision['permitted_command']=copy.deepcopy(generated[-1]['dispatch'])
        decision['arrived_commands']=[copy.deepcopy(e) for e in env.event_history
            if e['kind'] in ('command_arrived','command_executed','command_superseded') and t<=e['time_minute']<end]
        decision['observed_subintervals']=[dict(start_minute=x['start_minute'],end_minute=x['end_minute'])
            for x in segments if t<=x['start_minute']<end]
    energy=np.zeros((4,3))
    for seg in segments:energy+=np.asarray(seg['unserved_kW'])*(seg['end_minute']-seg['start_minute'])/60
    counts={k:sum(e['cause_class']==k for e in events) for k in
        ('residual_stress_protection','no_export_isolation','minimum_output_interlock','exogenous_device_fault')}
    # Exogenous incident count is onset-only; recoveries are separate events.
    counts['exogenous_device_fault']=sum(e['kind']=='exogenous_fault_start' for e in events)
    summary=dict(**identity,episode_family=s['id'],policy=policy,stage=s['stage'],event_class=s['event_class'],
        design_family=s['design_family'],initiating_device=s.get('initiating_device',''),
        sample_stratum=s.get('stratum',''),sampling_cohort=s['sampling_cohort'],
        status=status,attempted=True,completed=status=='completed',steps=len(legacy),
        physical_service_failure=first is not None,
        p0_failed_any=(1 if first is not None else 0 if status=='completed' else None),
        first_failure_observed_minute=first,first_failure_observation_interval=interval,
        observed_until_minute=observed_until,requested_end_minute=int(s['duration_steps'])*float(s.get('step_minutes',15)),
        outcome_unknown_after_minute=None if status=='completed' else observed_until,
        full_trajectory_outcome_valid=status=='completed',event_cause_counts=counts,
        secondary_fault_count=sum(counts[k] for k in ('residual_stress_protection','no_export_isolation','minimum_output_interlock')),
        projection_fallback_steps=0,economic_tiebreak_skipped_steps=sum('economic_tiebreak' in r.get('optimizer_warning','') for r in legacy),
        cost_USD_observed=sum(x['cost_USD_step'] for x in segments),
        max_balance_residual_kW=max((max(map(abs,x['energy_balance_residuals'])) for x in segments),default=None),
        max_steam_residual_lbph=max((abs(x['steam_balance_residual_lbph']) for x in segments),default=None),
        max_temperature_by_priority_C={g:max((x['temperature'][g] for x in segments),default=None) for g in GROUPS},
        temperature_violation_minutes={g:sum((x['end_minute']-x['start_minute']) for x in segments if x['violation'][g]) for g in GROUPS},
        q_design=s['distribution_id'],p_reference=None,importance_weight=None,
        ENS_semantics='integrated observed prefix only; use full_trajectory_outcome_valid for horizon totals',
        temperature_violation_duration_semantics='sum of classified subinterval durations, not interpolated threshold crossings')
    for gi,g in enumerate(GROUPS):
        for ki,k in enumerate(CARRIERS):summary[f'{g}_{k}_ENS_kWh']=float(energy[gi,ki])
    zones=copy.deepcopy(getattr(env,'zone_history',[])) if reset_completed else []
    for z in zones:z.update(policy=policy,episode_family=s['id'])
    return json_clean(dict(identity=identity,scenario=s,seed=seed,initial_state=initial,
        summary=summary,rows=legacy,decisions=decisions,zones=zones,events=events,segments=segments,
        solver_calls=env.core.solver_calls if valid_core is not None else [],
        solver_errors=(getattr(env,'solver_errors',[]) if reset_completed else
            [error['diagnostic']] if error and error['diagnostic'] is not None else []),error=error))

def generate_dataset(*,root,out,episodes_per_stage,steps,seed,resume,security_weight,
                     risk_horizon,policies,suite_id,execution_contract,scenario_specs=None,
                     max_trajectories=None,schema_versions=None,label_horizons=(15,30,60)):
    from scenario_sampler import sample_scenarios,coverage_summary
    from generate_nonmpc_training import add_future_risk_labels
    root=Path(root);out=Path(out)
    if steps<=risk_horizon or risk_horizon<1 or episodes_per_stage<1:raise ValueError('Need steps > risk_horizon >=1 and positive count')
    if set(policies)-set(POLICIES) or not policies or len(set(policies))!=len(policies):raise ValueError('Only distinct original non-MPC policies')
    if not math.isfinite(security_weight) or not 0<=security_weight<=10:raise ValueError('Invalid security weight')
    if max_trajectories is not None and max_trajectories<1:raise ValueError('Positive batch size required')
    contract=resolve_contract(execution_contract);env=None
    if scenario_specs is None:
        env=HospitalIESEnv(root,execution_contract=contract)
        scenario_specs=sample_scenarios(env.hourly,episodes_per_stage,steps,seed,suite_id=suite_id)
    scenarios=[normalize_scenario(s,seed) for s in scenario_specs]
    if not scenarios:raise ValueError('No scenario specifications; empty generation is not a dataset')
    if len({s['id'] for s in scenarios})!=len(scenarios):raise ValueError('Duplicate scenario_id')
    schemas=dict(SCHEMA_VERSIONS if schema_versions is None else schema_versions)
    if set(schemas)!=set(SCHEMA_VERSIONS) or any(not v for v in schemas.values()):raise ValueError('Complete nonempty schema versions required')
    splits=family_splits(split_rows(scenarios),seed=seed,embargo_minutes=60)
    manifest=build_manifest(root,scenarios,policies,contract,schemas,
        dict(seed=seed,security_weight=security_weight,risk_horizon=risk_horizon,
            label_horizons=list(label_horizons),purpose='development',embargo_minutes=60),splits)
    verify_execution_source(root)
    open_manifest(out,manifest,resume)
    payloads=[];new_attempts=0
    for s in scenarios:
        for policy in policies:
            cp=checkpoint_path(out,s['id'],policy)
            if cp.exists():
                payloads.append(load_checkpoint(cp,manifest['signature']));continue
            if max_trajectories is not None and new_attempts>=max_trajectories:continue
            if env is None:env=HospitalIESEnv(root,execution_contract=contract)
            payload=run_trajectory(env,s,policy,seed,security_weight,manifest,label_horizons)
            save_checkpoint(cp,payload,manifest['signature']);payloads.append(payload);new_attempts+=1
            print(f"{s['id']} {policy}: {payload['summary']['status']}, observed P0={payload['summary']['physical_service_failure']}",flush=True)
    payloads.sort(key=lambda p:(p['identity']['scenario_id'],p['identity']['policy_id']))
    summaries=[p['summary'] for p in payloads];rows=[r for p in payloads for r in p['rows']]
    df=pd.DataFrame(rows)
    if len(df):
        df=add_future_risk_labels(df,risk_horizon);build_features(df)
    df.to_csv(out/'closed_loop_expert_steps.csv',index=False)
    pd.DataFrame([z for p in payloads for z in p['zones']]).to_csv(out/'closed_loop_zone_temperatures.csv',index=False)
    pd.DataFrame(summaries).to_csv(out/'closed_loop_episode_summary.csv',index=False)
    uq=[dict(**p['summary'],scenario=p['scenario'],initial_state=p['initial_state']) for p in payloads]
    decisions=[r for p in payloads for r in p['decisions']]
    # Strongly separate model inputs from ex-post labels/commands/outcomes.
    features=build_decision_features(decisions)
    if len(features):
        for key in ('scenario_id','policy_id','decision_time_min','execution_contract_id','simulator_hash'):
            features.insert(0,key,[d[key] for d in decisions])
    features.to_csv(out/'decision_features.csv',index=False)
    for name,content in (('scenario_uq',uq),('decision_timeseries',decisions),
        ('device_graphs',[dict(**p['identity'],decision_time_min=d['decision_time_min'],graph=d['graph']) for p in payloads for d in p['decisions']]),
        ('event_trace',[dict(**p['identity'],event=e) for p in payloads for e in p['events']]),
        ('subinterval_trace',[dict(**p['identity'],segment=s) for p in payloads for s in p['segments']]),
        ('errors',[dict(**p['identity'],error=p['error'],solver_errors=p['solver_errors']) for p in payloads if p['error']])):
        write_json(out/(name+'.json'),content)
    write_json(out/'scenario_definitions.json',sorted(scenarios,key=lambda s:s['id']))
    write_json(out/'scenario_coverage.json',coverage_summary(scenarios))
    write_json(out/'split_manifest.json',dict(rows=split_rows(scenarios),assignments=splits,all_development=True,embargo_minutes=60))
    write_json(out/'schema.json',dict(versions=schemas,actions=ACTION_SCHEMA,
        observation_catalog=decisions[0]['feature_metadata'] if decisions else {},
        legacy_label='t+1..t+H completed rows; invalid tail retains old zero placeholder only with false mask',
        new_label='t<T_first_observed<=t+H minutes; first observed P1B classification, not continuous clinical time',
        input_table='decision_features.csv',forbidden_inputs=['scenario_uq','labels','post_outcome','command_generated','actual_execution']))
    write_json(out/'method_registry.json',registry())
    counts={k:sum(s['status']==k for s in summaries) for k in ('completed','model_constraint_infeasible','solver_error','unsupported_model_regime')}
    metadata=dict(version='p2a.diagnostic.v1',manifest_signature=manifest['signature'],
        scenario_specifications=len(scenarios),
        lineage_family_ids=len({s['family_id'] for s in scenarios}),
        parent_template_ids=len({s['parent_template_id'] for s in scenarios}),
        incident_families=len({v['group_id'] for v in splits.values()}),
        incident_family_semantics='conservative connected components of lineage and hourly windows plus 60min embargo',
        planned_trajectories=len(scenarios)*len(policies),
        attempted=len(summaries),trajectories=len(summaries),steps=len(rows),**counts,
        physical_service_failure=sum(s['physical_service_failure'] for s in summaries),
        unknown_full_outcome=sum(not s['full_trajectory_outcome_valid'] for s in summaries),
        policies=list(sorted(policies)),seed=seed,execution_contract=contract,
        future_label_steps=risk_horizon,future_label_semantics='unchanged legacy t+1..t+H',
        new_label_horizon_minutes=list(label_horizons),label_counts={str(h):{
            'positive':sum(d['labels'][str(h)]['value']==1 for d in decisions),
            'negative':sum(d['labels'][str(h)]['value']==0 for d in decisions),
            'unknown_or_not_at_risk':sum(not d['labels'][str(h)]['valid'] for d in decisions),
            'full_horizon':sum(d['labels'][str(h)]['full_horizon'] for d in decisions)} for h in label_horizons},
        interpretation='Development interface diagnostics only; no real probabilities, hardware calibration, training or policy benefit evidence')
    write_json(out/'dataset_metadata.json',metadata)
    return metadata
