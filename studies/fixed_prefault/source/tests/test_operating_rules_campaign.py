"""A batch must not launch beyond its registered budget or drifted identity."""
import importlib,sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]

def api():
    try:return importlib.import_module('tools.operating_rules_study.campaign')
    except ModuleNotFoundError as exc:pytest.fail('Bounded study dispatch is missing: '+str(exc))

def test_attempt_budget_counts_failed_and_unfinished_attempts():
    rows=[{'event':'started'},{'event':'finished','status':'failed_unknown'},
          {'event':'started'},{'event':'finished','status':'completed'},{'event':'started'}]
    assert api().attempt_count(rows)==3
    with pytest.raises(RuntimeError,match='budget'):api().require_budget(rows,3)
    api().require_budget(rows,4)

def test_unknown_audit_is_not_physical_failure_but_known_bad_balance_stops():
    # Old test assumed every unknown audit had observable evidence. The review
    # reproduced an empty episode accepted as unknown; require positive checks.
    with pytest.raises(RuntimeError,match='audit'):
        api().require_audit({'audit_status':'unknown','failed_checks':[],'unknown_checks':['unmetered steam']})
    with pytest.raises(RuntimeError,match='audit'):api().require_audit({'audit_status':'failed','failed_checks':['electric balance']})
    with pytest.raises(RuntimeError,match='audit'):api().require_audit({})

def test_source_identity_mismatch_is_explicit():
    with pytest.raises(RuntimeError,match='identity'):api().require_identity('old','new')
    api().require_identity('same','same')

def test_unknown_empty_episode_cannot_enter_comparison():
    from tools.operating_rules_study.ledger import audit_payload
    report=audit_payload({'segments':[],'scenario':{'duration_steps':12,'step_minutes':15.},
        'summary':{'requested_end_minute':180.,'status':'completed'}},result={'status':'completed'})
    with pytest.raises(RuntimeError,match='audit'):api().require_audit(report)

def test_resume_checks_identity_before_trusting_receipt(tmp_path,monkeypatch):
    m=api();out=tmp_path/'old';out.mkdir();m.write(out/'STUDY_ATTEMPT.json',{'accepted_complete':True})
    case=tmp_path/'case.json';m.write(case,{'id':'case'})
    plan=tmp_path/'plan.json';m.write(plan,dict(study_root=str(tmp_path),identity={'source':'old'},
        items=[dict(output=str(out),case_file=str(case),case_sha256=m.sha(case),case_id='case',method='M')]))
    monkeypatch.setattr(m,'identity',lambda:{'source':'new'})
    with pytest.raises(RuntimeError,match='identity'):m.run_plan(plan)

def test_resume_requires_saved_run_evidence_even_when_identity_matches(tmp_path,monkeypatch):
    m=api();out=tmp_path/'old';out.mkdir();m.write(out/'STUDY_ATTEMPT.json',{'accepted_complete':True})
    case=tmp_path/'case.json';m.write(case,{'id':'case'})
    plan=tmp_path/'plan.json';m.write(plan,dict(study_root=str(tmp_path),identity={'source':'same'},
        items=[dict(output=str(out),case_file=str(case),case_sha256=m.sha(case),case_id='case',method='M')]))
    monkeypatch.setattr(m,'identity',lambda:{'source':'same'})
    with pytest.raises((RuntimeError,FileNotFoundError)):m.run_plan(plan)

def test_completed_run_cannot_be_reused_under_another_plan(tmp_path):
    m=api();plan_file=tmp_path/'plan.json';m.write(plan_file,{'identity':'plan'})
    item={'case_id':'case','method':'M','output':str(tmp_path/'run')}
    with pytest.raises(RuntimeError,match='receipt'):
        m.verify_completed_run(item,{'identity':{}},plan_file,receipt={'accepted_complete':True,
            'case_id':'case','method':'M','output':item['output'],'plan_sha256':'wrong'})
