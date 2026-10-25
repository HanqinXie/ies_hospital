"""Review is explicit, read-only, and never pairs a hidden physical change."""
from copy import deepcopy
import csv
import gzip
import hashlib
import importlib
import json

import pytest

from tools.model_correctness_v23.comparison import with_comparison_inputs
from tools.model_correctness_v23.metrics import build_reliability_metrics


def api():
    try:
        return importlib.import_module('tools.model_correctness_v23.review')
    except ModuleNotFoundError as exc:
        if exc.name == 'tools.model_correctness_v23.review':
            pytest.fail('V23 explicit final-run review is missing')
        raise


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
        ensure_ascii=False, allow_nan=False).encode('utf-8')).hexdigest()


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def native(a, b, short=False):
    cut = [[0., 0., 0.] for _ in range(4)]
    if short:
        cut[0][2] = 25.
    return dict(start_minute=a, end_minute=b, service_request_ledger=dict(
        original_demand_kW=[[100., 100., 100.] for _ in range(4)], active_cut_kW=cut,
        connected_unserved_kW=[[0., 0., 0.] for _ in range(4)],
        delivered_kW=[[100.-v for v in row] for row in cut]))


def write_run(root, name, policy='C', *, hardware=40., source='a'*64, failed=False, fee=100.):
    path = root/name
    path.mkdir()
    scenario = dict(id='same-scenario', start_hour=1000, duration_steps=4, step_minutes=1.,
        initialization_seed=23, faults=dict(GRID=0.))
    model_config = dict(configuration_id='H1', ups_capacity_kWh=hardware)
    raw = dict(segments=[native(0., 1., True), native(1., 4.)], summary=dict(requested_end_minute=4.))
    rel = build_reliability_metrics(raw, references=dict(external_fault_clear_minute=1., actual_source_restore_minute=.5))
    metrics = dict(complete=True, observed_until_minute=4., requested_end_minute=4., unknown_tail_minutes=0.,
        carrier_service=deepcopy(rel['carrier_service']), priority_union=deepcopy(rel['priority_union']),
        critical_union=deepcopy(rel['critical_union']),
        economics=dict(energy_cost_USD=fee, price_year=2001, price_currency='USD',
            emissions_conditional=dict(total_CO2_kg_conditional=50., emissions_factor_sha256='factor', CO2_boundary='boundary')))
    inventories = {key: dict(initial_kWh=value, scale_kWh=value, final_kWh=value/2.)
        for key, value in [('ups', hardware), ('cold_store', 60.), ('DG_shaft_budget', 600.)]}
    with gzip.open(path/'trajectory.json.gz', 'wt', encoding='utf-8') as stream:
        json.dump(raw, stream)
    (path/'PROCESS.csv').write_text('start_minute,end_minute\n0,1\n1,4\n', encoding='utf-8')
    case = dict(id='case', scenario=scenario, configuration='H1', policy=dict(alias=policy))
    (path/'CASE.json').write_text(json.dumps(dict(case=case)), encoding='utf-8')
    # Matches the actual M23 worker's resolved-case layout.
    (path/'RESOLVED_CASE.json').write_text(json.dumps(dict(case=case, model_config=model_config)), encoding='utf-8')
    record = dict(status='failed_unknown' if failed else 'completed', case_id='case', model_id='M23_model_correctness',
        source_sha256=source, input_sha256={'loads': 'same-input'}, source_and_inputs_unchanged=True,
        scenario_sha256=digest(scenario), model_config_sha256=digest(model_config),
        resolved_model_config=model_config, configuration='H1',
        policy=dict(alias=policy, policy_id=policy+'_M23', config={}), actual_episode_attempts=1,
        requested_end_minute=4., observed_until_minute=4., unknown_tail_minutes=0., metrics=metrics,
        reliability_v23=rel, recovery_references=dict(external_fault_clear_minute=1., actual_source_restore_minute=.5),
        inventories=inventories, comparison_inputs=with_comparison_inputs(metrics, rel, inventories),
        raw_path=str(path/'trajectory.json.gz'), raw_sha256=sha(path/'trajectory.json.gz'),
        process=dict(path=str(path/'PROCESS.csv'), sha256=sha(path/'PROCESS.csv')),
        error=dict(type='KnownFailure', message='retained failure') if failed else None)
    (path/'RESULT.json').write_text(json.dumps(record), encoding='utf-8')
    return path


def rows(path):
    with path.open(encoding='utf-8-sig', newline='') as stream:
        return list(csv.DictReader(stream))


def mutate(run, **updates):
    path = run/'RESULT.json'
    record = json.loads(path.read_text(encoding='utf-8'))
    record.update(updates)
    path.write_text(json.dumps(record), encoding='utf-8')


def test_explicit_review_exports_real_fields_clocks_and_policy_comparison(tmp_path):
    left = write_run(tmp_path, 'C', fee=99.)
    right = write_run(tmp_path, 'R', 'R')
    before = {str(p): sha(p) for run in (left, right) for p in run.iterdir()}
    receipt = api().review_runs([left, right/'RESULT.json'], tmp_path/'review')
    runs = rows(tmp_path/'review'/'RUNS.csv')
    assert len(runs) == 2
    assert float(runs[0]['P0_c_ENS_kWh']) == pytest.approx(25./60.)
    assert float(runs[0]['P3_h_delivered_kWh']) == pytest.approx(100.*4./60.)
    assert float(runs[0]['general_c_ENS_kWh']) == 0.
    assert float(runs[0]['energy_cost_USD']) == 99.
    assert float(runs[0]['DG_shaft_budget_final_kWh']) == 300.
    assert runs[0]['critical_union_recovery_status'] == 'observed'
    assert float(runs[0]['critical_union_recovery_absolute_minute']) == 1.
    assert float(runs[0]['critical_union_recovery_confirmation_minute']) == 2.
    assert float(runs[0]['critical_union_recovery_wait_after_actual_source_restore_minutes']) == .5
    recovery = rows(tmp_path/'review'/'RECOVERY.csv')
    assert len(recovery) == 34
    pairs = rows(tmp_path/'review'/'PAIRWISE.csv')
    assert pairs and all(row['formal_comparison'] == 'True' for row in pairs)
    fee = next(row for row in pairs if row['metric'] == 'energy_cost_USD')
    assert float(fee['left_minus_right']) == -1.
    assert fee['choice'] == 'left'
    assert receipt['counts']['candidate_policy_pairs'] == 1
    assert receipt['solver_calls'] == 0
    assert before == {str(p): sha(p) for run in (left, right) for p in run.iterdir()}


def test_different_effective_hardware_or_same_policy_is_not_a_policy_pair(tmp_path):
    a = write_run(tmp_path, 'C')
    b = write_run(tmp_path, 'R_H0', 'R', hardware=0.)
    c = write_run(tmp_path, 'C_repeat')
    receipt = api().review_runs([a, b, c], tmp_path/'review')
    assert rows(tmp_path/'review'/'PAIRWISE.csv') == []
    assert receipt['counts']['candidate_policy_pairs'] == 0


@pytest.mark.parametrize('kind', ['source_drift', 'different_source', 'raw_drift', 'process_drift'])
def test_source_or_evidence_drift_is_visible_and_cannot_be_formal(tmp_path, kind):
    a, b = write_run(tmp_path, 'C'), write_run(tmp_path, 'R', 'R')
    if kind == 'source_drift':
        mutate(b, source_and_inputs_unchanged=False, source_drift_error='source changed')
    elif kind == 'different_source':
        mutate(b, source_sha256='b'*64)
    elif kind == 'raw_drift':
        (b/'trajectory.json.gz').write_bytes(b'changed native evidence')
    else:
        (b/'PROCESS.csv').write_text('changed process', encoding='utf-8')
    api().review_runs([a, b], tmp_path/'review')
    pairs = rows(tmp_path/'review'/'PAIRWISE.csv')
    assert pairs and all(row['formal_comparison'] == 'False' for row in pairs)
    assert all(row['choice'] == 'unknown' and row['eligibility_reason'] for row in pairs)


def test_failure_and_missing_result_keep_unknowns_and_actual_attempt_counts(tmp_path):
    failed = write_run(tmp_path, 'failed', failed=True)
    mutate(failed, metrics=None, reliability_v23=None, comparison_inputs=None,
        observed_until_minute=None, unknown_tail_minutes=4.)
    missing = tmp_path/'absent'
    missing.mkdir()
    receipt = api().review_runs([failed, missing], tmp_path/'review')
    table = rows(tmp_path/'review'/'RUNS.csv')
    assert all(row['P0_c_ENS_kWh'] == '' and row['energy_cost_USD'] == '' for row in table)
    assert all(row['critical_union_recovery_status'] == 'missing' for row in table)
    assert table[0]['error_type'] == 'KnownFailure'
    assert table[1]['status'] == 'missing_result'
    assert receipt['counts']['actual_episode_attempts_known_sum'] == 1
    assert receipt['counts']['actual_episode_attempts_total'] is None


def test_missing_comparison_snapshot_is_unknown_without_invented_inventory(tmp_path):
    a, b = write_run(tmp_path, 'C'), write_run(tmp_path, 'R', 'R')
    mutate(b, comparison_inputs=None, inventories=None)
    api().review_runs([a, b], tmp_path/'review')
    pairs = rows(tmp_path/'review'/'PAIRWISE.csv')
    assert pairs and all(row['choice'] == 'unknown' for row in pairs)
    assert all(row['formal_comparison'] == 'False' for row in pairs)


def test_review_refuses_duplicates_and_overwrite(tmp_path):
    run = write_run(tmp_path, 'C')
    with pytest.raises(ValueError, match='Duplicate'):
        api().review_runs([run, run/'RESULT.json'], tmp_path/'review')
    api().review_runs([run], tmp_path/'review')
    with pytest.raises(FileExistsError):
        api().review_runs([run], tmp_path/'review')
