"""Read compact registered results; never simulate or read raw/annual values."""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import csv
import hashlib
import json
import math
import itertools
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
RUN=ROOT/'outputs/v20_load_accounting_20261019_run01'
OLD_REL='outputs/v19_model_closeout_20261018_run01'
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
PRIORITIES=('P0','P1','P2','P3')
CARRIERS=('e','h','c')
SERVICE_FIELDS=('requested_kWh','delivered_kWh','ENS_kWh','disconnected_ENS_kWh','connected_unserved_ENS_kWh')
OLD_IDS=dict(E='endurance_restore_p0_ready_v14r6',K1='readiness_handoff_K1_v15',
             C19='C19_critical_reserve_v19r1',C18_M20='C18_service_rich_M19compat')
OLD_PREFIX=dict(main='main_final_r1_',long='long_final_r1_',fixed8='confirmation_final_r1_',
    propagation='propagation_final_r1_',open1='sensitivity_open1_r1_',contact025='sensitivity_contact025_r1_')
ECON_FIELDS=('actual_energy_fee_USD','accounting_cost_USD','GT_gas_MMBtu','boiler_gas_MMBtu',
    'natural_gas_total_MMBtu','grid_purchase_kWh','grid_fee_USD','GT_gas_fee_USD','boiler_gas_fee_USD',
    'DG_electric_work_kWh','DG_shaft_work_kWh','DG_loss_work_kWh','DG_rotor_delta_kWh',
    'DG_fuel_budget_debit_kWh_equivalent','DG_original_electric_fee_USD','DG_shaft_equivalent_fee_USD')


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def finite(value):
    if value is None:return None
    value=float(value)
    if not math.isfinite(value):raise ValueError('Nonfinite saved report metric')
    return value


def union_minutes(runs,start,end):
    start,end=finite(start),finite(end)
    if end<start:raise ValueError('Reversed report window')
    intervals=[]
    for run in runs:
        a,b=finite(run['start_minute']),finite(run['end_minute'])
        if b<a:raise ValueError('Reversed material interval')
        a,b=max(start,a),min(end,b)
        if b>a:intervals.append((a,b))
    merged=[]
    for a,b in sorted(intervals):
        if merged and a<=merged[-1][1]+1e-8:merged[-1][1]=max(merged[-1][1],b)
        else:merged.append([a,b])
    return sum(b-a for a,b in merged)


def window_metrics(record,name,bounds,*,expected_model='M20_load_accounting'):
    """Full values require a complete saved window; prefix values stay explicit."""
    analysis=(record or {}).get('analysis') or {}
    window=analysis.get('v18',{}).get('windows',{}).get(name) or {}
    ledger=analysis.get('v20',{}).get('service_ledgers',{}).get(name) or {}
    identity_ok=(record or {}).get('model_id')==expected_model and analysis.get('execution_model_id')==expected_model
    same_window=window.get('window_minutes')==list(bounds)
    complete=bool(identity_ok and same_window and window.get('complete'))
    prefix=window.get('observed_prefix') or {} if same_window and identity_ok else {}
    carriers=prefix.get('carrier_service') or {}
    ledger_identity_ok=identity_ok and ledger.get('window_minutes')==list(bounds)
    ledgers=ledger.get('observed_prefix') or {} if ledger_identity_ok else {}
    ledger_complete=bool(ledger.get('complete') and ledgers)
    row=dict(window=name,window_start_minute=float(bounds[0]),window_end_minute=float(bounds[1]),
        complete=complete,metric_identity_valid=identity_ok,window_identity_valid=same_window,
        observed_until_minute=analysis.get('observed_until_minute'),
        unknown_tail_minutes=window.get('unknown_tail_minutes') if window else list(bounds),
        requested_delivery_scope='exact_saved_segment_ledger' if ledgers else 'unknown; no saved request/delivery ledger',
        observed_value_scope='complete_window' if complete else 'observed_prefix_only' if window else 'unavailable')
    for p in PRIORITIES:
        values=carriers.get(p) or {}
        all_runs=[v['loss_runs'] for v in values.values() if isinstance(v.get('loss_runs'),list)]
        union=union_minutes([run for runs in all_runs for run in runs],*bounds) if len(all_runs)==3 else None
        row[p+'_material_union_minutes']=union if complete else None
        row['observed_'+p+'_material_union_minutes']=union
        for c in CARRIERS:
            legacy=values.get(c) or {};saved=ledgers.get(p,{}).get(c) or {}
            for field in SERVICE_FIELDS:
                value=saved.get(field) if field in ('requested_kWh','delivered_kWh') else legacy.get(field)
                full_known=complete and (ledger_complete or field not in ('requested_kWh','delivered_kWh'))
                row[p+'_'+c+'_'+field]=finite(value) if full_known else None
                row['observed_'+p+'_'+c+'_'+field]=finite(value)
            value=finite(legacy.get('material_minutes'))
            row[p+'_'+c+'_material_minutes']=value if complete else None
            row['observed_'+p+'_'+c+'_material_minutes']=value
        temp=prefix.get('temperature',{}).get(p) or {}
        row[p+'_temperature_violation_minutes']=finite(temp.get('violation_minutes')) if complete else None
        row[p+'_temperature_coverage']=temp.get('coverage','unknown')
    economics=prefix.get('economics') or {}
    for field in ECON_FIELDS:
        value=finite(economics.get(field))
        row[field]=value if complete else None
        row['observed_'+field]=value
    row['DG_fuel_budget_debit_shaft_equivalent_kWh']=row['DG_fuel_budget_debit_kWh_equivalent']
    row['partial_DG_receipt_not_interpolated']=economics.get('partial_DG_receipt_not_interpolated')
    for field in ('DG_budget_start_kWh_equivalent','DG_budget_finish_kWh_equivalent'):
        row[field]=finite(ledger.get(field)) if ledger_identity_ok else None
    row['observed_DG_exhaustion_minutes']=ledger.get('observed_DG_exhaustion_minutes') if ledger_identity_ok else None
    row['actual_source_integrals']=ledger.get('actual_source_integrals') or {} if ledger_identity_ok else {}
    return row


def _journal(path):
    rows=[];issues=[]
    if not path.is_file():return rows,issues
    for i,line in enumerate(path.read_text(encoding='utf8').splitlines(),1):
        if not line.strip():continue
        try:rows.append(json.loads(line))
        except (ValueError,TypeError) as exc:issues.append(dict(path=str(path),line=i,error=str(exc),status='journal_read_unknown'))
    return rows,issues


def _safe_read(path,issues):
    if not path.is_file():return None
    try:return read(path)
    except (OSError,ValueError,TypeError) as exc:
        issues.append(dict(path=str(path),error=str(exc),status='compact_record_read_unknown'))
        return None


def execution_qualification(record,journal,path,*,audit_key,journal_issues=()):
    """Numerical window coverage never substitutes for a valid execution."""
    reasons=[]
    if journal_issues:reasons.append('attempt_journal_contains_unread_records')
    record=record or {};analysis=record.get('analysis') or {}
    if record.get('status')!='completed':reasons.append('physical_status_not_completed')
    if record.get('error'):reasons.append('physical_error_recorded')
    if record.get('analysis_error'):reasons.append('analysis_error_recorded')
    if not analysis:reasons.append('analysis_unavailable')
    if analysis.get('execution_model_id')!=record.get('model_id'):reasons.append('analysis_model_identity_mismatch')
    audit=analysis.get(audit_key,{}).get('episode_audit') or {}
    if audit.get('status')!='passed' or audit.get('issues')!=[]:
        reasons.append('episode_audit_not_explicitly_passed_with_zero_issues')
    own=[r for r in journal if r.get('scenario_id')==record.get('scenario_id') and r.get('policy_id')==record.get('policy_id')]
    starts=[r for r in own if r.get('event')=='started'];finishes=[r for r in own if r.get('event')=='finished']
    if len(starts)!=1:reasons.append('actual_start_event_not_unique')
    elif starts[0].get('model_id')!=record.get('model_id'):reasons.append('actual_start_model_identity_mismatch')
    if len(finishes)!=1:reasons.append('actual_finish_event_not_unique')
    else:
        finish=finishes[0]
        if finish.get('status')!=record.get('status'):reasons.append('finish_status_mismatch')
        if finish.get('analysis_status')!='saved':reasons.append('finish_analysis_not_saved')
        if not path.is_file() or finish.get('result_sha256')!=sha(path):reasons.append('finish_result_sha_mismatch')
        if finish.get('raw_sha256')!=record.get('raw_sha256'):reasons.append('finish_raw_sha_mismatch')
    return not reasons,reasons


def _batch(run,job,issues):
    directory=run/'experiments'/job['name']
    plan=_safe_read(directory/'plan.json',issues)
    manifest=_safe_read(directory/'manifest.json',issues)
    terminal=_safe_read(directory/'BATCH_RESULT.json',issues)
    journal,journal_issues=_journal(directory/'ATTEMPTS.jsonl');issues.extend(journal_issues)
    binding=bool(plan and manifest and plan.get('model_id')=='M20_load_accounting'
        and manifest.get('execution_model_id')=='M20_load_accounting'
        and plan.get('manifest_sha256')==sha(directory/'manifest.json')
        and plan.get('manifest_signature')==manifest.get('signature'))
    bound_terminal=bool(binding and terminal and terminal.get('source_unchanged') is True
        and terminal.get('manifest_sha256')==sha(directory/'manifest.json')
        and terminal.get('plan_sha256')==sha(directory/'plan.json'))
    return directory,plan or {},manifest or {},terminal or {},journal,binding,bound_terminal,journal_issues


def _windows(scene,cohort):
    end=float(scene['duration_steps'])*float(scene.get('step_minutes',15.))
    primary='full' if cohort in ('main','fixed8','propagation','open1') else 'registered_full'
    return [(name,[0.,stop],name==primary) for name,stop in (('full',600.),('registered_full',end))]


def _baseline_index(root,jobs,issues):
    if root is None:return {}
    base=Path(root)/OLD_REL/'experiments'
    wanted={(job['cohort'],job['scene']['id'],alias,job['source_proposal']) for job in jobs for alias in job['policies']}
    result=defaultdict(list)
    from tools.execution_recovery.summarize import valid_matching_evidence
    for directory in sorted(base.glob('*')):
        if not directory.is_dir():continue
        cohorts=[cohort for cohort,prefix in OLD_PREFIX.items() if directory.name.startswith(prefix)]
        if directory.name.startswith('normal_'):cohorts=['normal']
        if directory.name.startswith(('compat_final_r1_','compat_long_final_r1_')):cohorts=['compat']
        if not cohorts:continue
        plan=_safe_read(directory/'plan.json',issues);manifest=_safe_read(directory/'manifest.json',issues)
        if not plan or not manifest:continue
        terminal=_safe_read(directory/'BATCH_RESULT.json',issues) or {}
        journal,journal_issues=_journal(directory/'ATTEMPTS.jsonl');issues.extend(journal_issues)
        bound=bool(plan.get('model_id')=='M19_model_closeout' and manifest.get('execution_model_id')=='M19_model_closeout'
            and plan.get('manifest_sha256')==sha(directory/'manifest.json')
            and terminal.get('source_unchanged') is True
            and terminal.get('manifest_sha256')==sha(directory/'manifest.json')
            and terminal.get('plan_sha256')==sha(directory/'plan.json'))
        scenes={s['id']:s for s in manifest.get('scenarios',[])}
        for spec in plan.get('policy_specs',[]):
            aliases=[alias for alias,identity in OLD_IDS.items() if spec.get('policy_id')==identity]
            for scene_id,scene in scenes.items():
                for alias in aliases:
                    for cohort in cohorts:
                        candidates=[key for key in wanted if key[:3]==(cohort,scene_id,alias)]
                        if not candidates:continue
                        record_path=directory/'pairs'/(scene_id+'__'+spec['policy_id'])/'RESULT.json'
                        record=_safe_read(record_path,issues)
                        if not record:continue
                        identity_ok=(record.get('model_id')=='M19_model_closeout' and record.get('scenario_id')==scene_id
                            and record.get('policy_id')==spec['policy_id'] and record.get('manifest_signature')==manifest.get('signature'))
                        matched=valid_matching_evidence(terminal.get('matching_evidence',{}).get(scene_id),len(plan.get('policy_specs',[])))
                        qualified,reasons=execution_qualification(record,journal,record_path,audit_key='v19',journal_issues=journal_issues)
                        binding_inputs=[dict(path=str(directory/n),sha256=sha(directory/n),kind='old_binding_metadata')
                            for n in ('plan.json','manifest.json','BATCH_RESULT.json','ATTEMPTS.jsonl') if (directory/n).is_file()]
                        for key in candidates:result[key].append(dict(record=record,scene=scene,spec=spec,
                            model_config=plan.get('model_config'),bound=bound and identity_ok and matched and qualified,
                            execution_qualified=qualified,disqualification_reasons=reasons,
                            binding_inputs=binding_inputs,
                            path=str(record_path),sha256=sha(record_path)))
    return dict(result)


def _aggregate(windows):
    groups=defaultdict(list)
    for row in windows:
        if not row['is_primary_window']:continue
        for cls in ('ALL',row.get('canonical_class') or 'unknown'):
            groups[(row['cohort'],row['alias'],cls,row['window'],row['window_start_minute'],row['window_end_minute'])].append(row)
    result=[]
    metrics=[*[p+'_material_union_minutes' for p in PRIORITIES],
        *[p+'_'+c+'_'+field for p in PRIORITIES for c in CARRIERS for field in SERVICE_FIELDS],
        *ECON_FIELDS]
    for key,rows in sorted(groups.items(),key=lambda item:str(item[0])):
        cohort,alias,cls,window,start,end=key
        valid=[r for r in rows if r['complete'] and r['comparison_eligible']]
        item=dict(cohort=cohort,alias=alias,canonical_class=cls,window=window,
            window_start_minute=start,window_end_minute=end,expected_pairs=len(rows),
            eligible_complete_pairs=len(valid),unknown_or_ineligible_pairs=len(rows)-len(valid),
            sums_are_complete_matched_windows_only=True)
        for field in metrics:
            values=[r[field] for r in valid if r.get(field) is not None]
            item[field]=sum(values) if len(values)==len(rows) and rows else None
            item[field+'_known_complete_sum']=sum(values) if values else None
            item[field+'_known_complete_count']=len(values)
        result.append(item)
    return result


def collect_campaign(run=RUN,*,jobs=None,baseline_root=None):
    run=Path(run);issues=[]
    if jobs is None:
        from scripts.v20_campaign import campaign_jobs
        jobs=campaign_jobs(run/'provenance/v19_design')
        if len(jobs)!=148 or sum(len(j['policies']) for j in jobs)!=406:
            raise ValueError('V20 planned denominator must be 148 batches / 406 pairs')
    expected_keys=[(j['name'],j['scene']['id'],a) for j in jobs for a in j['policies']]
    if len(set(expected_keys))!=len(expected_keys):raise ValueError('Duplicate planned pair identity')
    baseline=_baseline_index(baseline_root,jobs,issues)
    status=[];windows=[];service=[];sources=[];comparisons=[];inputs=[]
    for candidates in baseline.values():
        for candidate in candidates:
            inputs.extend(candidate['binding_inputs'])
            inputs.append(dict(path=candidate['path'],sha256=candidate['sha256'],kind='old_compact_result'))
    from tools.execution_recovery.summarize import valid_matching_evidence
    for job in jobs:
        directory,plan,manifest,terminal,journal,binding,bound_terminal,journal_issues=_batch(run,job,issues)
        for filename in ('plan.json','manifest.json','ATTEMPTS.jsonl','BATCH_RESULT.json'):
            path=directory/filename
            if path.is_file():inputs.append(dict(path=str(path),sha256=sha(path),kind='new_registration_or_attempt'))
        specs={s.get('display_alias'):s for s in plan.get('policy_specs',[])}
        scenes={s['id']:s for s in manifest.get('scenarios',[])}
        for alias in job['policies']:
            scene=job['scene'];scene_id=scene['id'];spec=specs.get(alias)
            policy_id=spec['policy_id'] if spec else None
            path=directory/'pairs'/(scene_id+'__'+str(policy_id))/'RESULT.json'
            record=_safe_read(path,issues) if policy_id else None
            own=[row for row in journal if row.get('scenario_id')==scene_id and row.get('policy_id')==policy_id]
            starts=[row for row in own if row.get('event')=='started'];finishes=[row for row in own if row.get('event')=='finished']
            registered=bool(binding and spec and scene_id in scenes)
            record_bound=bool(registered and record and record.get('scenario_id')==scene_id
                and record.get('policy_id')==policy_id and record.get('model_id')=='M20_load_accounting'
                and record.get('manifest_signature')==manifest.get('signature'))
            evidence=terminal.get('matching_evidence',{}).get(scene_id)
            qualified,qualification_reasons=execution_qualification(record,journal,path,audit_key='v20',journal_issues=journal_issues)
            eligible=bool(record_bound and bound_terminal and qualified and valid_matching_evidence(evidence,len(specs)))
            state=('not_registered' if not registered else
                'not_started' if not starts and not record else
                'started_without_result' if not record else
                'result_identity_mismatch' if not record_bound else
                'result_saved_without_finish' if not finishes else record.get('status','unknown'))
            context=dict(batch=job['name'],cohort=job['cohort'],source_proposal=job['source_proposal'],
                scenario_id=scene_id,alias=alias,policy_id=policy_id,model_id='M20_load_accounting',
                canonical_class=scene.get('canonical_class'),source_day_index=scene.get('source_day_index'),
                registration_valid=registered,result_bound=record_bound,batch_terminal_bound=bound_terminal,
                execution_qualified=qualified,execution_disqualification_reasons=qualification_reasons,
                comparison_eligible=eligible,status=state,result_path=str(path) if record else None,
                result_sha256=sha(path) if record else None,raw_sha256=(record or {}).get('raw_sha256'),
                raw_opened=False,actual_started=bool(starts),actual_finished=bool(finishes),
                start_event_count=len(starts),finish_event_count=len(finishes),
                physical_scenario_signature=scene.get('physical_scenario_signature'),
                source_binding_sha256=hashlib.sha256(json.dumps(plan.get('source_sha256'),sort_keys=True,separators=(',',':')).encode('utf8')).hexdigest(),
                model_config_sha256=hashlib.sha256(json.dumps(plan.get('model_config'),sort_keys=True,separators=(',',':')).encode('utf8')).hexdigest(),
                runtime_inputs_sha256=(hashlib.sha256(json.dumps(manifest['files'],sort_keys=True,separators=(',',':')).encode('utf8')).hexdigest()
                    if isinstance(manifest.get('files'),list) and manifest['files'] else None),
                policy_panel_sha256=hashlib.sha256(json.dumps(plan.get('policy_specs'),sort_keys=True,separators=(',',':')).encode('utf8')).hexdigest(),
                evaluation_role=manifest.get('evaluation_role'),
                actual_initial_state_sha256=(evidence['initial_state_sha256'][0] if eligible else None),
                actual_exogenous_sha256=(evidence['applied_exogenous_definition_sha256'][0] if eligible else None),
                error=(record or {}).get('error'),analysis_error=(record or {}).get('analysis_error'))
            analysis=(record or {}).get('analysis') or {}
            full_window=analysis.get('v18',{}).get('windows',{}).get('registered_full') or {}
            complete=bool(record_bound and full_window.get('complete'))
            status.append(dict(context,registered_full_complete=complete,unknown_full=not complete,
                episode_audit_status=analysis.get('v20',{}).get('episode_audit',{}).get('status','unavailable')))
            if record:inputs.append(dict(path=str(path),sha256=sha(path),kind='new_compact_result'))
            candidates=baseline.get((job['cohort'],scene_id,alias,job['source_proposal']),[])
            old=candidates[0] if len(candidates)==1 else None
            for name,bounds,primary in _windows(scene,job['cohort']):
                metrics=window_metrics(record,name,bounds)
                current=dict(context,is_primary_window=primary,**metrics)
                windows.append(current)
                for p in PRIORITIES:
                    for c in CARRIERS:
                        service.append(dict(context,window=name,window_start_minute=bounds[0],window_end_minute=bounds[1],
                            is_primary_window=primary,complete=metrics['complete'],priority=p,carrier=c,
                            requested_delivery_scope=metrics['requested_delivery_scope'],
                            **{f:metrics[p+'_'+c+'_'+f] for f in (*SERVICE_FIELDS,'material_minutes')},
                            **{'observed_'+f:metrics['observed_'+p+'_'+c+'_'+f] for f in (*SERVICE_FIELDS,'material_minutes')}))
                for source,values in metrics['actual_source_integrals'].items():
                    sources.append(dict(context,window=name,window_start_minute=bounds[0],window_end_minute=bounds[1],
                        complete=metrics['complete'],source=source,**values))
                if baseline_root is not None:
                    previous=window_metrics(old['record'] if old else None,name,bounds,expected_model='M19_model_closeout')
                    same_physical=bool(old and scene.get('physical_scenario_signature') is not None
                        and old['scene'].get('physical_scenario_signature')==scene.get('physical_scenario_signature'))
                    same_preferences=bool(old and spec and old['spec'].get('config')==spec.get('config'))
                    same_config=bool(old and old.get('model_config')==plan.get('model_config'))
                    compare=bool(old and old['bound'] and same_physical and same_preferences and same_config
                        and eligible and metrics['complete'] and previous['complete'])
                    comp=dict(context,window=name,window_start_minute=bounds[0],window_end_minute=bounds[1],
                        is_primary_window=primary,old_model_id='M19_model_closeout',new_model_id='M20_load_accounting',
                        baseline_candidate_count=len(candidates),old_result_path=old['path'] if old else None,
                        old_result_sha256=old['sha256'] if old else None,
                        old_complete=previous['complete'],new_complete=metrics['complete'],
                        same_physical_specification=same_physical,same_policy_preferences=same_preferences,
                        old_execution_qualified=bool(old and old['execution_qualified']),
                        old_execution_disqualification_reasons=old['disqualification_reasons'] if old else ['missing_or_ambiguous_old_record'],
                        same_physical_model_configuration=same_config,comparison_valid=compare,
                        comparison_scope='Same fixed specification/preferences/window; changed load-accounting model, not same-version reproduction',
                        old_request_delivery_unknown_without_compact_ledger=True)
                    compare_fields=[*[p+'_material_union_minutes' for p in PRIORITIES],
                        *[p+'_'+c+'_'+f for p in PRIORITIES for c in CARRIERS for f in SERVICE_FIELDS],*ECON_FIELDS]
                    for field in compare_fields:
                        a,b=previous.get(field),metrics.get(field)
                        comp['old_'+field]=a;comp['new_'+field]=b
                        comp['delta_'+field]=b-a if compare and a is not None and b is not None else None
                    for version,values in (('old',previous),('new',metrics)):
                        comp[version+'_partial_DG_receipt_not_interpolated']=values.get('partial_DG_receipt_not_interpolated')
                    comparisons.append(comp)
    summary=dict(schema='hospital_ies.v20.compact_campaign_report.v1',
        generated_utc=datetime.now(timezone.utc).isoformat(),expected_batches=len(jobs),expected_pairs=len(expected_keys),
        actual_started_pairs=sum(s['actual_started'] for s in status),actual_finished_pairs=sum(s['actual_finished'] for s in status),
        not_started_pairs=sum(not s['actual_started'] for s in status),
        not_registered_pairs=sum(not s['registration_valid'] for s in status),
        complete_registered_full_pairs=sum(s['registered_full_complete'] for s in status),
        unknown_full_pairs=sum(s['unknown_full'] for s in status),
        eligible_terminal_pairs=sum(s['comparison_eligible'] for s in status),
        execution_qualified_pairs=sum(s['execution_qualified'] for s in status),
        numeric_complete_but_disqualified_pairs=sum(s['registered_full_complete'] and not s['execution_qualified'] for s in status),
        numeric_complete_is_not_execution_qualification=True,
        status_counts=dict(Counter(s['status'] for s in status)),
        cohorts={c:dict(expected_pairs=sum(s['cohort']==c for s in status),
            complete_pairs=sum(s['cohort']==c and s['registered_full_complete'] for s in status)) for c in sorted({s['cohort'] for s in status})},
        new_independent_background_count=0,fixed_eight_backgrounds_are_seen_rechecks=True,original_final12_unopened=True,
        no_raw_opened=True,no_annual_values_read=True,no_physical_episode_started=True,
        unknown_is_not_zero=True,material_union_is_interval_union=True,
        group_totals_require_all_expected_pairs_eligible_and_known=True,
        old_request_delivery_not_reconstructed=True,baseline_root=str(baseline_root) if baseline_root is not None else None,
        baseline_candidates=sum(len(v) for v in baseline.values()),
        baseline_ambiguous_pairs=sum(len(v)>1 for v in baseline.values()),
        issues=issues)
    inputs=list({row['path']:row for row in inputs}.values())
    return dict(summary=summary,status=status,windows=windows,service=service,sources=sources,
        comparisons=comparisons,aggregates=_aggregate(windows),strategy_comparisons=strategy_pairs(windows),inputs=inputs)


def strategy_pairs(windows):
    """Actual-reset matched per-case comparisons, including seven C18 controls."""
    grouped=defaultdict(dict)
    for row in windows:
        key=(row['cohort'],row['scenario_id'],row['window'],row['window_start_minute'],row['window_end_minute'])
        if row['alias'] in grouped[key]:raise ValueError('Ambiguous repeated new policy comparison')
        grouped[key][row['alias']]=row
    pairs=[]
    for key,own in grouped.items():
        for left,right in itertools.combinations(sorted(own),2):pairs.append((own[left],own[right],key[0]))
    for key,own in grouped.items():
        if key[0]!='compat' or 'C18_M20' not in own:continue
        candidate=[row for row in windows if row['cohort'] in ('main','long') and row['alias']=='C19'
            and row['scenario_id']==key[1] and row['window']==key[2]
            and row['window_start_minute']==key[3] and row['window_end_minute']==key[4]]
        if len(candidate)==1:pairs.append((candidate[0],own['C18_M20'],'compat'))
    metrics=[*[p+'_material_union_minutes' for p in PRIORITIES],
        *[p+'_'+c+'_ENS_kWh' for p in PRIORITIES for c in CARRIERS],*ECON_FIELDS]
    output=[]
    for left,right,cohort in pairs:
        keys=('actual_initial_state_sha256','actual_exogenous_sha256','source_binding_sha256','model_config_sha256',
              'runtime_inputs_sha256','physical_scenario_signature')
        matched=all(left.get(k) is not None and left[k]==right.get(k) for k in keys)
        valid=bool(matched and left['complete'] and right['complete'] and left['comparison_eligible'] and right['comparison_eligible'])
        item=dict(cohort=cohort,scenario_id=left['scenario_id'],canonical_class=left['canonical_class'],
            window=left['window'],window_start_minute=left['window_start_minute'],window_end_minute=left['window_end_minute'],
            is_primary_window=right['is_primary_window'] if cohort=='compat' else left['is_primary_window'],
            model_id='M20_load_accounting',left_alias=left['alias'],right_alias=right['alias'],
            left_batch=left['batch'],right_batch=right['batch'],actual_reset_exogenous_source_config_match=matched,
            runtime_inputs_sha256=left.get('runtime_inputs_sha256') if matched else None,
            left_policy_panel_sha256=left.get('policy_panel_sha256'),right_policy_panel_sha256=right.get('policy_panel_sha256'),
            left_evaluation_role=left.get('evaluation_role'),right_evaluation_role=right.get('evaluation_role'),
            comparison_valid=valid,left_complete=left['complete'],right_complete=right['complete'])
        for field in metrics:
            a,b=left.get(field),right.get(field)
            item['left_'+field]=a;item['right_'+field]=b
            item['delta_right_minus_left_'+field]=b-a if valid and a is not None and b is not None else None
        output.append(item)
    return output


def _emissions(windows,registry):
    from tools.model_closeout.emissions import evaluate_emissions
    output=[]
    if not registry:return output
    for row in windows:
        for key,case in registry['cases'].items():
            canonical=json.dumps(case,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)
            if hashlib.sha256(canonical.encode('utf8')).hexdigest()!=registry['case_canonical_sha256'][key]:
                raise ValueError('Changed emissions factor registry')
            try:value=evaluate_emissions([row],case)[0]
            except (ValueError,TypeError) as exc:
                value=dict(row,emissions_factor_case_id=key,total_CO2_kg_conditional=None,emissions_error=str(exc))
            output.append(value)
    return output


def comparison_emissions(comparisons,registry):
    """Evaluate both saved versions with the same registered factor cases."""
    if not registry:return []
    from tools.model_closeout.emissions import evaluate_emissions
    output=[]
    metrics=('grid_CO2_kg','natural_gas_CO2_kg','grid_plus_gas_CO2_kg','DG_CO2_kg_conditional','total_CO2_kg_conditional')
    for row in comparisons:
        for key,case in registry['cases'].items():
            item={k:row[k] for k in ('cohort','scenario_id','alias','canonical_class','window','window_start_minute',
                'window_end_minute','is_primary_window','old_model_id','new_model_id','comparison_valid')}
            item['factor_case_id']=key;item['factor_case_sha256']=registry['case_canonical_sha256'][key]
            item['diesel_CO2_is_conditional']=True
            results={}
            for version in ('old','new'):
                values={field:row.get(version+'_'+field) for field in ECON_FIELDS}
                values['DG_fuel_budget_debit_shaft_equivalent_kWh']=values.get('DG_fuel_budget_debit_kWh_equivalent')
                values['partial_DG_receipt_not_interpolated']=row.get(version+'_partial_DG_receipt_not_interpolated')
                try:results[version]=evaluate_emissions([values],case)[0]
                except (ValueError,TypeError) as exc:
                    results[version]={};item[version+'_emission_error']=str(exc)
            for field in metrics:
                a,b=results['old'].get(field),results['new'].get(field)
                item['old_'+field]=a;item['new_'+field]=b
                item['delta_'+field]=b-a if row['comparison_valid'] and a is not None and b is not None else None
            output.append(item)
    return output


def csv_new(path,rows):
    fields=list(dict.fromkeys(key for row in rows for key in row)) or ['no_rows']
    with path.open('x',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader()
        for row in rows:
            writer.writerow({k:json.dumps(v,ensure_ascii=False,allow_nan=False) if isinstance(v,(dict,list)) else v for k,v in row.items()})


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label',required=True)
    parser.add_argument('--baseline-root',type=Path)
    parser.add_argument('--run-dir',type=Path,default=RUN)
    a=parser.parse_args(argv)
    if not a.label.replace('_','').isalnum():raise ValueError('Fresh simple report label required')
    out=a.run_dir/'paper_results'/a.label
    if out.exists():raise FileExistsError('Reports/checkpoints are immutable; use a fresh label')
    results=collect_campaign(a.run_dir,baseline_root=a.baseline_root)
    registry_path=a.run_dir/'references/EMISSIONS_FACTOR_REGISTRY.json'
    registry=read(registry_path) if registry_path.is_file() else None
    results['emissions']=_emissions(results['windows'],registry)
    results['comparison_emissions']=comparison_emissions(results['comparisons'],registry)
    results['summary']['emissions_registry_sha256']=sha(registry_path) if registry_path.is_file() else None
    results['summary']['operating_CO2_is_conditional_on_registered_diesel_consumption']=True
    out.mkdir(parents=True)
    tables=dict(STATUS='status',WINDOWS='windows',SERVICE='service',ACTUAL_SOURCES='sources',
        V19_V20_COMPARISON='comparisons',V19_V20_EMISSIONS='comparison_emissions',
        STRATEGY_COMPARISONS='strategy_comparisons',AGGREGATES='aggregates',EMISSIONS='emissions')
    for name,key in tables.items():csv_new(out/(name+'.csv'),results[key])
    for name,key in (('SUMMARY','summary'),('INPUT_INDEX','inputs')):
        with (out/(name+'.json')).open('x',encoding='utf8') as f:json.dump(results[key],f,ensure_ascii=False,indent=2,allow_nan=False)
    receipt=dict(schema='hospital_ies.v20.report_receipt.v1',script_sha256=sha(Path(__file__)),
        output_files={p.name:sha(p) for p in out.iterdir() if p.is_file()},
        row_counts={name:len(results[key]) for name,key in tables.items()},raw_opened=False,annual_values_read=False)
    with (out/'RECEIPT.json').open('x',encoding='utf8') as f:json.dump(receipt,f,ensure_ascii=False,indent=2)
    print(json.dumps(dict(output_directory=str(out),**results['summary']),ensure_ascii=True))
    return 0


if __name__=='__main__':raise SystemExit(main())
