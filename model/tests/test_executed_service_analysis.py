"""Small saved-payload hooks; no production environment or trajectory reads."""
import copy
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


def api():
    try:
        return importlib.import_module('tools.executed_service.analysis')
    except ModuleNotFoundError:
        pytest.fail('Executed-service saved analysis adapter is missing')


def saved_payload():
    from tools.executed_service.history import make_snapshot
    cfg = dict(p0_solver_feasibility_tol_kW=1e-6, p0_unserved_abs_tol_kW=.01, p0_unserved_rel_tol=.001)
    segment = dict(start_minute=0., end_minute=15., command_id=1,
                   loads_kW=[[10., 10., 10.]] * 4,
                   unserved_kW=[[0., 0., 0.], [0., 0., 0.], [.1, 0., 0.], [0., 0., .2]])
    history = []
    for t, segments in ((0., []), (15., [segment])):
        env = SimpleNamespace(config=cfg, core=SimpleNamespace(time=t, segments=segments))
        history.append(make_snapshot(env, dict(decision_time_min=t, observation_time_min=t))['executed_service_history'])
    command = dict(id=1, generated_minute=0., arrival_minute=0., dispatch={})
    payload = dict(summary=dict(observed_until_minute=15., requested_end_minute=30.),
        segments=[segment], events=[dict(kind='command_executed', command_id=1, time_minute=0.)],
        decisions=[dict(decision_time_min=0., executed_service_history=history[0], command_generated=command,
                        policy_diagnostic=dict(effective_service_budget_fractions=[.05, .20])),
                   dict(decision_time_min=15., executed_service_history=history[1], command_generated=None,
                        policy_diagnostic=dict(effective_service_budget_fractions=[.04875, .195]))])
    scene = dict(id='s', root_family_id='r', analysis_class='L2', analysis_cohort='dev',
                 condition_id=None, duration_steps=2, step_minutes=15)
    return scene, payload


def test_hook_reuses_identical_open_payload_and_preserves_censored_interval(monkeypatch):
    a = api(); scene, payload = saved_payload(); before = copy.deepcopy(payload)
    delegated = []
    def original_decisions(s, pid, raw):
        delegated.append(raw)
        return [{'original_metric_row': True}]
    monkeypatch.setattr(a.coordinated, 'analysis_namespace', lambda refs: {'_decisions': original_decisions})
    collected = a.new_collection()
    namespace = a.analysis_namespace(('fixed',), collected=collected)
    result = namespace['_decisions'](scene, 'feedback', payload)
    assert delegated == [payload] and delegated[0] is payload
    assert result == [{'original_metric_row': True}] and payload == before
    assert len(collected['history_decisions']) == 2
    row = collected['history_decisions'][-1]
    assert row['cumulative_ENS_kWh_json'][0][0] == pytest.approx(.025)
    assert row['effective_service_budget_fractions_json'] == [.04875, .195]
    assert row['last_completed_segment_command_id'] == 1 and row['generated_command_id'] is None
    interval = collected['execution_intervals'][0]
    assert interval['right_censored'] and interval['end_minute'] is None
    assert interval['complete_interval_P2_P3_ENS_kWh'] is None
    assert collected['trajectories'][0]['unknown_tail_minutes'] == [15., 30.]


def test_wrapper_binds_full_runtime_separately_from_common_components_and_keeps_unknown(tmp_path, monkeypatch):
    a = api(); scene, payload = saved_payload(); calls = []
    history_contract = 'executed_service.completed_prefix.v1'
    policy_contract = 'executed_service.monotone_allowance.v1'
    common_sources = {f'common_{i}.py': 'a' * 64 for i in range(51)}
    full_sources = dict(common_sources, **{f'new_{i}.py': 'b' * 64 for i in range(5)})
    manifest = dict(executed_service_history_contract_id=history_contract,
                    executed_service_policy_contract_id=policy_contract)
    paths = [tmp_path / name for name in ('manifest.json', 'plan.json', 'AUDIT.json')]
    for path, value in zip(paths, (manifest, {}, {})):
        path.write_text(json.dumps(value), encoding='utf-8')
    def verify(plan):
        calls.append('verify')
        return {}, manifest
    runtime = SimpleNamespace(verify_batch=verify, _source_binding=lambda: full_sources)
    monkeypatch.setattr(a, '_runtime', lambda: (runtime, policy_contract))
    monkeypatch.setattr(a.coordinated, 'analysis_namespace', lambda refs: {'_decisions': lambda *args: []})
    def common_analyze(manifest, plan, results, audit, out_dir):
        calls.append('common')
        namespace = analysis_namespace(('fixed',))
        namespace['_decisions'](scene, 'feedback', payload)
        out = Path(out_dir); out.mkdir()
        report = dict(registered_trajectory_count=1, unknown_full_trajectory_count=1)
        (out / 'SUMMARY.json').write_text(json.dumps(report), encoding='utf-8')
        service = dict(trajectories=[dict(scenario_id='s', policy_id='feedback',
            primary=dict(complete=False, all_registered=None), full=dict(complete=False, all_registered=None))])
        (out / 'SERVICE_SWITCH_SUMMARY.json').write_text(json.dumps(service), encoding='utf-8')
        (out / 'COORDINATED_SOURCE_BINDING.json').write_text(json.dumps(dict(registered_runtime_sources=common_sources)), encoding='utf-8')
        return report
    monkeypatch.setattr(a.coordinated, 'analyze_batch', common_analyze)
    result = a.analyze_batch(paths[0], paths[1], tmp_path / 'results', paths[2], tmp_path / 'analysis')
    assert calls == ['verify', 'common'] and result['unknown_full_trajectory_count'] == 1
    out = tmp_path / 'analysis'
    receipt = json.loads((out / 'EXECUTED_SERVICE_SOURCE_BINDING.json').read_text(encoding='utf-8'))
    assert receipt['runtime_source_count'] == 56 and receipt['registered_runtime_sources'] == full_sources
    assert receipt['common_component_source_count'] == 51
    assert 'common' in receipt['common_component_receipt_scope'].lower()
    assert receipt['executed_service_history_contract_id'] == history_contract
    assert receipt['executed_service_policy_contract_id'] == policy_contract
    assert receipt['extra_raw_opens'] == 0 and receipt['physical_trajectories_started'] == 0
    common = json.loads((out / 'COORDINATED_SOURCE_BINDING.json').read_text(encoding='utf-8'))
    assert common['registered_runtime_sources'] == common_sources
    summary = json.loads((out / 'EXECUTED_SERVICE_SUMMARY.json').read_text(encoding='utf-8'))
    assert summary['trajectories'][0]['full_service_all_registered'] is None
    assert summary['trajectories'][0]['unknown_tail_minutes'] == [15., 30.]
    assert not list(out.glob('*.png')) and not list(out.glob('*.svg'))


def test_new_runner_rejection_precedes_any_common_analysis(monkeypatch):
    a = api()
    def reject(*args):
        raise ValueError('new runtime source binding differs')
    runtime = SimpleNamespace(verify_batch=reject)
    monkeypatch.setattr(a, '_runtime', lambda: (runtime, 'executed_service.monotone_allowance.v1'))
    monkeypatch.setattr(a.coordinated, 'analyze_batch', lambda *args: pytest.fail('Common analysis ran before v13 verification'))
    with pytest.raises(ValueError, match='source binding'):
        a.analyze_batch('manifest', 'plan', 'results', 'audit', 'out')
