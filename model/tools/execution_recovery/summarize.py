"""Summarize saved V18 outcomes without replay or opening annual inputs."""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
B = ROOT / 'outputs/v18_execution_recovery_20261018_run01'

def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf8')).hexdigest()

def bind_scene_definition(seen, cohort_key, scene):
    key=(*cohort_key,scene['id']); value=digest(scene)
    if key in seen and seen[key]!=value:
        raise ValueError('Same scenario identity has a different exact registered scene definition')
    seen[key]=value
    return value

def valid_matching_evidence(evidence, expected_count):
    if (not isinstance(evidence,dict) or expected_count<1
        or evidence.get('same_initial_state') is not True
        or evidence.get('same_exogenous_definition') is not True):
        return False
    for key in ('initial_state_sha256','applied_exogenous_definition_sha256'):
        values=evidence.get(key)
        if (not isinstance(values,list) or len(values)!=expected_count or
            not all(isinstance(v,str) and len(v)==64 and all(c in '0123456789abcdef' for c in v) for v in values)
            or len(set(values))!=1):
            return False
    return True

def union_minutes(runs):
    intervals = sorted((float(x['start_minute']),float(x['end_minute'])) for x in runs)
    merged = []
    for a, b in intervals:
        if b < a:
            raise ValueError('Reversed service interval')
        if merged and a <= merged[-1][1]+1e-8:
            merged[-1][1] = max(b, merged[-1][1])
        else:
            merged.append([a,b])
    return sum(b-a for a,b in merged)

def rows(record, batch, result_path, context):
    analysis = record.get('analysis')
    common = dict(batch=batch, scenario_id=record['scenario_id'], policy_id=record['policy_id'],
        source_day_index=record.get('source_day_index'), model_id=record['model_id'], status=record['status'],
        analysis_error=bool(record.get('analysis_error')), result_path=result_path,
        raw_sha256=record.get('raw_sha256'), error_type=(record.get('error') or {}).get('type'),
        **context)
    if not analysis:
        yield dict(common, window='unavailable', complete=False)
        return
    for window, value in analysis['v18']['windows'].items():
        prefix = value['observed_prefix']
        row = dict(common, window=window, complete=value['complete'],
            observed_until_minute=analysis['observed_until_minute'],
            window_start_minute=value['window_minutes'][0],window_end_minute=value['window_minutes'][1],
            unknown_tail_minutes=value['unknown_tail_minutes'],
            canonical_class=analysis['v18']['canonical_class'],
            original_P0_classifier_failure=prefix['original_P0_classifier']['P0_failure_in_window'])
        for p, carriers in prefix['carrier_service'].items():
            row[p+'_material_union_minutes'] = union_minutes([run for v in carriers.values() for run in v['loss_runs']])
            for c, v in carriers.items():
                row[p+'_'+c+'_ENS_kWh'] = v['ENS_kWh']
                row[p+'_'+c+'_material_minutes'] = v['material_minutes']
        for p, temperature in prefix['temperature'].items():
            row[p+'_temperature_violation_minutes'] = temperature['violation_minutes']
        row.update(prefix['economics'])
        yield row

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--label', required=True)
    p.add_argument('--batch-glob', default='*')
    a = p.parse_args()
    out = B/'review'/('summary_'+a.label)
    out.mkdir(parents=True, exist_ok=False)
    allrows = []; records = []; expected_policies = {}; scene_definitions={}
    for directory in sorted((B/'experiments').glob(a.batch_glob)):
        plan_path=directory/'plan.json';manifest_path=directory/'manifest.json'
        plan = json.loads(plan_path.read_text(encoding='utf8'))
        manifest=json.loads(manifest_path.read_text(encoding='utf8'))
        manifest_sha=hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        if manifest_sha!=plan['manifest_sha256']:
            raise ValueError('Changed registered manifest before saved comparison')
        scenes={s['id']:s for s in manifest['scenarios']}
        batch_path=directory/'BATCH_RESULT.json'
        batch=json.loads(batch_path.read_text(encoding='utf8')) if batch_path.is_file() else {}
        batch_bound=(batch.get('source_unchanged') is True and batch.get('manifest_sha256')==manifest_sha
            and batch.get('plan_sha256')==hashlib.sha256(plan_path.read_bytes()).hexdigest())
        binding = hashlib.sha256(json.dumps(plan['source_sha256'],sort_keys=True,separators=(',',':')).encode('utf8')).hexdigest()
        config = hashlib.sha256(json.dumps(plan['model_config'],sort_keys=True,separators=(',',':')).encode('utf8')).hexdigest()
        context = dict(source_binding_sha256=binding, model_config_sha256=config)
        key = (directory.name.rsplit('_',1)[0],binding,config)
        policies = {s['policy_id'] for s in plan['policy_specs']}
        if key in expected_policies and expected_policies[key] != policies:
            raise ValueError('Different policy panels share a cohort binding; use distinct cohort names')
        expected_policies[key] = policies
        for path in sorted((directory/'pairs').glob('*/RESULT.json')):
            record = json.loads(path.read_text(encoding='utf8'))
            if (record['policy_id'] not in policies or record['model_id']!=plan['model_id']
                    or record['scenario_id'] not in scenes or record.get('manifest_signature')!=manifest['signature']):
                raise ValueError('Saved result does not match its registered scene/policy/model')
            scene_sha=bind_scene_definition(scene_definitions,key,scenes[record['scenario_id']])
            evidence=batch.get('matching_evidence',{}).get(record['scenario_id'])
            eligible=batch_bound and valid_matching_evidence(evidence,len(policies))
            current_context=dict(context,registered_scene_sha256=scene_sha,
                comparison_matching_eligible=eligible,
                comparison_matching_reason='bound_actual_reset_and_exogenous_hashes_match' if eligible else
                    'terminal_batch_binding_or_actual_matching_evidence_missing_or_false',
                comparison_key=digest([record['scenario_id'],scene_sha,evidence['initial_state_sha256'][0],
                    evidence['applied_exogenous_definition_sha256'][0]]) if eligible else None)
            allrows.extend(rows(record, directory.name, path.relative_to(ROOT).as_posix(), current_context))
            records.append(dict(batch=directory.name, **{k:record.get(k) for k in
                ('scenario_id','policy_id','model_id','status','error','analysis_error','raw_sha256','raw_path','elapsed_seconds')}))
    fields = list(dict.fromkeys(key for row in allrows for key in row))
    with (out/'RESULT_WINDOWS.csv').open('x', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(allrows)
    groups = defaultdict(list)
    for row in allrows:
        key = (row['batch'].rsplit('_',1)[0],row['model_id'],row['source_binding_sha256'],row['model_config_sha256'],
               row['window'],row.get('window_start_minute'),row.get('window_end_minute'))
        groups[key].append(row)
    aggregates = []
    comparable = []
    for key, values in sorted(groups.items(),key=lambda x:str(x[0])):
        cohort, model, binding, config, window, start, end = key
        policies = expected_policies[(cohort,binding,config)]
        identities = [(v['scenario_id'],v['policy_id']) for v in values if v['complete']]
        if len(identities) != len(set(identities)):
            raise ValueError('Repeated complete specification/policy outcome; split attempt cohorts before comparison')
        sets = [{v['comparison_key'] for v in values if v['policy_id']==policy and v['complete']
                 and v['comparison_matching_eligible']} for policy in policies]
        matched = set.intersection(*sets) if sets else set()
        for policy in sorted(policies):
            own = [v for v in values if v['policy_id']==policy]
            complete = [v for v in own if v['complete'] and v['comparison_matching_eligible'] and v['comparison_key'] in matched]
            comparable.append((cohort,model,binding,config,window,start,end,policy,own,complete,matched))
    for cohort,model,binding,config,window,start,end,policy,values,complete,matched in comparable:
        v = dict(cohort=cohort, policy_id=policy,window=window,window_minutes=[start,end],
            model_id=model,source_binding_sha256=binding,model_config_sha256=config,
            attempts=len(values), own_complete=sum(x['complete'] for x in values),
            unknown=sum(not x['complete'] for x in values), matched_complete=len(complete),
            comparison_ineligible_complete=sum(x['complete'] and not x['comparison_matching_eligible'] for x in values),
            matched_scenario_ids=sorted({x['scenario_id'] for x in complete}),
            matched_comparison_keys=sorted(matched),
            totals_scope='same exact scene/source/model/window plus actual reset/exogenous matching; complete intersection only')
        for priority in ('P0','P1','P2','P3'):
            field=priority+'_material_union_minutes'
            v[priority+'_material_specs'] = sum(x[field]>1e-8 for x in complete) if complete else None
            for field in [field, *[priority+'_'+c+'_ENS_kWh' for c in ('e','h','c')]]:
                v[field] = sum(x[field] for x in complete) if complete else None
            thermal=priority+'_temperature_violation_minutes'
            known = [x[thermal] for x in complete if x[thermal] is not None]
            v[thermal] = sum(known) if known else None
            v[priority+'_temperature_unknown_records'] = sum(x[thermal] is None for x in complete)
        for field in ('original_P0_classifier_failure','actual_energy_fee_USD','natural_gas_total_MMBtu',
                      'boiler_gas_MMBtu','DG_electric_work_kWh','DG_fuel_budget_debit_kWh_equivalent'):
            known = [x[field] for x in complete if x.get(field) is not None]
            v[field] = sum(known) if len(known)==len(complete) and complete else None
            v[field+'_known_sum'] = sum(known) if known else None
            v[field+'_unknown_records'] = len(complete)-len(known)
        aggregates.append(v)
    result = dict(schema='hospital_ies.v18.saved_summary.v1', actual_result_records=len(records),
        status_counts=dict(Counter(r['status'] for r in records)), aggregates=aggregates,
        attempts=records, no_simulation=True, no_annual_input_reads=True,
        old80_seen_regression=True, final12_unopened=True,
        unknown_records_are_not_zero_service=True, costs_not_pooled_across_cohorts=True)
    with (out/'SUMMARY.json').open('x', encoding='utf8') as f:
        json.dump(result,f,ensure_ascii=False,indent=2,allow_nan=False)
    print(json.dumps({k:result[k] for k in ('actual_result_records','status_counts')}))

if __name__ == '__main__':
    main()
