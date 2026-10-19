"""Read compact RESULT/allowlisted CSV only; never open raw or annual inputs.

External diagnostic, outside the frozen runtime. Outputs are immutable snapshots.
No simulation-module imports, physical replay, threshold changes or imputation.
"""
from __future__ import annotations
import argparse
from collections import defaultdict
import copy
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys

SCRIPT = Path(__file__).resolve()
RUN = SCRIPT.parents[1]
MODEL = 'M19_model_closeout'
FOCUS = ('dev_long_r2_000', 'dev_long_r3_000', 'dev_contact_r1_000')
POLICY = {'E':'endurance_restore_p0_ready_v14r6', 'K1':'readiness_handoff_K1_v15',
          'C19':'C19_critical_reserve_v19r1', 'C18_M19':'C18_service_rich_M19compat'}
CSV_NAMES = ('ENGINEERING_INTERVALS.csv', 'EVENT_TIMELINE.csv', 'COMMAND_LIFECYCLE.csv')
JSON_NAMES = {'plan.json', 'manifest.json', 'BATCH_RESULT.json', 'RESULT.json'}
EVENT_KINDS = {'diesel_state_changed','diesel_fuel_exhausted','grid_bus_state_changed',
    'grid_open_bridge_probe','grid_open_bridge_wait','grid_open_bridge_fallback',
    'grid_return_readiness_proved','gt_start_requested','gt_start_process_begun',
    'gt_start_completed','gt_actual_started','gt_actual_stopped','gt_pending_start_cancelled_by_arrived_stop',
    'local_down_requested','local_down_completed','exogenous_fault_start','exogenous_fault_end',
    'exogenous_shock_start','exogenous_shock_end','no_export_interlock','protection_trip',
    'local_contact_restore_scheduled','local_contact_restore_completed','load_connection_restored'}
EVENT_KINDS |= {'local_real_load_restore_requested','local_real_load_restored','local_real_load_restore_rejected',
    'algebraic_interlock_latched','algebraic_interlock_reset','disconnected_port_demand_changed'}
SCHEMA = 'hospital_ies.v19.external_compact_trajectory_diagnostics.v1'


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=True,separators=(',',':'),
        allow_nan=False).encode('utf-8')).hexdigest()


def val(text):
    if text is None or text == '':return None
    try:return json.loads(text)
    except (ValueError,TypeError):return text


def number(value):
    try:
        x=float(value)
        return x if math.isfinite(x) else None
    except (TypeError,ValueError):return None


def write_json(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf-8') as f:json.dump(value,f,ensure_ascii=False,indent=2,allow_nan=False)


def write_csv(path,rows):
    fields=sorted({key for row in rows for key in row})
    with path.open('x',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader()
        for row in rows:writer.writerow({k:json.dumps(v,ensure_ascii=False,allow_nan=False)
            if isinstance(v,(dict,list)) else v for k,v in row.items()})


class CompactReader:
    def __init__(self):self.inputs={};self.unstable=[]
    def bytes(self,path):
        path=path.resolve()
        if not path.is_relative_to((RUN/'experiments').resolve()):raise ValueError('Input outside canonical experiments')
        if path.suffix.lower() not in ('.json','.csv') or (path.suffix=='.json' and path.name not in JSON_NAMES) or (path.suffix=='.csv' and path.name not in CSV_NAMES):
            raise ValueError('Input is not a compact allowlisted RESULT/metadata/CSV')
        before=path.stat();data=path.read_bytes();after=path.stat()
        if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):self.unstable.append(str(path))
        self.inputs[str(path)]=dict(path=path.relative_to(RUN).as_posix(),bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),mtime_ns=after.st_mtime_ns)
        return data
    def json(self,path):return json.loads(self.bytes(path).decode('utf-8-sig'))
    def rows(self,path):
        import io
        csv.field_size_limit(32_000_000)
        return csv.DictReader(io.StringIO(self.bytes(path).decode('utf-8-sig'),newline=''))


def classification(plan,manifest):
    role=manifest.get('evaluation_role');phase=plan.get('phase')
    if role=='independent_confirmation':cohort='independent_confirmation'
    elif role=='development' or str(phase).startswith('dev_'):cohort='development'
    elif manifest.get('freeze_signature'):cohort='frozen_final_'+str(role or 'unknown_role')
    else:cohort='unfrozen_or_unknown_role'
    return dict(cohort=cohort,phase=phase,evaluation_role=role,panel=manifest.get('panel'),
        freeze_signature=manifest.get('freeze_signature'),
        source_binding_sha256=digest(plan.get('source_sha256',{})),
        model_config_sha256=digest(plan.get('model_config',{})))


def physical_scene(scene):
    keys={'start_hour','duration_steps','step_minutes','initialization_seed','automatic_fast_shedding',
        'grid_limit_kW','pre_event_grid_limit_kW','response_delay_minutes','faults','shock','shock_by_priority',
        'exogenous_events','actual_p0_load_multiplier','enable_propagation','full_source_support',
        'gt_initial_age_minutes','diesel_initial_energy_kWh','initial_state','initialization_state'}
    return {k:v for k,v in scene.items() if k in keys or k.startswith(('fault_','shock_'))}


def same_actual_identity(terminal,scene_id):
    e=(terminal or {}).get('matching_evidence',{}).get(scene_id,{})
    result={}
    for field in ('initial_state_sha256','applied_exogenous_definition_sha256'):
        values=e.get(field,[])
        flag='same_initial_state' if field=='initial_state_sha256' else 'same_exogenous_definition'
        result[field]=values[0] if e.get(flag) is True and values and all(v and v==values[0] for v in values) else None
    return result


def extract_result(context,result):
    row=dict(context)
    row.update(status=result.get('status','missing_RESULT'),error=result.get('error'),
        analysis_error=result.get('analysis_error'),raw_sha256=result.get('raw_sha256'))
    analysis=result.get('analysis') or {};window=analysis.get('v18',{}).get('windows',{}).get('registered_full',{})
    prefix=window.get('observed_prefix') or {};complete=bool(window.get('complete'))
    row.update(registered_window_minutes=window.get('window_minutes'),complete_registered_window=complete,
        observed_until_minute=analysis.get('observed_until_minute'),unknown_tail_minutes=window.get('unknown_tail_minutes',analysis.get('unknown_tail_minutes')),
        value_scope='registered_full' if complete else 'observed_prefix' if prefix else 'unavailable',
        audit_status=analysis.get('v19',{}).get('episode_audit',{}).get('status','unavailable'),
        temperature=prefix.get('temperature'),GT_unit_online_hours=prefix.get('GT_unit_online_hours'),
        GT_actual_start_count=prefix.get('GT_actual_start_count'),
        target_device_operation=analysis.get('v18',{}).get('recovery_stages',{}).get('target_device_operation'),
        original_P0_classifier=prefix.get('original_P0_classifier'))
    carriers=[]
    for priority in ('P0','P1','P2','P3'):
        for carrier in ('e','h','c'):
            metrics=prefix.get('carrier_service',{}).get(priority,{}).get(carrier,{})
            fields=('ENS_kWh','disconnected_ENS_kWh','connected_unserved_ENS_kWh','material_minutes',
                'longest_material_minutes','loss_runs','first_clean_minute','confirmed_30min_interval',
                'recurrence_after_30min_count','recovery_status')
            carriers.append(dict(context,priority=priority,carrier=carrier,value_scope=row['value_scope'],
                complete_registered_window=complete,demand_kWh=None,served_kWh=None,served_fraction=None,
                CSV_service_integral_status='not_loaded_or_unknown',**{k:metrics.get(k) for k in fields}))
    economics=dict(context,value_scope=row['value_scope'],complete_registered_window=complete,
        **prefix.get('economics',{}))
    return row,carriers,economics


def csv_bound(row,record):
    return (row.get('scenario_id')==record['scenario_id'] and row.get('policy_id')==record['policy_id']
        and row.get('raw_sha256')==record.get('raw_sha256') and row.get('manifest_signature')==record['manifest_signature']
        and row.get('execution_model_id')==MODEL)


def project_interval(row):
    parsed={k:val(row.get(k)) for k in ('action','arrived_source_setpoints','pre_state','post_state',
        'loads_kW','unserved_kW','disconnected_loads_kW','connected_unserved_kW','availability')}
    action=parsed['action'] or {};arrived=parsed['arrived_source_setpoints'] or {}
    pre=parsed['pre_state'] or {};post=parsed['post_state'] or {}
    out=dict(start_minute=number(row.get('start_minute')),end_minute=number(row.get('end_minute')),
        command_id=val(row.get('command_id')),actual_GRID_kW=action.get('grid_import_kW'),
        actual_DG_kW=action.get('diesel_kW'),actual_GT_kW=action.get('gt_electric_kW'),
        arrived_GRID_permission_kW=arrived.get('grid_import_kW'),arrived_DG_permission_kW=arrived.get('diesel_kW'),
        arrived_GT_target_kW=arrived.get('gt_electric_kW'),DG_status_before=pre.get('diesel_status'),
        DG_status_after=post.get('diesel_status'),DG_shaft_budget_before_kWh=pre.get('diesel_energy_remaining_kWh'),
        DG_shaft_budget_after_kWh=post.get('diesel_energy_remaining_kWh'),
        effective_availability=parsed['availability'],auxiliary_electric_kW=number(row.get('auxiliary_electric_kW')),
        loads_kW=parsed['loads_kW'],unserved_kW=parsed['unserved_kW'],active_cut_kW=parsed['disconnected_loads_kW'],
        connected_unserved_kW=parsed['connected_unserved_kW'],grid_bus_state=None,
        source='ENGINEERING_INTERVALS.csv; availability is effective, not a hardware-only ledger')
    return out


def read_timelines(reader,batch,records):
    bykey={(r['scenario_id'],r['policy_id']):r for r in records};intervals=[];events=[];commands=[];issues=[]
    for name in CSV_NAMES:
        path=batch/'analysis'/name
        if not path.exists():issues.append('missing_'+name);continue
        for row in reader.rows(path):
            if name=='EVENT_TIMELINE.csv' and row.get('kind') not in EVENT_KINDS:continue
            target=bykey.get((row.get('scenario_id'),row.get('policy_id')))
            if target is None:continue
            if not csv_bound(row,target):issues.append('CSV_binding_mismatch:'+name+':'+target['policy_id']);continue
            identity={k:target.get(k) for k in ('batch','scenario_id','policy_id','cohort','phase','source_binding_sha256','model_config_sha256')}
            if name=='ENGINEERING_INTERVALS.csv':intervals.append(dict(identity,**project_interval(row)))
            elif name=='EVENT_TIMELINE.csv':
                keys=('event_id','kind','time_minute','device','before','after','reason','command_id','ready_minute',
                    'grid_close_minute','remaining_budget_kWh','current_actual_GT_kW','diesel_enabled',
                    'preserves_measured_critical_service','candidate_critical_deficit_kW','allowed_critical_deficit_kW',
                    'actual_candidate_action','restored_kW','requested_kW','rate_kW_per_hour')
                keys += ('arrival_minute','no_energy_sink_during_delay','required_kW','connected_electric_kW',
                    'maximum_real_auxiliary_kW','power_surplus_kW','diesel_arrived_ceiling_kW','diesel_minimum_electric_kW',
                    'hardware_failure_asserted','automatic_restart','latch','before_disconnected_kW','after_disconnected_kW')
                events.append(dict(identity,event_payload_model_id=row.get('model_id'),
                    execution_model_id=row.get('execution_model_id'),
                    **{k:val(row.get(k)) for k in keys if row.get(k) not in ('',None)}))
            else:
                keys=('command_id','generated_minute','planned_arrival_minute','actual_arrival_minute',
                    'actual_execution_minute','lifecycle_status','eventual_execution_status',
                    'requested_dispatch','executed_dispatch_at_arrival','arrival_revalidation_changed')
                commands.append(dict(identity,**{k:val(row.get(k)) for k in keys}))
    for r in records:
        key=(r['scenario_id'],r['policy_id']);mine=[x for x in intervals if (x['scenario_id'],x['policy_id'])==key]
        bus=sorted([e for e in events if (e['scenario_id'],e['policy_id'])==key and e['kind']=='grid_bus_state_changed'],key=lambda e:e['time_minute'])
        for segment in mine:
            state=bus[0].get('before') if bus else None
            for event in bus:
                if event['time_minute']<=segment['start_minute']+1e-9:state=event.get('after')
            segment['grid_bus_state']=state
        r['timeline_status']='available' if mine else 'unknown_missing_bound_CSV'
        r['timeline_interval_count']=len(mine)
        cursor=0.;holes=[]
        for segment in sorted(mine,key=lambda x:x['start_minute']):
            if abs(segment['start_minute']-cursor)>1e-7:holes.append([cursor,segment['start_minute']])
            cursor=segment['end_minute']
        if mine and r.get('observed_until_minute') is not None and abs(cursor-r['observed_until_minute'])>1e-7:
            holes.append([cursor,r['observed_until_minute']])
        r['CSV_coverage_holes_or_overlaps_minutes']=holes
        if holes:r['timeline_status']='unknown_CSV_coverage_hole_or_overlap'
        r['OPEN_intervals_minutes']=[[x['start_minute'],x['end_minute']] for x in mine if x['grid_bus_state']=='TRANSFER_OPEN'] if mine else None
        r['OPEN_minutes']=sum(b-a for a,b in r['OPEN_intervals_minutes']) if mine and bus else None
        r['DG_first_positive_interval_start_minute']=next((x['start_minute'] for x in sorted(mine,key=lambda x:x['start_minute']) if (number(x['actual_DG_kW']) or 0)>1e-4),None)
        r['DG_exhaustion_event_minutes']=[e['time_minute'] for e in events if (e['scenario_id'],e['policy_id'])==key and e['kind']=='diesel_fuel_exhausted'] if mine else None
        r['key_event_counts']={kind:sum(e['kind']==kind and (e['scenario_id'],e['policy_id'])==key for e in events) for kind in ('grid_open_bridge_wait','grid_open_bridge_fallback','gt_actual_started')}
        totals={f'P{i}':{c:dict(demand_kWh=0.,delivered_kWh=0.,ENS_kWh=0.) for c in ('e','h','c')} for i in range(4)} if mine else None
        if mine:
            for segment in mine:
                hours=(segment['end_minute']-segment['start_minute'])/60
                for i in range(4):
                    for k,c in enumerate(('e','h','c')):
                        d=segment['loads_kW'][i][k];u=segment['unserved_kW'][i][k];t=totals[f'P{i}'][c]
                        t['demand_kWh']+=hours*d;t['ENS_kWh']+=hours*u;t['delivered_kWh']+=hours*(d-u)
            for bycarrier in totals.values():
                for t in bycarrier.values():t['delivered_fraction']=t['delivered_kWh']/t['demand_kWh'] if t['demand_kWh']>0 else None
        r['CSV_observed_service_integrals']=totals
    return intervals,events,commands,sorted(set(issues))


def compare(left,right,*,contact=False):
    reasons=[]
    for key in ('source_binding_sha256','scene_physical_sha256','input_files_sha256',
                'actual_initial_state_sha256','actual_applied_exogenous_definition_sha256'):
        if not left.get(key) or not right.get(key) or left[key]!=right[key]:reasons.append(key+'_unknown_or_different')
    if left.get('cohort')!=right.get('cohort'):reasons.append('different_cohorts')
    if contact:
        a=copy.deepcopy(left['model_config']);b=copy.deepcopy(right['model_config'])
        da=a.get('execution_recovery',{}).pop('local_contact_restore_delay_minutes',None)
        db=b.get('execution_recovery',{}).pop('local_contact_restore_delay_minutes',None)
        if a!=b or da!=0. or db!=.25:reasons.append('not_exact_contact0_to_025_only')
        if left['policy_id']!=right['policy_id']:reasons.append('contact_policy_mismatch')
    elif left.get('model_config_sha256')!=right.get('model_config_sha256'):reasons.append('different_model_config')
    for r in (left,right):
        if r.get('status')!='completed' or not r.get('complete_registered_window') or r.get('audit_status')!='passed' or r.get('error') or r.get('analysis_error') or not r.get('terminal_source_unchanged') or r.get('binding_issue'):reasons.append('not_complete_audited_terminal')
    if left.get('registered_window_minutes')!=right.get('registered_window_minutes'):reasons.append('different_windows')
    return dict(left=dict(batch=left['batch'],scenario_id=left['scenario_id'],policy_id=left['policy_id']),
        right=dict(batch=right['batch'],scenario_id=right['scenario_id'],policy_id=right['policy_id']),
        status='matched' if not reasons else 'unknown_not_comparable',reasons=sorted(set(reasons)),
        comparison_type='registered_contact_clock_sensitivity' if contact else 'same_scenario_policy_comparison',
        physical_unavoidability_claim=False)


def report_template():
    return ('# Compact trajectory diagnostic snapshot\n\n'
        'Scope: candidate read-only diagnostics, not a final completion report.\n\n'
        '1. Separate development epochs and frozen final cohorts by source/config bindings.\n'
        '2. Report P0/P1 e/h/c ENS, active-cut versus connected-unserved, material runs and recurrence.\n'
        '3. Report P2/P3 service and the lower-priority cost of preserving critical service.\n'
        '4. Keep DG shaft work, electrical work, losses, original fee and shaft-equivalent fee distinct.\n'
        '5. Distinguish command generation, arrival, positive actual output, fuel exhaustion, GRID PREP/OPEN and service recovery.\n'
        '6. long000 versus long005 is descriptive: different scene/background. Contact0/.25 differences require matched actual initialization/stream/source and otherwise identical model config.\n'
        '7. Missing CSV, unfinished results, analysis errors and unobserved tails remain unknown. No raw or annual reads; no physical necessity claim.\n')


def run_snapshot(args,reader):
    canonical=RUN/'experiments'
    names=sorted(p.name for p in canonical.iterdir() if p.is_dir()) if args.all_results else list(dict.fromkeys(args.batch or FOCUS))
    records=[];carriers=[];economics=[];intervals=[];events=[];commands=[];issues=[];batches=[]
    for name in names:
        batch=(canonical/name).resolve()
        if batch.parent!=canonical.resolve():raise ValueError('Batch must be one canonical directory name')
        if not (batch/'plan.json').exists() or not (batch/'manifest.json').exists():issues.append(dict(batch=name,issue='missing_registration'));continue
        plan=reader.json(batch/'plan.json');manifest=reader.json(batch/'manifest.json')
        terminal=reader.json(batch/'BATCH_RESULT.json') if (batch/'BATCH_RESULT.json').exists() else None
        plan_sha=reader.inputs[str((batch/'plan.json').resolve())]['sha256']
        manifest_sha=reader.inputs[str((batch/'manifest.json').resolve())]['sha256']
        registration_ok=(plan.get('manifest_sha256')==manifest_sha and plan.get('manifest_signature')==manifest.get('signature')
            and plan.get('model_id')==manifest.get('execution_model_id')==MODEL
            and plan.get('source_sha256')==manifest.get('source_sha256') and bool(plan.get('source_sha256')))
        terminal_ok=bool(terminal and terminal.get('plan_sha256')==plan_sha and terminal.get('manifest_sha256')==manifest_sha)
        if not registration_ok:issues.append(dict(batch=name,issue='registration_identity_or_binding_mismatch'))
        if terminal and not terminal_ok:issues.append(dict(batch=name,issue='terminal_metadata_binding_mismatch'))
        cls=classification(plan,manifest);batch_records=[]
        batches.append(dict(batch=name,**cls,terminal_available=terminal is not None))
        for scene in manifest['scenarios']:
            actual=same_actual_identity(terminal,scene['id'])
            for spec in plan['policy_specs']:
                path=batch/'pairs'/(scene['id']+'__'+spec['policy_id'])/'RESULT.json'
                context=dict(batch=name,**cls,scenario_id=scene['id'],policy_id=spec['policy_id'],model_id=plan.get('model_id'),
                    scene_physical_sha256=digest(physical_scene(scene)),input_files_sha256=digest(manifest.get('files',[])),
                    source_day_index=scene.get('source_day_index'),model_config=plan.get('model_config'),
                    actual_initial_state_sha256=actual['initial_state_sha256'],
                    actual_applied_exogenous_definition_sha256=actual['applied_exogenous_definition_sha256'],
                    manifest_signature=manifest['signature'],terminal_source_unchanged=bool(terminal_ok and registration_ok and terminal.get('source_unchanged')),
                    result_path=path.relative_to(RUN).as_posix(),external_fault_definitions=scene.get('exogenous_events'),
                    requested_window_minutes=[0.,scene['duration_steps']*scene.get('step_minutes',15.)])
                result=reader.json(path) if path.exists() else {}
                row,cs,ec=extract_result(context,result)
                if not registration_ok:row['binding_issue']='registration_identity_or_binding_mismatch'
                if result and (result.get('model_id')!=MODEL or result.get('manifest_signature')!=manifest['signature'] or result.get('scenario_id')!=scene['id'] or result.get('policy_id')!=spec['policy_id']):
                    row['binding_issue']='RESULT_identity_mismatch';issues.append(dict(batch=name,issue=row['binding_issue'],policy_id=spec['policy_id']))
                records.append(row);batch_records.append(row);carriers.extend(cs);economics.append(ec)
        if name in FOCUS or name in (args.timeline_batch or []):
            ins,evs,cmds,problems=read_timelines(reader,batch,batch_records)
            intervals.extend(ins);events.extend(evs);commands.extend(cmds);issues.extend(dict(batch=name,issue=p) for p in problems)
    lookup={(r['batch'],r['scenario_id'],r['policy_id']):r for r in records};comparisons=[]
    for b in names:
        for scene in {r['scenario_id'] for r in records if r['batch']==b}:
            a=lookup.get((b,scene,POLICY['C19']));z=lookup.get((b,scene,POLICY['C18_M19']))
            if a and z:comparisons.append(compare(a,z))
    for policy in POLICY.values():
        a=next((r for r in records if r['batch']=='dev_long_r3_000' and r['policy_id']==policy),None)
        z=next((r for r in records if r['batch']=='dev_contact_r1_000' and r['policy_id']==policy),None)
        if a and z:comparisons.append(compare(a,z,contact=True))
    for pair in comparisons:
        if pair['status']!='matched':pair['deltas']=None;continue
        a=next(r for r in records if all(r[k]==v for k,v in pair['left'].items()));z=next(r for r in records if all(r[k]==v for k,v in pair['right'].items()))
        pair['deltas_right_minus_left']={}
        for group in ('P0','P1','P2','P3'):
            for carrier in ('e','h','c'):
                get=lambda r:next(c for c in carriers if c['batch']==r['batch'] and c['scenario_id']==r['scenario_id'] and c['policy_id']==r['policy_id'] and c['priority']==group and c['carrier']==carrier)
                left,right=get(a),get(z)
                for metric in ('ENS_kWh','material_minutes','demand_kWh','served_kWh'):
                    x,y=number(left.get(metric)),number(right.get(metric));pair['deltas_right_minus_left'][f'{group}_{carrier}_{metric}']=y-x if x is not None and y is not None else None
        for metric in ('DG_shaft_work_kWh','DG_electric_work_kWh','DG_original_electric_fee_USD','DG_shaft_equivalent_fee_USD','actual_energy_fee_USD'):
            get=lambda r:next(e for e in economics if e['batch']==r['batch'] and e['scenario_id']==r['scenario_id'] and e['policy_id']==r['policy_id'])
            x,y=number(get(a).get(metric)),number(get(z).get(metric))
            pair['deltas_right_minus_left'][metric]=y-x if x is not None and y is not None else None
    for r in records:
        totals=r.get('CSV_observed_service_integrals')
        if totals:
            differences=[]
            for c in carriers:
                if c['batch']==r['batch'] and c['scenario_id']==r['scenario_id'] and c['policy_id']==r['policy_id'] and c.get('ENS_kWh') is not None:
                    t=totals[c['priority']][c['carrier']]
                    differences.append(abs(t['ENS_kWh']-c['ENS_kWh']))
                    c.update(demand_kWh=t['demand_kWh'],served_kWh=t['delivered_kWh'],served_fraction=t['delivered_fraction'],
                        CSV_integrated_ENS_kWh=t['ENS_kWh'],CSV_service_integral_status='complete_registered_window' if r['complete_registered_window'] and r.get('timeline_status')=='available' else 'observed_prefix_only',
                        interval_array_roles='loads_kW=original demand; unserved_kW=total unmet incl active cuts; served=loads-unserved; disconnected_loads_kW=actual cuts; connected_unserved_kW=residual connected shortage',
                        interval_integration='sum((end_minute-start_minute)/60 * array[group,carrier]); carriers reported separately; GT endpoints do not identify served loads')
            r['CSV_vs_RESULT_max_ENS_difference_kWh']=max(differences) if differences else None
            if differences and max(differences)>1e-6:issues.append(dict(batch=r['batch'],policy_id=r['policy_id'],issue='CSV_RESULT_ENS_disagreement'))
    for pair in comparisons:
        if pair['status']!='matched':continue
        for group in ('P0','P1','P2','P3'):
            for carrier in ('e','h','c'):
                get=lambda side:next(c for c in carriers if all(c[k]==v for k,v in side.items()) and c['priority']==group and c['carrier']==carrier)
                a,z=get(pair['left']),get(pair['right'])
                for metric in ('demand_kWh','served_kWh'):
                    x,y=number(a.get(metric)),number(z.get(metric))
                    pair['deltas_right_minus_left'][f'{group}_{carrier}_{metric}']=y-x if x is not None and y is not None else None
    focus_complete=not issues and all(any(r['batch']==b for r in records) and all(r['status']=='completed' and r['complete_registered_window'] and r.get('timeline_status')=='available' and r['terminal_source_unchanged'] for r in records if r['batch']==b) for b in FOCUS if b in names)
    if not focus_complete and not args.allow_partial:raise ValueError('Focused terminal/RESULT/CSV not ready; wait, or explicitly --allow-partial to retain unknowns')
    if reader.unstable:raise ValueError('Compact input changed during read: '+str(reader.unstable))
    return dict(schema=SCHEMA,utc=datetime.now(timezone.utc).isoformat(),snapshot_status='all_focus_compact_inputs_available' if focus_complete else 'partial_unknown_retained',
        batches=batches,trajectories=records,carrier_service=carriers,economics=economics,key_events=events,
        timeline_intervals=intervals,commands=commands,comparisons=comparisons,issues=issues,
        raw_files_opened=0,annual_files_opened=0,new_physical_runs=0,thresholds_changed=False,
        missing_fields_are_unknown=True,final_completion_claim=False,
        interpretation_limits=['No across-epoch aggregation.','long000 and long005 differ in background and external disturbances; side-by-side is descriptive.',
            'A positive source or a preserved zero measured service is not full critical service recovery.',
            'Finite budget envelope/arrived permission, actual exhaustion and finite OPEN are separate facts; no physical inevitability asserted.'])


def self_test():
    context=dict(batch='fixture',scenario_id='s',policy_id=POLICY['C19'],source_binding_sha256='a',
        scene_physical_sha256='s',input_files_sha256='i',actual_initial_state_sha256='x',actual_applied_exogenous_definition_sha256='e',
        cohort='development',model_config_sha256='m',model_config={'execution_recovery':{'local_contact_restore_delay_minutes':0.}})
    empty,rows,ec=extract_result(context,{})
    assert empty['value_scope']=='unavailable' and len(rows)==12 and all(r['ENS_kWh'] is None for r in rows)
    a=dict(context,status='completed',complete_registered_window=True,audit_status='passed',terminal_source_unchanged=True,registered_window_minutes=[0,1065])
    b=copy.deepcopy(a);assert compare(a,b)['status']=='matched'
    b['source_binding_sha256']='different';assert compare(a,b)['status']=='unknown_not_comparable'
    b=copy.deepcopy(a);b['actual_initial_state_sha256']=None;assert compare(a,b)['status']=='unknown_not_comparable'
    b=copy.deepcopy(a);b['model_config']['execution_recovery']['local_contact_restore_delay_minutes']=.25
    assert compare(a,b,contact=True)['status']=='matched'
    b['model_config']['extra']=1;assert compare(a,b,contact=True)['status']=='unknown_not_comparable'
    b=copy.deepcopy(a);b['complete_registered_window']=False;assert compare(a,b)['status']=='unknown_not_comparable'
    try:CompactReader().bytes(RUN/'experiments/fixture/trajectory.json.gz')
    except ValueError:pass
    else:raise AssertionError('raw path was not rejected')
    print(json.dumps(dict(self_test='passed',assertion_groups=8,raw_opens=0,physical_runs=0)))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare',action='store_true');parser.add_argument('--snapshot',action='store_true')
    parser.add_argument('--self-test',action='store_true');parser.add_argument('--name')
    parser.add_argument('--all-results',action='store_true');parser.add_argument('--batch',action='append')
    parser.add_argument('--timeline-batch',action='append');parser.add_argument('--allow-partial',action='store_true')
    args=parser.parse_args()
    if args.self_test:return self_test()
    if args.prepare==args.snapshot or not args.name:parser.error('choose --prepare or --snapshot, with --name')
    if Path(args.name).name!=args.name or args.name in ('.','..'):raise ValueError('Name must be one new directory component')
    out=RUN/'review/trajectory_diagnostics'/args.name
    if out.exists():raise FileExistsError(out)
    reader=CompactReader();data=run_snapshot(args,reader) if args.snapshot else None
    out.mkdir(parents=True)
    (out/'REPORT_TEMPLATE.md').write_text(report_template(),encoding='utf-8')
    if data:
        write_json(out/'DIAGNOSTICS.json',data)
        for name,key in [('TRAJECTORIES','trajectories'),('CARRIER_SERVICE','carrier_service'),('ECONOMICS','economics'),
            ('KEY_EVENTS','key_events'),('TIMELINE_INTERVALS','timeline_intervals'),('COMMANDS','commands')]:write_csv(out/(name+'.csv'),data[key])
        write_csv(out/'P2_P3_SERVICE.csv',[r for r in data['carrier_service'] if r['priority'] in ('P2','P3')])
        write_json(out/'COMPARISONS.json',data['comparisons'])
        lines=[report_template(),'\n|batch|policy|scope|status|DG positive|DG exhaustion|OPEN min|\n|---|---|---|---|---|---|---|\n']
        for r in data['trajectories']:
            lines.append('|'+ '|'.join(str(r.get(k)) for k in ('batch','policy_id','value_scope','status','DG_first_positive_interval_start_minute','DG_exhaustion_event_minutes','OPEN_minutes'))+'|\n')
        (out/'REPORT_CANDIDATE.md').write_text(''.join(lines),encoding='utf-8')
        causal=['# Observed focused event/interval timeline\n\nNo new physical replay; no physical inevitability claim.\n']
        for record in data['trajectories']:
            if record['batch'] not in FOCUS:continue
            match=lambda x:x['batch']==record['batch'] and x['scenario_id']==record['scenario_id'] and x['policy_id']==record['policy_id']
            causal.append('\n## '+record['batch']+' / '+record['policy_id']+'\n\nSource '+record['source_binding_sha256']+'; config '+record['model_config_sha256']+'.\n\n')
            causal.append('|priority/carrier|ENS kWh|material min|saved material runs|\n|---|---|---|---|\n')
            for c in data['carrier_service']:
                if match(c) and c['priority'] in ('P0','P1'):causal.append('|'+c['priority']+'/'+c['carrier']+'|'+str(c['ENS_kWh'])+'|'+str(c['material_minutes'])+'|'+json.dumps(c['loss_runs'])+'|\n')
            causal.append('\n|minute|event|device|before to after / reason|\n|---|---|---|---|\n')
            for e in sorted((e for e in data['key_events'] if match(e)),key=lambda x:x['time_minute']):
                if e['kind']=='load_connection_restored':continue
                detail=(str(e.get('before',''))+' -> '+str(e.get('after',''))+' '+str(e.get('reason',''))) if e['kind']!='no_export_interlock' else 'Actual GT stopped by logged no-export interlock; source before/after retained in KEY_EVENTS.csv'
                if e['kind']=='local_real_load_restore_requested':detail='Required '+str(e.get('required_kW'))+' kW; arrival '+str(e.get('arrival_minute'))+'; no energy sink during delay='+str(e.get('no_energy_sink_during_delay'))
                if e['kind']=='local_real_load_restored':detail='Restored actual load by priority kW='+str(e.get('restored_kW'))
                if e['kind']=='no_export_interlock':detail='Connected '+str(e.get('connected_electric_kW'))+' kW; max real auxiliary '+str(e.get('maximum_real_auxiliary_kW'))+'; surplus '+str(e.get('power_surplus_kW'))+'; actual GT stop in adjacent event'
                causal.append('|'+str(e['time_minute'])+'|'+e['kind']+'|'+str(e.get('device',''))+'|'+detail.replace('|','/')+'|\n')
            causal.append('\n|interval min|command|GT actual kW|DG actual / arrived kW|shaft budget before to after kWh|P0/P1 e connected unmet kW|bus|\n|---|---|---|---|---|---|---|\n')
            for x in data['timeline_intervals']:
                if not match(x) or not (30<=x['start_minute']<75 or 449<=x['start_minute']<526 or 750<=x['start_minute']<855):continue
                missing=x.get('connected_unserved_kW') or x.get('unserved_kW')
                causal.append('|'+str([x['start_minute'],x['end_minute']])+'|'+str(x['command_id'])+'|'+str(x['actual_GT_kW'])+'|'+str(x['actual_DG_kW'])+' / '+str(x['arrived_DG_permission_kW'])+'|'+str(x['DG_shaft_budget_before_kWh'])+' -> '+str(x['DG_shaft_budget_after_kWh'])+'|'+str([missing[i][0] for i in (0,1)] if missing else None)+'|'+str(x['grid_bus_state'])+'|\n')
        (out/'FOCUSED_TIMELINE.md').write_text(''.join(causal),encoding='utf-8')
    write_json(out/'INPUT_MANIFEST.json',dict(schema=SCHEMA,script_sha256=hashlib.sha256(SCRIPT.read_bytes()).hexdigest(),
        mode='snapshot' if data else 'prepare_only',inputs=list(reader.inputs.values()),raw_opens=0,annual_opens=0,
        physical_runs=0,immutable_output=True,requested_focus_batches=list(FOCUS),
        pending_execution='Run --snapshot only when terminal compact data/CSV are ready; --allow-partial is explicit and never fills a tail.'))
    print(json.dumps(dict(output=str(out),mode='snapshot' if data else 'prepare_only',raw_opens=0,physical_runs=0)))


if __name__=='__main__':main()
