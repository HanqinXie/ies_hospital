"""Versioned history wiring over unchanged M0, with no physical-data fixtures."""
import importlib
from pathlib import Path
import sys
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))

def api():
    try:return importlib.import_module('tools.executed_service.closed_loop')
    except ModuleNotFoundError:pytest.fail('Executed-service runner is missing')

def contracts(a):
    return dict(executed_service_history_contract_id=a.EXECUTED_SERVICE_HISTORY_CONTRACT_ID,
        executed_service_policy_contract_id=a.EXECUTED_SERVICE_POLICY_CONTRACT_ID)

def test_original_source_and_environment_are_unchanged(monkeypatch):
    a=api();old=importlib.import_module('tools.coordinated_dispatch.closed_loop_adaptive')
    assert len(old.SOURCE_FILES)==53
    assert set(a.SOURCE_FILES)-set(old.SOURCE_FILES)=={
        'tools/executed_service/history.py','tools/executed_service/policy.py','tools/executed_service/closed_loop.py'}
    marker=object();monkeypatch.setattr(old,'_environment_factory',lambda *args:marker)
    assert a._environment_factory({},contracts(a)) is marker
    with pytest.raises(ValueError):a._environment_factory({}, {})

def test_both_online_and_exporter_snapshots_are_the_same_history_adapter():
    a=api();episode=a.episode_function()
    assert episode.__globals__['PolicyEnvironment'].step.__globals__['decision_snapshot'] is a.decision_snapshot
    assert episode.__globals__['run_trajectory'].__globals__['decision_snapshot'] is a.decision_snapshot
    from hospital_ies import dataset_export
    from tools.integrated_learning import episode as original
    assert original.PolicyEnvironment.step.__globals__['decision_snapshot'] is not a.decision_snapshot
    assert dataset_export.run_trajectory.__globals__['decision_snapshot'] is not a.decision_snapshot

def test_new_feedback_factory_and_legacy_reference_validation_are_distinct():
    a=api()
    spec=dict(policy_id='executed_loss_feedback_v13',kind='executed_service',
        config=dict(coordination_mode='fixed',fixed_preference='standby_first',budget_feedback=False))
    assert a._factory(spec).__class__.__name__=='ExecutedServicePolicy'
    assert a._check_specs([spec])==[spec]
    with pytest.raises(ValueError):a._check_specs([spec,spec])
    bad={**spec,'policy_id':'execution_reserve'}
    with pytest.raises(ValueError):a._check_specs([bad])
    ns=a._runner_namespace()
    for k in ('_factory','_source_binding','run_policy_episode','_payload_binding'):
        assert ns[k] is getattr(a,k)

def test_contract_checks_reject_missing_history_without_relabeling_payload(monkeypatch):
    a=api();old=importlib.import_module('tools.coordinated_dispatch.closed_loop_adaptive')
    monkeypatch.setattr(old,'_payload_binding',lambda *args:dict(passed=True,checks={'old':True}))
    manifest=contracts(a);payload={**manifest,'decisions':[]}
    assert a._payload_binding(payload,{},manifest)['passed'] is True
    payload.pop('executed_service_history_contract_id')
    assert a._payload_binding(payload,{},manifest)['passed'] is False
