"""Existing saved nineteen-target analysis plus bounded service/switch metrics."""
from __future__ import annotations
from collections import Counter
from functools import partial
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from tools.autonomous_dispatch.analysis import analysis_namespace as original_namespace
from tools.state_dispatch import analysis as original
from .interface import validate_action

def references_for(specs):
    refs=[]
    for spec in specs:
        pid=spec['policy_id'];config=spec.get('config',{})
        if (pid in ('execution_reserve','joint_standby_1p25_h2','priority_milp','secure_milp')
                or spec['kind']=='coordinated' and config.get('coordination_mode')=='fixed'
                and config.get('service_budget_enabled',True) and config.get('handoff_enabled',True)):
            refs.append(pid)
    if not refs:raise ValueError('At least one actual matched reference is required')
    return tuple(refs)

def analysis_namespace(references):
    namespace=original_namespace(validate_action)
    namespace.update(REFERENCES=tuple(references),SCHEMA='coordinated_dispatch.audited_saved_analysis.v1')
    # The old nested loader requires secure even after REFERENCES is rebound.
    # Keep its metric functions, but isolate this one actual-panel entry point.
    deepening=SimpleNamespace(**original.deepening.__dict__)
    deepening._load_audited_batch=partial(_load_audited_batch,
        references=tuple(references),deepening=deepening)
    namespace['deepening']=deepening
    return namespace

def _load_audited_batch(manifest_path,plan_path,results_dir,audit_path,*,references,deepening):
    """Original audited loader checks; actual registered references replace secure.

    The original pair reader retains result/raw SHA, terminal/identity/binding
    and audit-recomputation checks. No policy or saved identity is relabelled.
    """
    previous=original.previous
    manifest_path,plan_path,results_dir,audit_path=(Path(p).resolve()
        for p in (manifest_path,plan_path,results_dir,audit_path))
    manifest,plan,audit=map(previous._read_json,(manifest_path,plan_path,audit_path))
    ids=plan.get('scenario_ids',[])
    policy_ids=[spec['policy_id'] for spec in plan.get('policy_specs',[])]
    expected={(sid,pid) for sid in ids for pid in policy_ids}
    if (audit.get('passed') is not True or audit.get('matched_initial_state_and_stream') is not True
            or audit.get('plan_sha256')!=previous._sha(plan_path)
            or audit.get('manifest_sha256')!=previous._sha(manifest_path)
            or plan.get('manifest_sha256')!=previous._sha(manifest_path)
            or plan.get('manifest_signature')!=manifest.get('signature')
            or plan.get('registered_pair_count')!=len(expected)
            or any(audit.get(key)!=len(expected) for key in
                ('registered_pair_count','terminal_pair_count','recomputed_pair_count'))):
        raise ValueError('Passed raw audit and registered plan/manifest binding required')
    if len(ids)!=len(set(ids)) or len(policy_ids)!=len(set(policy_ids)):
        raise ValueError('Duplicate registered scene or policy')
    if not references or not set(references)<=set(policy_ids):
        raise ValueError('Actual registered matched references required')
    scenes={scene['id']:scene for scene in manifest['scenarios']}
    if not set(ids)<=set(scenes):raise ValueError('Registered scene missing from manifest')
    ledger={}
    for item in audit.get('pair_ledger',[]):
        key=(item.get('scenario_id'),item.get('policy_id'))
        if key in ledger:raise ValueError('Duplicate audited pair')
        ledger[key]=item
    if set(ledger)!=expected:raise ValueError('Audited pair denominator differs from registered plan')
    rows,states,streams,profiles=[],{},{},{}
    for sid in ids:
        scene=scenes[sid]
        if scene.get('is_final_blind') or scene.get('experiment_role')!=plan.get('phase'):
            raise ValueError('Sealed final scene or registered phase mismatch')
        for pid in policy_ids:
            payload,result,raw=previous._read_verified_pair(results_dir,scene,pid,ledger[(sid,pid)])
            states[(sid,pid)]=json.dumps(payload.get('initial_state'),ensure_ascii=False,sort_keys=True)
            streams[(sid,pid)]=payload.get('identity',{}).get('exogenous_stream_id')
            profiles[(sid,pid)]=deepening._compact_profile(payload)
            rows.append(deepening.trajectory_row(scene,pid,payload,result,raw))
    for sid in ids:
        if len({states[(sid,pid)] for pid in policy_ids})!=1 or len({streams[(sid,pid)] for pid in policy_ids})!=1:
            raise ValueError('Matched policies differ in initial state or exogenous stream')
    return dict(manifest=manifest,plan=plan,audit=audit,manifest_path=manifest_path,plan_path=plan_path,
        audit_path=audit_path,policy_ids=policy_ids,scenes=scenes,scenario_ids=ids,row_list=rows,
        rows={(row['scenario_id'],row['policy_id']):row for row in rows},states=states,profiles=profiles)

def _read(path):return json.loads(Path(path).read_text(encoding='utf-8'))
def _sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def _new_json(path,value):
    with Path(path).open('x',encoding='utf-8') as stream:json.dump(value,stream,ensure_ascii=False,indent=2,allow_nan=False)

def analyze_batch(manifest,plan,results,audit,out_dir):
    from .metrics import extract_metrics
    from .recovery import extract_execution_recovery
    from .budget_execution import extract_executed_budget
    from .closed_loop import _source_binding
    plan_value=_read(plan);namespace=analysis_namespace(references_for(plan_value['policy_specs']))
    old_decisions=namespace['_decisions'];extras=[];decision_rows=[];transition_rows=[]
    def decisions(scene,pid,payload):
        # The established analyzer has already opened and SHA-checked this raw.
        # Reuse that same in-memory payload; never open a raw again for extras.
        value=extract_metrics(payload,scene)
        value['execution_recovery_diagnostics']=extract_execution_recovery(payload)
        value['execution_budget_diagnostics']=extract_executed_budget(payload,scene)
        identity=dict(scenario_id=scene['id'],policy_id=pid,root_family_id=scene['root_family_id'],
                      analysis_class=scene['analysis_class'],analysis_cohort=scene['analysis_cohort'])
        for row in value['decision_rows']:decision_rows.append({**identity,**row})
        for row in value['source_transition_rows']:transition_rows.append({**identity,**row})
        extras.append({**identity,**{k:v for k,v in value.items() if k not in ('decision_rows','source_transition_rows')},
            'coordination_state_counts':dict(Counter((d.get('policy_diagnostic') or {}).get('coordination_state','not_coordinated') for d in payload.get('decisions',[]))),
            'coordination_preference_counts':dict(Counter((d.get('policy_diagnostic') or {}).get('coordination_preference','historical_reference') for d in payload.get('decisions',[])))})
        return old_decisions(scene,pid,payload)
    namespace['_decisions']=decisions
    report=namespace['analyze_batch'](manifest,plan,results,audit,out_dir)
    out=Path(out_dir)
    if len(extras)!=report['registered_trajectory_count']:raise ValueError('Extra metrics lost registered outcomes')
    extra_report=dict(schema='coordinated_dispatch.service_switch_saved_summary.v2',
        registered_trajectory_count=len(extras),metric_source_sha256=_sha(Path(__file__).with_name('metrics.py')),
        recovery_source_sha256=_sha(Path(__file__).with_name('recovery.py')),
        execution_budget_source_sha256=_sha(Path(__file__).with_name('budget_execution.py')),
        older_missing_execution_recovery_status='not_extracted',
        older_missing_execution_budget_status='not_extracted',
        analysis_source_sha256=_sha(__file__),original_19_targets_unchanged=True,
        new_physical_episodes=0,additional_raw_opens_for_extra_metrics=0,
        planned_budget_is_actual_ENS_guarantee=False,temperature_P3_not_observed=True,
        sustained_recovery_minutes=30.,sustained_recovery_is_clinical_threshold=False,
        incomplete_window_all_registered_is_null=True,trajectories=extras)
    _new_json(out/'SERVICE_SWITCH_SUMMARY.json',extra_report)
    flat=[]
    for item in extras:
        row={k:item[k] for k in ('scenario_id','policy_id','root_family_id','analysis_class','analysis_cohort',
                                 'coordination_state_counts','coordination_preference_counts')}
        row['execution_recovery_diagnostics_json']=item['execution_recovery_diagnostics']
        row['execution_budget_diagnostics_json']=item['execution_budget_diagnostics']
        for window in ('primary','full'):
            row[window+'_complete']=item[window]['complete']
            row[window+'_observed_minutes']=item[window]['observed_minutes']
            row[window+'_all_registered_json']=item[window]['all_registered']
            row[window+'_observed_prefix_json']=item[window]['observed_prefix']
        flat.append(row)
    original._write_table(out/'SERVICE_SWITCH_TRAJECTORIES.csv',flat)
    original._write_table(out/'SERVICE_BUDGET_DECISIONS.csv',decision_rows)
    original._write_table(out/'ACTUAL_SOURCE_TRANSITIONS.csv',transition_rows)
    receipt=dict(schema='coordinated_dispatch.analysis_source_binding.v2',wrapper_sha256=_sha(__file__),
        metric_sha256=_sha(Path(__file__).with_name('metrics.py')),registered_runtime_sources=_source_binding(),
        recovery_sha256=_sha(Path(__file__).with_name('recovery.py')),
        execution_budget_sha256=_sha(Path(__file__).with_name('budget_execution.py')),
        additional_fields=['execution_recovery_diagnostics','execution_budget_diagnostics'],older_missing_field_status='not_extracted',
        report_sha256=_sha(out/'SUMMARY.json'),extra_report_sha256=_sha(out/'SERVICE_SWITCH_SUMMARY.json'),
        physical_trajectories_started=0,extra_raw_opens=0)
    _new_json(out/'COORDINATED_SOURCE_BINDING.json',receipt)
    return report
