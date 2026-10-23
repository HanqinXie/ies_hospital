"""Read-only, carrier-preserving M21 comparison and source-block inference.

No simulator, optimizer, plotting backend or legacy raw outcome is imported.
Synthetic arithmetic tests never become scientific result tables.
"""
from __future__ import annotations

import argparse
import copy
import csv
import gzip
import hashlib
import json
import math
from collections import Counter,defaultdict
from pathlib import Path

import numpy as np

PRIORITIES=('P0','P1','P2','P3')
CARRIERS=('e','h','c')
SERVICE_FIELDS=('requested_kWh','delivered_kWh','ENS_kWh','disconnected_ENS_kWh',
    'connected_unserved_ENS_kWh','material_minutes','longest_material_minutes',
    'interruption_count','repeat_interruption_count')
PHASES=('preparation','initial_handoff','endurance','recovery')
GAP_METRICS=('critical_initial_handoff_gap_minutes','critical_post_handoff_gap_minutes',
    'critical_recovery_gap_minutes')
PHASE_METRICS=tuple(f'phase_{phase}_{p}_{c}_ENS_kWh' for phase in PHASES for p in PRIORITIES for c in CARRIERS)
METRICS=tuple(f'{p}_{c}_{f}' for p in PRIORITIES for c in CARRIERS for f in SERVICE_FIELDS)+tuple(
    f'{p}_union_{f}' for p in PRIORITIES for f in ('material_minutes','longest_material_minutes','interruption_count','repeat_interruption_count'))+(
    'critical_union_minutes','energy_cost_USD','total_CO2_kg_conditional','online_solver_seconds','online_solver_max_seconds')+GAP_METRICS+PHASE_METRICS
COMPARE_METRICS=tuple(m for m in METRICS if '_requested_' not in m and '_delivered_' not in m)
SCHEMA='hospital_ies.sci_analysis.v21.v1'


def _read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def _sha(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):digest.update(block)
    return digest.hexdigest()


def _digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode('utf-8')).hexdigest()


def _number(value):
    if value is None:
        return None
    if isinstance(value,bool) or not isinstance(value,(int,float,np.number)) or not math.isfinite(value):
        raise ValueError('Nonfinite or nonnumeric scientific quantity')
    return float(value)


def verify_result_evidence(record):
    """Verify complete latest-result artifacts without restoring large solver logs.

    Old results with rows inline have no sidecar key. A declared sidecar is
    mandatory, including a valid empty sidecar, and is checked line by line.
    Failed attempts keep their recorded receipts; they cannot supply a
    completed scientific comparison irrespective of artifact availability.
    """
    declared=record.get('solver_row_evidence')
    required='solver_row_evidence' in record or 'raw_total_bytes' in record
    receipt=declared if isinstance(declared,dict) else {}
    result=dict(evidence_complete=False,evidence_status='not_completed',evidence_error=None,
        solver_sidecar_required=required,solver_sidecar_verified=False,
        solver_rows_path=receipt.get('path'),solver_rows_gzip_sha256=receipt.get('gzip_sha256'),
        solver_rows_gzip_bytes=receipt.get('gzip_bytes'),solver_rows_entry_count_declared=receipt.get('entry_count'),
        solver_rows_count_declared=receipt.get('row_count'),solver_rows_attempted_entry_count=receipt.get('attempted_entry_count'),
        solver_rows_entry_count_observed=None,solver_rows_count_observed=None,
        raw_sha256_verified=False,raw_size_for_resource_statistics_bytes=None)
    if record.get('status')!='completed':return result
    # In-memory arithmetic fixtures have no RESULT/raw binding. Registered
    # actual results always carry _result_path and must verify their raw file.
    bound=bool(record.get('_result_path') or record.get('raw_path') or required)
    if not bound:
        result.update(evidence_complete=True,evidence_status='unbound_in_memory_arithmetic')
        return result
    try:
        path=record.get('raw_path');digest=record.get('raw_sha256')
        if not path or not digest or not Path(path).is_file() or _sha(path)!=digest:
            raise ValueError('Missing completed main raw or main raw SHA drift')
        main_bytes=Path(path).stat().st_size
        if record.get('raw_bytes') is not None and record['raw_bytes']!=main_bytes:
            raise ValueError('Main raw byte count differs from recorded raw_bytes')
        result['raw_sha256_verified']=True
        if required:
            schema='hospital_ies.solver_validation_rows.sidecar.v1'
            if not isinstance(declared,dict) or receipt.get('schema')!=schema or receipt.get('closed') is not True or receipt.get('status')!='completed':
                raise ValueError('Missing or incomplete required solver sidecar receipt')
            if any(receipt.get(field) is not None for field in ('write_error','close_error','body_exception')) or receipt.get('partial_tail_possible') is True:
                raise ValueError('Required solver sidecar records an incomplete body or tail')
            counts=[receipt.get(key) for key in ('entry_count','row_count','attempted_entry_count')]
            if any(isinstance(x,bool) or not isinstance(x,int) or x<0 for x in counts) or counts[0]!=counts[2]:
                raise ValueError('Invalid or incomplete solver sidecar receipt counts')
            side=receipt.get('path')
            if not side or not Path(side).is_file() or not receipt.get('gzip_sha256') or _sha(side)!=receipt['gzip_sha256']:
                raise ValueError('Missing required solver sidecar or gzip SHA drift')
            if Path(side).stat().st_size!=receipt.get('gzip_bytes'):
                raise ValueError('Solver sidecar gzip byte count drift')
            seen=set();entry_count=row_count=0
            with gzip.open(side,'rt',encoding='utf-8') as stream:
                for line in stream:
                    if not line.endswith('\n'):raise ValueError('Incomplete solver sidecar JSONL tail')
                    entry=json.loads(line)
                    if not isinstance(entry,dict):raise ValueError('Solver sidecar entry is not an object')
                    identity=entry.get('record_id');rows=entry.get('rows')
                    nrows=entry.get('row_count')
                    if entry.get('schema')!=schema or not isinstance(identity,str) or not identity or identity in seen:
                        raise ValueError('Invalid or duplicate solver sidecar entry identity')
                    if not isinstance(rows,list) or isinstance(nrows,bool) or not isinstance(nrows,int) or len(rows)!=nrows:
                        raise ValueError('Solver sidecar entry row count drift')
                    canonical=json.dumps(rows,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode('utf-8')
                    if hashlib.sha256(canonical).hexdigest()!=entry.get('rows_sha256'):
                        raise ValueError('Solver sidecar entry rows SHA drift')
                    seen.add(identity);entry_count+=1;row_count+=nrows
            result.update(solver_rows_entry_count_observed=entry_count,solver_rows_count_observed=row_count)
            if entry_count!=counts[0] or row_count!=counts[1]:raise ValueError('Solver sidecar total entry or row count drift')
            result['solver_sidecar_verified']=True
            total=main_bytes+receipt['gzip_bytes']
        else:total=main_bytes
        if record.get('raw_total_bytes') is not None and record['raw_total_bytes']!=total:
            raise ValueError('Combined raw_total_bytes differs from verified main and solver sidecar bytes')
        result.update(evidence_complete=True,evidence_status='verified_main_and_solver_sidecar' if required else 'verified_legacy_inline_main',
            raw_size_for_resource_statistics_bytes=record.get('raw_total_bytes') if record.get('raw_total_bytes') is not None else total)
    except (OSError,EOFError,UnicodeError,ValueError,TypeError) as error:
        result.update(evidence_complete=False,evidence_status='evidence_drift',evidence_error=str(error),
            raw_size_for_resource_statistics_bytes=None)
    return result


def critical_continuity(metrics,scene,window):
    """Split actual interruption unions without pretending the handoff clock is a device event.

    Continuity begins at the first observed clean point at/after the registered
    initial window. Its upper observation limit is the registered recovery
    start, capped by the actual complete evaluation window. No recurrence by
    that limit is right censoring, not a measured fuel or energy limit.
    """
    row={field:None for field in GAP_METRICS}
    row.update(critical_first_observed_clean_minute=None,critical_first_gap_after_clean_minute=None,
        critical_observed_continuous_minutes=None,critical_continuity_right_censored=None,
        critical_continuity_limit_minute=None,critical_first_post_handoff_gap_minute=None,
        critical_continuity_status='unknown_missing_intervals')
    if window is None:return row
    incident=float(scene.get('first_event_minute',scene.get('fault_start_minute',0.)) or 0.)
    handoff=incident+float(scene.get('initial_handoff_window_minutes',15.))
    recovery=float(scene.get('recovery_start_minute',incident+float(scene.get('fault_duration_minutes',0.))))
    if not all(math.isfinite(x) for x in (incident,handoff,recovery,window)) or not 0<=incident<=recovery or handoff<incident:
        raise ValueError('Invalid registered incident, handoff or recovery boundary')
    first=min(handoff,recovery,window);limit=min(recovery,window)
    row['critical_continuity_limit_minute']=limit
    union=metrics.get('critical_union') or {}
    if 'intervals' not in union:return row
    intervals=[];previous=0.
    for item in union['intervals']:
        if not isinstance(item,(list,tuple)) or len(item)!=2:raise ValueError('Invalid critical union interval')
        a,b=map(float,item)
        if not math.isfinite(a+b) or a<0 or b<=a or a<previous-1e-8 or b>window+1e-7:
            raise ValueError('Overlapping or invalid critical union interval')
        intervals.append((a,b));previous=b
    material=union.get('material_minutes')
    if material is not None and abs(sum(b-a for a,b in intervals)-_number(material))>1e-7:
        raise ValueError('Critical intervals do not close to aggregate material minutes')
    def overlap(start,end):
        return sum(max(0.,min(b,end)-max(a,start)) for a,b in intervals) if end>start else 0.
    row.update(critical_initial_handoff_gap_minutes=overlap(incident,first),
        critical_post_handoff_gap_minutes=overlap(first,limit),
        critical_recovery_gap_minutes=overlap(limit,window))
    later=[max(a,first) for a,b in intervals if b>first and a<limit]
    row['critical_first_post_handoff_gap_minute']=min(later) if later else None
    anchor=first
    for a,b in intervals:
        if a<=anchor<b:anchor=b
    if anchor>=limit:
        row['critical_continuity_status']='no_observed_clean_boundary_before_recovery'
        return row
    later=[a for a,b in intervals if a>=anchor and a<limit]
    gap=min(later) if later else None
    row.update(critical_first_observed_clean_minute=anchor,critical_first_gap_after_clean_minute=gap,
        critical_observed_continuous_minutes=(gap if gap is not None else limit)-anchor,
        critical_continuity_right_censored=gap is None,
        critical_continuity_status='right_censored_at_recovery_or_window' if gap is None else 'observed_later_gap')
    return row


def collect_results(directories):
    """Attach immutable registration metadata; never open raw trajectories."""
    results=[]
    for directory in map(Path,directories):
        plan_path=directory/'PLAN.json';plan=_read(plan_path)
        scenes={s['id']:s for s in plan['panel']['scenarios']}
        policies={p['policy_id']:p for p in plan['policy_specs']}
        manifest=_read(directory/'MANIFEST.json')
        found=set()
        for path in sorted(directory.glob('pairs/*/attempt_*/RESULT.json')):
            row=_read(path)
            if row.get('scenario_id') not in scenes or row.get('policy_id') not in policies:
                raise ValueError('Actual outcome absent from registered scientific identity')
            row.update(_scenario=copy.deepcopy(scenes[row['scenario_id']]),
                _policy=copy.deepcopy(policies[row['policy_id']]),_campaign=str(directory.resolve()),
                _result_path=str(path.resolve()),_result_sha256=_sha(path),_plan_sha256=_sha(plan_path))
            results.append(row)
            found.add((row['scenario_id'],row['policy_id'],row['configuration_id']))
        starts={}
        journal=directory/'ATTEMPTS.jsonl'
        if journal.is_file():
            for line in journal.read_text(encoding='utf-8-sig').splitlines():
                item=json.loads(line)
                if item.get('event')=='started':
                    starts[item['scenario_id'],item['policy_id'],item['configuration_id']]=item
        for sid,scene in scenes.items():
            for pid,policy in policies.items():
                for config_id,config in plan['configurations'].items():
                    key=(sid,pid,config_id)
                    if key in found:continue
                    started=starts.get(key);duration=scene['duration_steps']*scene.get('step_minutes',15.)
                    results.append(dict(scenario_id=sid,policy_id=pid,configuration_id=config_id,
                        source_block_id=scene['source_block_id'],model_id=plan['model_id'],phase=plan['phase'],
                        source_binding_sha256=manifest['simulator_hash'],scenario_sha256=_digest(scene),
                        policy_sha256=_digest(policy),configuration_sha256=_digest(config),
                        canonical_class=scene.get('canonical_class'),season_quarter=scene.get('season_quarter'),
                        status='started_no_terminal' if started else 'not_started',
                        attempt_id=started['attempt_id'] if started else 'not_started',
                        metrics=dict(complete=False,requested_end_minute=duration,unknown_tail_minutes=duration),
                        unknown_tail_minutes=duration,_scenario=copy.deepcopy(scene),_policy=copy.deepcopy(policy),
                        _campaign=str(directory.resolve()),_plan_sha256=_sha(plan_path),
                        _registration_is_not_physical_outcome=True))
    return results


def flatten_records(records):
    bindings={r.get('source_binding_sha256') for r in records if r.get('source_binding_sha256')}
    models={r.get('model_id') for r in records if r.get('model_id')}
    if len(bindings)>1 or len(models)>1:
        raise ValueError('Mixed source/model versions cannot form one scientific analysis')
    latest={};counts=Counter()
    for record in records:
        key=tuple(record.get(k) for k in ('phase','scenario_id','policy_id','configuration_id','model_id','source_binding_sha256'))
        counts[key]+=1
        rank=str(record.get('attempt_id','attempt_0001'))
        if key not in latest or rank>str(latest[key].get('attempt_id','attempt_0001')):
            latest[key]=record
        elif rank==str(latest[key].get('attempt_id','attempt_0001')) and record!=latest[key]:
            # Multiple copies of identical evidence may be indexed, but two
            # conflicting terminals with one attempt identity are corruption.
            fields=('status','raw_sha256','scenario_sha256','policy_sha256','configuration_sha256')
            if any(record.get(f)!=latest[key].get(f) for f in fields):
                raise ValueError('Conflicting terminal records for the same attempt')
    output=[]
    for key,record in latest.items():
        metrics=record.get('metrics') or {};scene=record.get('_scenario') or {};policy=record.get('_policy') or {}
        evidence=verify_result_evidence(record)
        eligible=(record.get('status')=='completed' and metrics.get('complete') is True
            and record.get('source_unchanged',True) is True and record.get('error') is None and evidence['evidence_complete'])
        registered_window=(float(scene['duration_steps'])*float(scene.get('step_minutes',15.))
            if 'duration_steps' in scene else None)
        row={k:record.get(k) for k in ('scenario_id','policy_id','configuration_id','source_block_id','model_id',
            'source_binding_sha256','scenario_sha256','policy_sha256','configuration_sha256','phase',
            'canonical_class','season_quarter','status','attempt_id','raw_sha256','elapsed_seconds','raw_bytes','raw_total_bytes')}
        row.update(evidence,recorded_status=record.get('status'))
        if evidence['evidence_status']=='evidence_drift':row['status']='evidence_drift'
        row.update(eligible=eligible,attempts_observed=counts[key],
            window_minutes=_number(metrics.get('requested_end_minute',registered_window)),
            unknown_tail_minutes=_number(record.get('unknown_tail_minutes',metrics.get('unknown_tail_minutes'))),
            policy_alias=policy.get('display_alias'),matched_factor_id=scene.get('matched_factor_id',record['scenario_id']),
            interaction_group_id=scene.get('interaction_group_id'),interaction_cell=scene.get('interaction_cell'),
            enable_propagation=scene.get('enable_propagation'),
            mechanism_comparison=policy.get('mechanism_comparison',False),
            reserve_enabled=policy.get('config',{}).get('reserve_enabled'),
            critical_preparation_first=policy.get('config',{}).get('critical_preparation_first'),
            legacy_C19_order=policy.get('config',{}).get('legacy_C19_order'),
            fault_duration_minutes=scene.get('fault_duration_minutes'),initial_reserve_kWh=scene.get('initial_reserve_kWh'),
            demand_amplitude=scene.get('demand_amplitude'),rejection_capacity_kW=scene.get('rejection_capacity_kW'),
            critical_cooling_kW_lower_bound=scene.get('critical_cooling_kW_lower_bound'),
            rejection_bound_source=scene.get('rejection_bound_source'),fault_target=scene.get('fault_target'),
            fault_availability=scene.get('fault_availability'),response_delay_minutes=scene.get('response_delay_minutes'),
            result_path=record.get('_result_path'),result_sha256=record.get('_result_sha256'),
            raw_path=record.get('raw_path'),plan_sha256=record.get('_plan_sha256'))
        if evidence['evidence_status']=='evidence_drift':row['unknown_tail_minutes']=row['window_minutes']
        for field,value in (record.get('resource_measurement') or {}).items():row['resource_'+field]=value
        for field,value in metrics.items():
            if field.startswith('online_') or field in ('solver_call_count','solver_backend_attempt_count',
                'solver_backend_rejected_attempt_count','solver_timeout_attempt_count','scheduling_period_seconds'):
                row['resource_'+field]=value
        for metric in METRICS:row[metric]=None
        row.update(critical_continuity({},scene,row['window_minutes']))
        if row.get('critical_cooling_kW_lower_bound') is not None and row.get('rejection_bound_source'):
            row['required_rejection_kW_lower_bound']=_number(row['critical_cooling_kW_lower_bound'])*(1.+.77/3.51685)
            row['rejection_bound_formula']='P0+P1 cold lower bound * (1 + 0.77 / 3.51685); optimistic all-electric shared rejection path'
        if eligible:
            for p in PRIORITIES:
                for c in CARRIERS:
                    service=metrics.get('carrier_service',{}).get(p,{}).get(c,{})
                    for field in SERVICE_FIELDS:row[f'{p}_{c}_{field}']=_number(service.get(field))
                union=metrics.get('priority_union',{}).get(p,{})
                for field in ('material_minutes','longest_material_minutes','interruption_count','repeat_interruption_count'):
                    row[f'{p}_union_{field}']=_number(union.get(field))
            row['critical_union_minutes']=_number(metrics.get('critical_union',{}).get('material_minutes'))
            row.update(critical_continuity(metrics,scene,row['window_minutes']))
            for phase in PHASES:
                for p in PRIORITIES:
                    for c in CARRIERS:
                        row[f'phase_{phase}_{p}_{c}_ENS_kWh']=_number(metrics.get('phase_ENS_kWh',{}).get(phase,{}).get(p,{}).get(c))
            economics=metrics.get('economics') or {}
            row['energy_cost_USD']=_number(economics.get('energy_cost_USD'))
            emission=economics.get('emissions_conditional') or {}
            row['total_CO2_kg_conditional']=_number(emission.get('total_CO2_kg_conditional'))
            row['emissions_status']=emission.get('emissions_status','unknown')
            row['initial_inventory_fee_USD_conditional']=_number(economics.get('initial_inventory_fee_USD_conditional'))
            for field in ('online_solver_seconds','online_solver_max_seconds'):
                row[field]=_number(metrics.get(field))
            for field,value in (metrics.get('actual_energy') or {}).items():row['energy_'+field]=_number(value)
            for store,value in (metrics.get('storage') or {}).items():
                for field,quantity in (value.get('totals') or {}).items():row[store+'_'+field]=_number(quantity)
        output.append(row)
    return sorted(output,key=lambda r:tuple(str(r.get(k,'')) for k in ('phase','scenario_id','configuration_id','policy_id')))


def _check_pair(left,right):
    for field in ('model_id','source_binding_sha256','source_block_id','scenario_sha256'):
        if left.get(field)!=right.get(field):raise ValueError('Mismatched source or scenario: '+field)
    if left.get('window_minutes')!=right.get('window_minutes'):
        raise ValueError('Different evaluation windows cannot be paired')


def _difference(candidate,reference,metric):
    _check_pair(candidate,reference)
    left,right=candidate.get(metric),reference.get(metric)
    valid=candidate['eligible'] and reference['eligible'] and left is not None and right is not None
    return dict(scenario_id=candidate['scenario_id'],source_block_id=candidate['source_block_id'],
        canonical_class=candidate['canonical_class'],season_quarter=candidate['season_quarter'],
        configuration_id=candidate['configuration_id'],phase=candidate.get('phase'),
        candidate_policy_id=candidate['policy_id'],reference_policy_id=reference['policy_id'],
        window_minutes=candidate['window_minutes'],metric=metric,paired=valid,
        candidate_value=left if valid else None,reference_value=right if valid else None,
        delta=left-right if valid else None,
        comparison_status='paired_complete' if valid else 'unknown_or_failed_input')


def paired_differences(rows,reference_policy_id):
    groups=defaultdict(dict)
    for row in rows:groups[row.get('phase'),row['scenario_id'],row['configuration_id']][row['policy_id']]=row
    output=[]
    for group in groups.values():
        if reference_policy_id not in group:continue
        reference=group[reference_policy_id]
        for policy,candidate in group.items():
            if policy!=reference_policy_id:
                output.extend(_difference(candidate,reference,metric) for metric in COMPARE_METRICS)
    return output


def source_block_interval(pairs,*,seed=20261023,repetitions=2000):
    groups=defaultdict(list)
    for row in pairs:
        if row.get('delta') is not None:groups[row['source_block_id']].append(float(row['delta']))
    means=np.array([np.mean(groups[k]) for k in sorted(groups)],float)
    result=dict(source_block_count=len(means),paired_scenario_count=sum(map(len,groups.values())),
        mean_block_delta=float(means.mean()) if len(means) else None,lower_95=None,upper_95=None,
        estimand='Equal-weight source-block means; variants within a block remain correlated',
        bootstrap_seed=seed,bootstrap_repetitions=repetitions)
    if len(means)>=2:
        if not isinstance(repetitions,int) or repetitions<100:raise ValueError('At least100 registered bootstrap replications required')
        rng=np.random.default_rng(seed)
        values=np.array([rng.choice(means,size=len(means),replace=True).mean() for _ in range(repetitions)])
        result.update(lower_95=float(np.quantile(values,.025)),upper_95=float(np.quantile(values,.975)),status='cluster_bootstrap')
    else:result['status']='insufficient_source_blocks_for_interval'
    return result


def summarize_pairs(pairs,*,repetitions=2000,seed=20261023,numerical_zero=1e-8):
    groups=defaultdict(list)
    for row in pairs:
        base=(row.get('phase'),row['candidate_policy_id'],row['reference_policy_id'],row['configuration_id'],row['window_minutes'],row['metric'])
        for typ,value in (('all','all'),('canonical_class',row['canonical_class']),('season_quarter',row['season_quarter'])):
            groups[base+(typ,value)].append(row)
    output=[]
    for key,items in sorted(groups.items(),key=lambda x:str(x[0])):
        phase,candidate,reference,config,window,metric,typ,value=key
        complete=[r for r in items if r['paired']]
        delta=np.array([r['delta'] for r in complete],float)
        ci=source_block_interval(complete,seed=seed,repetitions=repetitions)
        output.append(dict(phase=phase,candidate_policy_id=candidate,reference_policy_id=reference,
            configuration_id=config,window_minutes=window,metric=metric,stratum_type=typ,stratum=value,
            registered_comparisons=len(items),paired_scenarios=len(complete),unknown_comparisons=len(items)-len(complete),
            source_blocks=ci['source_block_count'],improved=int(np.sum(delta < -numerical_zero)),
            tied=int(np.sum(np.abs(delta)<=numerical_zero)),worsened=int(np.sum(delta>numerical_zero)),
            mean_scenario_delta=float(delta.mean()) if len(delta) else None,
            median_delta=float(np.median(delta)) if len(delta) else None,
            p10_delta=float(np.quantile(delta,.1)) if len(delta) else None,
            p90_delta=float(np.quantile(delta,.9)) if len(delta) else None,
            worst_delta=float(delta.max()) if len(delta) else None,
            numerical_zero_threshold=numerical_zero,service_failure_threshold_unchanged=True,**ci))
    return output


def mechanism_effects(rows):
    groups=defaultdict(dict)
    for row in rows:
        if row.get('mechanism_comparison') is not True:continue
        flags=row.get('reserve_enabled'),row.get('critical_preparation_first')
        if not all(type(v) is bool for v in flags) or row.get('legacy_C19_order') is not False:continue
        key=(row.get('phase'),row['matched_factor_id'],row['canonical_class'],row['configuration_id'],row['source_block_id'],row['window_minutes'])
        if flags in groups[key]:raise ValueError('Duplicate mechanism cell within one matched physical factor')
        groups[key][flags]=row
    output=[]
    for key,cells in groups.items():
        if set(cells)!={(False,False),(True,False),(False,True),(True,True)}:
            output.append(dict(matched_factor_id=key[1],status='incomplete_four_cells',observed_cells=len(cells)))
            continue
        values=[cells[k] for k in ((False,False),(True,False),(False,True),(True,True))]
        for other in values[1:]:_check_pair(values[0],other)
        for metric in COMPARE_METRICS:
            valid=all(r['eligible'] and r.get(metric) is not None for r in values)
            y=[r.get(metric) for r in values]
            output.append(dict(matched_factor_id=key[1],canonical_class=key[2],configuration_id=key[3],source_block_id=key[4],
                window_minutes=key[5],metric=metric,status='complete_matched_four_cells' if valid else 'unknown_cell',
                reserve_only_delta=y[1]-y[0] if valid else None,preparation_only_delta=y[2]-y[0] if valid else None,
                both_delta=y[3]-y[0] if valid else None,interaction_delta=y[3]-y[1]-y[2]+y[0] if valid else None))
    return output


def hardware_differences(rows):
    groups=defaultdict(dict)
    for row in rows:
        if row.get('policy_alias') not in ('C','A'):continue
        groups[row.get('phase'),row['scenario_id'],row['policy_id']][row['configuration_id']]=row
    output=[]
    for group in groups.values():
        if 'H0' not in group:continue
        for config,candidate in group.items():
            if config not in ('H1','H2'):continue
            if candidate.get('policy_sha256')!=group['H0'].get('policy_sha256'):
                raise ValueError('Hardware comparison changed the upper policy')
            for metric in COMPARE_METRICS:
                row=_difference(candidate,group['H0'],metric)
                row.update(reference_configuration_id='H0',hardware_change_only=True)
                output.append(row)
    return output


def disturbance_interactions(rows):
    """ENS(fault+shock)-ENS(fault)-ENS(shock)+ENS(normal), same protection.

    Native L3/L4/L4b identity is descriptive only. Only explicit registered
    interaction groups enter this table; the main four-class pool does not.
    """
    names=('normal','fault_only','shock_only','fault_and_shock')
    groups=defaultdict(dict)
    for row in rows:
        group=row.get('interaction_group_id')
        if group is None:continue
        cell=row.get('interaction_cell')
        if cell not in names:raise ValueError('Invalid registered disturbance interaction cell')
        if row.get('enable_propagation') is not True:raise ValueError('Interaction cells must share enabled propagation/protection')
        key=(row.get('phase'),group,row['policy_id'],row['configuration_id'])
        if cell in groups[key]:raise ValueError('Duplicate disturbance interaction cell')
        groups[key][cell]=row
    output=[]
    for key,cells in groups.items():
        reference=next(iter(cells.values()))
        for other in cells.values():
            for field in ('model_id','source_binding_sha256','source_block_id','policy_sha256','configuration_sha256','window_minutes'):
                if other.get(field)!=reference.get(field):raise ValueError('Disturbance interaction changed '+field)
        complete=set(cells)==set(names)
        for p in PRIORITIES:
            for c in CARRIERS:
                field=f'{p}_{c}_ENS_kWh'
                valid=complete and all(row['eligible'] and row.get(field) is not None for row in cells.values())
                output.append(dict(phase=key[0],interaction_group_id=key[1],policy_id=key[2],configuration_id=key[3],
                    policy_alias=reference.get('policy_alias'),source_block_id=reference['source_block_id'],
                    window_minutes=reference['window_minutes'],priority=p,carrier=c,observed_cells=len(cells),
                    status='complete_matched_four_cells' if valid else 'unknown_or_failed_cell' if complete else 'incomplete_four_cells',
                    interaction_ENS_kWh=(cells['fault_and_shock'][field]-cells['fault_only'][field]-cells['shock_only'][field]+cells['normal'][field]) if valid else None,
                    normal_ENS_kWh=cells.get('normal',{}).get(field) if valid else None,
                    fault_ENS_kWh=cells.get('fault_only',{}).get(field) if valid else None,
                    shock_ENS_kWh=cells.get('shock_only',{}).get(field) if valid else None,
                    combined_ENS_kWh=cells.get('fault_and_shock',{}).get(field) if valid else None,
                    protection_condition_unchanged=True,native_class_is_not_a_pairing_key=True))
    return output


def necessary_conditions(rows):
    """Only sourced optimistic bounds can certify a physical necessary failure."""
    output=[]
    for item in rows:
        row=copy.deepcopy(item);certificates=[]
        sourced=bool(row.get('bound_source'))
        for demand,available,label in (
            ('required_shaft_energy_kWh_lower_bound','available_shaft_energy_kWh','energy_lower_bound_exceeds_inventory'),
            ('critical_electric_kW_lower_bound','available_electric_kW_upper_bound','power_lower_bound_exceeds_capacity'),
            ('critical_cooling_kW_lower_bound','available_cooling_kW_upper_bound','cold_lower_bound_exceeds_capacity'),
            ('required_rejection_kW_lower_bound','rejection_capacity_kW','rejection_lower_bound_exceeds_capacity')):
            if sourced and row.get(demand) is not None and row.get(available) is not None:
                if _number(row[demand])>_number(row[available])+1e-8:certificates.append(label)
        if sourced and row.get('all_critical_cooling_paths_unavailable') is True:
            certificates.append('no_available_critical_cooling_path')
        row.update(physical_impossibility_certified=bool(certificates),certificates=';'.join(certificates),
            status='necessary_condition_violated' if certificates else 'no_necessary_violation_proved' if sourced else 'insufficient_optimistic_bound',
            online_policy_ranking=False,all_tested_failures_are_not_a_physical_proof=True)
        output.append(row)
    return output


def build_tables(records,*,references=('C_sci_v21',),repetitions=2000,seed=20261023,bounds=()):
    rows=flatten_records(records)
    paired=[]
    for reference in references:paired.extend(paired_differences(rows,reference))
    service=[]
    for row in rows:
        for p in PRIORITIES:
            for c in CARRIERS:
                service.append(dict(**{k:row[k] for k in ('scenario_id','policy_id','configuration_id','source_block_id','canonical_class','season_quarter','window_minutes','eligible')},
                    priority=p,carrier=c,**{f:row.get(f'{p}_{c}_{f}') for f in SERVICE_FIELDS}))
    registered_bounds={}
    for row in rows:
        if row.get('configuration_id') not in ('H0','H1') or not row.get('rejection_bound_source') or row.get('required_rejection_kW_lower_bound') is None:continue
        key=(row.get('phase'),row['scenario_id'],row['configuration_id'])
        registered_bounds[key]=dict(boundary_id='__'.join(str(x) for x in key),
            **{k:row.get(k) for k in ('scenario_id','configuration_id','source_block_id','demand_amplitude','rejection_capacity_kW',
                'critical_cooling_kW_lower_bound','required_rejection_kW_lower_bound','rejection_bound_formula')},
            bound_source=row['rejection_bound_source'],applicability='Shared rejection path; no independent stored/local cold bypass',
            condition_scope='Necessary instantaneous capacity only; no continuity or achievable upper-policy certificate')
    resource_fields=('scenario_id','policy_id','configuration_id','source_block_id','phase','attempt_id','recorded_status','status',
        'eligible','evidence_complete','evidence_status','evidence_error','elapsed_seconds','raw_bytes','raw_total_bytes',
        'raw_size_for_resource_statistics_bytes','solver_rows_gzip_bytes','solver_sidecar_required','solver_sidecar_verified',
        'solver_rows_entry_count_declared','solver_rows_count_declared','solver_rows_attempted_entry_count',
        'solver_rows_entry_count_observed','solver_rows_count_observed')
    resources=[{key:row.get(key) for key in (*resource_fields,*sorted(k for k in row if k.startswith('resource_')))} for row in rows]
    integrity=dict(latest_case_count=len(rows),evidence_complete_cases=sum(r['evidence_complete'] for r in rows),
        evidence_drift_cases=sum(r['evidence_status']=='evidence_drift' for r in rows),
        solver_sidecar_required_cases=sum(r['solver_sidecar_required'] for r in rows),
        solver_sidecar_verified_cases=sum(r['solver_sidecar_verified'] for r in rows),
        solver_row_entries_verified=sum(r['solver_rows_entry_count_observed'] or 0 for r in rows if r['solver_sidecar_verified']),
        solver_rows_verified=sum(r['solver_rows_count_observed'] or 0 for r in rows if r['solver_sidecar_verified']),
        count_scope='Latest case evidence; all original attempts remain in ATTEMPT_STATUS',
        raw_size_statistics='Prefer RESULT.raw_total_bytes; trajectory raw_bytes and solver_rows_gzip_bytes remain separate')
    return dict(ATTEMPT_STATUS=[{k:r.get(k) for k in ('scenario_id','policy_id','configuration_id','attempt_id','status','error','raw_sha256','source_binding_sha256','_result_path','_result_sha256','solver_row_evidence','raw_bytes','raw_total_bytes')} for r in records],
        CASE_METRICS=rows,CARRIER_SERVICE=service,PAIRED_DIFFERENCES=paired,
        RESOURCE_MEASUREMENTS=resources,EVIDENCE_INTEGRITY_SUMMARY=[integrity],
        PAIRED_SUMMARY=summarize_pairs(paired,repetitions=repetitions,seed=seed),
        MECHANISM_EFFECTS=mechanism_effects(rows),HARDWARE_DIFFERENCES=hardware_differences(rows),
        DISTURBANCE_INTERACTIONS=disturbance_interactions(rows),
        NECESSARY_BOUNDARY_CONDITIONS=necessary_conditions([*bounds,*registered_bounds.values()]))


def export_tables(tables,directory):
    """MATLAB consumes CSV strings and the matching MAT numeric matrices."""
    from scipy.io import savemat
    out=Path(directory)
    if out.exists():raise FileExistsError('Use a fresh immutable analysis export')
    out.mkdir(parents=True)
    inventory=[];mat={}
    for name,rows in tables.items():
        fields=sorted({k for r in rows for k in r})
        path=out/(name+'.csv')
        with path.open('x',encoding='utf-8-sig',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader()
            for row in rows:
                writer.writerow({k:(json.dumps(v,ensure_ascii=False,sort_keys=True) if isinstance(v,(dict,list,tuple)) else v) for k,v in row.items()})
        numeric=[f for f in fields if all(r.get(f) is None or isinstance(r.get(f),(int,float,np.number,bool)) for r in rows)]
        mat[name+'_numeric_columns']=np.array(numeric,dtype=object)
        mat[name+'_numeric']=np.array([[float(r[f]) if r.get(f) is not None else np.nan for f in numeric] for r in rows],float).reshape(len(rows),len(numeric))
        mat[name+'_csv_file']=name+'.csv'
        inventory.append(dict(path=path.name,sha256=_sha(path),rows=len(rows),columns=len(fields)))
    savemat(out/'SCI_TABLES.mat',mat,do_compression=True,long_field_names=True)
    inventory.append(dict(path='SCI_TABLES.mat',sha256=_sha(out/'SCI_TABLES.mat')))
    receipt=dict(schema=SCHEMA,files=inventory,scientific_figures_generated=0,
        final_plot_backend='MATLAB only',carrier_energies_are_not_added=True,
        source_blocks_are_statistical_units=True,unknown_quantities_are_not_zero=True,
        mixed_windows_are_separate=True,emissions_are_conditional_post_run_CO2=True)
    (out/'ANALYSIS_RECEIPT.json').write_text(json.dumps(receipt,indent=2,ensure_ascii=False),encoding='utf-8')
    return receipt


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--campaign',action='append',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--reference',action='append')
    parser.add_argument('--bootstrap',type=int,default=2000)
    parser.add_argument('--seed',type=int,default=20261023)
    parser.add_argument('--bounds',type=Path)
    args=parser.parse_args(argv)
    tables=build_tables(collect_results(args.campaign),references=tuple(args.reference or ['C_sci_v21']),
        repetitions=args.bootstrap,seed=args.seed,bounds=_read(args.bounds) if args.bounds else ())
    receipt=export_tables(tables,args.output)
    print(json.dumps(receipt,ensure_ascii=False));return 0


if __name__=='__main__':raise SystemExit(main())
