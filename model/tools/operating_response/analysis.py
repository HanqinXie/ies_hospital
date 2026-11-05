"""Single-pass v15 operating analysis of the caller's in-memory payload.

Original nineteen scores are delegated unchanged to the immutable v14
analysis. New full/event windows, rotor energy and missing P3 thermal coverage
are explicit additions. No simulator, solve or raw-file reload is performed.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import re

from tools.engineering_model import analysis as old
from hospital_ies.dataset_export import json_clean

SCHEMA='hospital_ies.operating_saved_analysis.v15.v2'
ENERGIES=('electrical_work_kWh','mechanical_work_kWh','loss_work_kWh','rotor_delta_kWh')


def _native_event_view(payload):
    """Restore explicit core:N sequence within ties; preserve cross-source slots.

    The native exporter sorts event_id strings lexically, so core:13 precedes
    core:9 at the same minute. Numeric core sequence is recorded provenance,
    unlike command generation IDs or a guessed across-source causal order.
    Segment IDs and all old lifecycle consistency checks remain untouched.
    """
    events=list(payload.get('events',[]));core={};by_time={};noncore_times=set()
    for position,event in enumerate(events):
        match=re.fullmatch(r'core:([1-9][0-9]*)',str(event.get('event_id','')))
        if not match:
            if event.get('time_minute') is not None:noncore_times.add(float(event['time_minute']))
            continue
        sequence=int(match.group(1))
        if sequence in core:raise ValueError('Duplicate native core event sequence')
        minute=old._number(event['time_minute'])
        core[sequence]=(minute,position,event)
        by_time.setdefault(minute,[]).append((position,sequence,event))
    native=[core[n][0] for n in sorted(core)]
    if any(b<a-1e-9 for a,b in zip(native,native[1:])):
        raise ValueError('Native core event sequence contradicts recorded time')
    restored=list(events);changed=0;cross_source=[]
    for minute,members in by_time.items():
        positions=sorted(position for position,_,_ in members)
        ordered=[event for _,_,event in sorted(members,key=lambda item:item[1])]
        for position,event in zip(positions,ordered):
            changed+=event is not events[position]
            restored[position]=event
        if minute in noncore_times:
            cross_source.append(minute)
    ids=lambda records:[event.get('event_id') for event in records]
    receipt=dict(schema='hospital_ies.operating_event_order_migration.v15.v1',
        native_core_event_count=len(core),reordered_same_time_core_events=changed,
        source_saved_event_order_sha256=hashlib.sha256(json.dumps(ids(events),ensure_ascii=True).encode()).hexdigest(),
        analysis_event_order_sha256=hashlib.sha256(json.dumps(ids(restored),ensure_ascii=True).encode()).hexdigest(),
        ordering_basis='Explicit original native core:N numeric sequence within identical recorded minute only',
        segment_command_ids_reassigned=False,raw_payload_mutated=False,
        cross_source_equal_time_minutes=sorted(cross_source),cross_source_causal_order='not_identified; original noncore slots retained',
        original_lifecycle_assertions_preserved=True)
    return dict(payload,events=restored),receipt


def _extra_windows(payload):
    scenario=payload.get('scenario',{});requested=float(payload['summary']['requested_end_minute'])
    windows={'registered_full':(0.,requested)}
    first=scenario.get('first_event_minute')
    if first is None:
        starts=[]
        if scenario.get('faults'):starts.append(float(scenario.get('fault_start_minute',0.)))
        if scenario.get('shock') or scenario.get('shock_by_priority'):starts.append(float(scenario.get('shock_start_minute',0.)))
        first=min(starts) if starts else None
    if first is not None:
        first=float(first)
        if not 0.<=first<requested:raise ValueError('First event outside registered episode')
        if first>0:windows['normal_prefix']=(0.,first)
        windows['event_first_600']=(first,first+600.)
        windows['event_to_registered_end']=(first,requested)
        for kind in ('fault','shock'):
            active=bool(scenario.get('faults')) if kind=='fault' else bool(scenario.get('shock') or scenario.get('shock_by_priority'))
            if not active:continue
            a=float(scenario.get(kind+'_start_minute',0.));b=a+float(scenario.get(kind+'_duration_minutes',requested-a))
            windows[kind+'_active']=(a,b)
            if b<requested:windows[kind+'_recovery_to_end']=(b,requested)
    return windows,first


def _diesel(payload):
    rows=[];sums={k:0. for k in ENERGIES};seen={k:False for k in ENERGIES}
    account_names=('fuel_budget_debit_kWh_equivalent','original_electric_fee_USD','shaft_equivalent_fee_USD')
    accounts={k:0. for k in account_names};account_seen={k:False for k in account_names}
    speeds=[]
    aliases={
        'electrical_work_kWh':('electrical_work_kWh','electrical_energy_kWh','Pe_work_kWh'),
        'mechanical_work_kWh':('mechanical_work_kWh','mechanical_energy_kWh','Pm_work_kWh'),
        'loss_work_kWh':('loss_work_kWh','loss_energy_kWh','shaft_loss_kWh'),
        'rotor_delta_kWh':('rotor_delta_kWh','delta_rotor_energy_kWh','delta_E_kWh')}
    for segment in payload['segments']:
        value=segment.get('diesel_local_response')
        if value is None:continue
        if not isinstance(value,dict):raise ValueError('Diesel local response must be an explicit mapping')
        row=copy.deepcopy(value);row.update(start_minute=float(segment['start_minute']),end_minute=float(segment['end_minute']))
        rows.append(row)
        for name,keys in aliases.items():
            found=next((value[k] for k in keys if value.get(k) is not None),None)
            if found is not None:
                sums[name]+=old._number(found);seen[name]=True
        for name in account_names:
            if value.get(name) is not None:
                accounts[name]+=old._number(value[name]);account_seen[name]=True
        low=value.get('speed_min_pu',value.get('min_speed_pu'))
        high=value.get('speed_max_pu',value.get('max_speed_pu'))
        if low is not None:speeds.append(old._number(low))
        if high is not None:speeds.append(old._number(high))
    prefix={k:v if seen[k] else None for k,v in sums.items()}
    prefix.update({k:v if account_seen[k] else None for k,v in accounts.items()})
    prefix.update(speed_min_pu=min(speeds) if speeds else None,speed_max_pu=max(speeds) if speeds else None)
    if all(seen.values()):
        prefix['recomputed_energy_residual_kWh']=(sums['mechanical_work_kWh']-sums['electrical_work_kWh']
            -sums['loss_work_kWh']-sums['rotor_delta_kWh'])
    else:prefix['recomputed_energy_residual_kWh']=None
    complete=float(payload['summary']['observed_until_minute'])>=float(payload['summary']['requested_end_minute'])-1e-8
    return dict(coverage='observed_local_response_segments' if rows else 'not_supplied',
        segment_count=len(rows),observed_prefix=prefix,registered_full=copy.deepcopy(prefix) if complete and rows else None,
        units='kWh; mechanical/rotor accounting is an explicit research fuel-budget migration, not physical liters',
        energy_identity='mechanical work = electrical work + declared losses + rotor energy change',
        unknown_tail_is_never_filled=True),rows


def analyze_payload(payload,env):
    view,migration=_native_event_view(payload)
    result=old.analyze_payload(view,env)
    result['event_order_migration']=migration
    definitions,first=_extra_windows(payload)
    matrices=[old._actual_matrices(s) for s in payload['segments']]
    gt=result['engineering']['gt'];transitions=result['engineering']['gt_transitions']
    for name,(start,end) in definitions.items():
        if end<=start:continue
        result['windows'][name]=old._window(payload,env,start,end,matrices,gt,transitions)
    result['schema']=SCHEMA
    observed=float(payload['summary']['observed_until_minute']);requested=float(payload['summary']['requested_end_minute'])
    complete=bool(result['windows']['registered_full']['complete'])
    result['model_domain']=dict(status=payload['summary'].get('status'),
        observed_until_minute=observed,requested_end_minute=requested,
        registered_full_known=complete,termination=copy.deepcopy(payload.get('error')),
        unknown_tail_minutes=[observed,requested] if not complete else None,
        original_service_thresholds_unchanged=True,new_dynamic_domain_is_separately_classified=True)
    result['matching_semantics']=dict(initial_reset_must_be_matched=True,
        policy_dependent_prefix_states_possible=first is not None and first>0.,
        accident_injection_does_not_reset_state=True,
        initial_reset_hash_does_not_prove_injection_state_equality=True,
        all_normal_prefix_energy_and_cost_retained=True)
    baseline=payload.get('p3_baseline_receipt')
    result['thermal_coverage']=dict(P3=dict(status='baseline_only' if baseline else 'not_modeled',
        disturbed_temperature_C=None,temperature_violation=None,
        meaning='Original P3 false flag is not evidence of temperature safety; evaluate actual P3 cold service separately',
        baseline_receipt=copy.deepcopy(baseline)))
    diesel,rows=_diesel(payload);result['diesel_local_response']=diesel
    result['timelines']['diesel_local_response']=rows
    for source,target in zip(payload['segments'],result['timelines']['actual_intervals']):
        target['diesel_local_response']=copy.deepcopy(source.get('diesel_local_response'))
        target['P3_disturbed_temperature_C']=None
        target['P3_temperature_violation']=None
    result['event_clock']=dict(first_event_minute=first,source_start_minute=(int(payload.get('scenario',{}).get('start_hour',1))-1)*60.,
        original_primary_and_full_remain_episode_clock=True)
    result['definitions'].update(registered_full='Actual requested [0,end), distinct from legacy full600 and long1440',
        event_windows='Named episode-clock intervals; no relabeling of original nineteen scores',
        P3='Cold ENS measured; disturbed room temperature is null unless a separately declared model supplies it')
    return json_clean(result)


def export_rows(records,out):
    records=json_clean(copy.deepcopy(list(records)));destination=Path(out)
    if destination.resolve().drive.lower()=='c:':raise ValueError('Use D/E project output workspace')
    tables={name:[] for name in ('TRAJECTORY_WINDOWS','GT_RUNS','GT_TRANSITIONS',
        'COMMAND_LIFECYCLE','COMMAND_EXECUTION_INTERVALS','EVENT_TIMELINE','ENGINEERING_INTERVALS',
        'DIESEL_LOCAL_RESPONSE','THERMAL_COVERAGE','MODEL_DOMAIN')}
    unknown_full=0;unknown_registered=0
    for record in records:
        analysis=record.get('analysis',record)
        identity={k:v for k,v in record.items() if k!='analysis' and not isinstance(v,(dict,list))}
        identity.update({k:analysis.get(k) for k in ('scenario_id','analysis_class','source_day_index','execution_model_id')})
        identity.update(analysis.get('identity',{}))
        unknown_full+=not analysis['windows']['full']['complete']
        unknown_registered+=not analysis['windows']['registered_full']['complete']
        for name,window in analysis['windows'].items():
            row=dict(identity,window=name,complete=window['complete'],observed_minutes=window['observed_minutes'],
                window_minutes=window['window_minutes'],all_registered_json=window['all_registered'],
                observed_prefix_json=window['observed_prefix'])
            original=window['original_19'];row.update(dict(zip(original['target_names'],original['targets'])))
            row['target_mask_json']=original['target_mask'];tables['TRAJECTORY_WINDOWS'].append(row)
        for device,state in analysis['engineering']['gt'].items():
            tables['GT_RUNS'].extend(dict(identity,device=device,**run) for run in state['runs'])
        tables['GT_TRANSITIONS'].extend(dict(identity,**row) for row in analysis['engineering']['gt_transitions'])
        for key,name in (('command_lifecycle','COMMAND_LIFECYCLE'),('execution_intervals','COMMAND_EXECUTION_INTERVALS'),
                ('events','EVENT_TIMELINE'),('actual_intervals','ENGINEERING_INTERVALS'),('diesel_local_response','DIESEL_LOCAL_RESPONSE')):
            tables[name].extend(dict(identity,**row) for row in analysis['timelines'][key])
        tables['THERMAL_COVERAGE'].extend(dict(identity,priority=priority,**row) for priority,row in analysis['thermal_coverage'].items())
        tables['MODEL_DOMAIN'].append(dict(identity,**analysis['model_domain']))
    names=[*(name+'.csv' for name in tables),'RESULTS.json','ANALYSIS_RECEIPT.json']
    if any((destination/name).exists() for name in names):raise FileExistsError('Keep previous analysis immutable; use fresh output')
    destination.mkdir(parents=True,exist_ok=True)
    for name,rows in tables.items():old._write_csv(destination/(name+'.csv'),rows)
    with (destination/'RESULTS.json').open('x',encoding='utf8') as stream:
        json.dump(dict(schema=SCHEMA,records=records),stream,ensure_ascii=False,indent=2,allow_nan=False)
    sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
    receipt=dict(schema=SCHEMA+'.receipt',registered_trajectory_count=len(records),
        unknown_full_trajectory_count=unknown_full,unknown_registered_full_trajectory_count=unknown_registered,
        table_row_counts={k:len(v) for k,v in tables.items()},
        output_sha256={name:sha(destination/name) for name in names if name!='ANALYSIS_RECEIPT.json'},
        analysis_program_sha256=sha(Path(__file__)),legacy_analysis_sha256=sha(Path(old.__file__)),
        physical_episodes_started=0,additional_raw_opens=0,old_scores_unchanged=True,
        no_figures_generated=True,P3_disturbed_temperature_coverage=False)
    with (destination/'ANALYSIS_RECEIPT.json').open('x',encoding='utf8') as stream:
        json.dump(receipt,stream,ensure_ascii=False,indent=2,allow_nan=False)
    return receipt
