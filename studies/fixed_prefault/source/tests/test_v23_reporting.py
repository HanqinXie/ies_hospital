"""Explicit RAW recovery export fills known times without rewriting old metrics."""
import csv
import gzip
import hashlib
import importlib
import json

import pytest


def api():
    try:
        return importlib.import_module('tools.model_correctness_v23.reporting')
    except ModuleNotFoundError as exc:
        if exc.name == 'tools.model_correctness_v23.reporting':
            pytest.fail('V23 explicit recovery exporter is missing')
        raise


def native(start, end, cold_short=False):
    req = [[100., 100., 100.] for _ in range(4)]
    cut = [[0., 0., 0.] for _ in range(4)]
    if cold_short:
        cut[0][2] = 25.
    return dict(start_minute=start, end_minute=end,
        service_request_ledger=dict(original_demand_kW=req, active_cut_kW=cut,
            connected_unserved_kW=[[0., 0., 0.] for _ in range(4)],
            delivered_kW=[[100.-v for v in row] for row in cut]))


def write_run(root, name, *, missing=False):
    run = root/name
    run.mkdir()
    (run/'RESULT.json').write_text(json.dumps(dict(status='completed', model_id='M23',
        source_sha256='science', requested_end_minute=3., metrics=dict(critical_union=dict(interruption_count=1)))), encoding='utf-8')
    if not missing:
        with gzip.open(run/'trajectory.json.gz', 'wt', encoding='utf-8') as f:
            json.dump(dict(segments=[native(0., 1., True), native(1., 3.)],
                summary=dict(requested_end_minute=3.)), f)
    return run


def rows(path):
    with path.open(encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))


def test_export_fills_union_and_all_cells_with_statuses_and_binds_raw(tmp_path):
    run = write_run(tmp_path, 'complete')
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in run.iterdir()}
    receipt = api().export_recovery_tables([run], tmp_path/'export',
        references={'complete': dict(external_fault_clear_minute=1., actual_source_restore_minute=1.)})
    cells = rows(tmp_path/'export'/'RECOVERY.csv')
    assert len(cells) == 17  # 12 cells, four priority unions and critical union
    union = next(r for r in cells if r['scope'] == 'critical_union')
    assert union['recovery_status'] == 'observed'
    assert float(union['recovery_absolute_minute']) == 1.
    assert float(union['recovery_confirmation_minute']) == 2.
    assert float(union['recovery_wait_after_external_fault_clear_minutes']) == 0.
    normal = next(r for r in cells if r['priority'] == 'P1' and r['carrier'] == 'e')
    assert normal['recovery_status'] == 'not_applicable'
    assert normal['recovery_absolute_minute'] == ''
    runrow = rows(tmp_path/'export'/'RUNS.csv')[0]
    assert float(runrow['critical_union_recovery_minutes']) == 0.
    assert runrow['critical_union_recovery_status'] == 'observed'
    assert receipt['inputs'][0]['RAW_sha256'] == before['trajectory.json.gz']
    assert {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in run.iterdir()} == before


def test_missing_raw_is_missing_and_never_zero(tmp_path):
    run = write_run(tmp_path, 'missing', missing=True)
    api().export_recovery_tables([run], tmp_path/'export')
    union = next(r for r in rows(tmp_path/'export'/'RECOVERY.csv') if r['scope'] == 'critical_union')
    assert union['recovery_status'] == 'missing'
    assert union['recovery_absolute_minute'] == ''
    assert rows(tmp_path/'export'/'RUNS.csv')[0]['critical_union_recovery_minutes'] == ''


def test_known_absolute_recovery_is_exported_without_unknown_reference_fabrication(tmp_path):
    run = write_run(tmp_path, 'complete')
    api().export_recovery_tables([run], tmp_path/'export')
    row = rows(tmp_path/'export'/'RUNS.csv')[0]
    assert float(row['critical_union_recovery_absolute_minute']) == 1.
    assert row['critical_union_recovery_minutes'] == ''
    assert row['critical_union_recovery_external_reference_status'] == 'missing'


def test_export_refuses_overwrite_and_duplicate_source_counting(tmp_path):
    run = write_run(tmp_path, 'complete')
    with pytest.raises(ValueError, match='Duplicate'):
        api().export_recovery_tables([run, run], tmp_path/'export')
    api().export_recovery_tables([run], tmp_path/'export')
    with pytest.raises(FileExistsError):
        api().export_recovery_tables([run], tmp_path/'export')


def test_failed_prefix_preserves_observed_recovery_without_full_episode_claim(tmp_path):
    run = write_run(tmp_path, 'prefix')
    result = json.loads((run/'RESULT.json').read_text(encoding='utf-8'))
    result['status'] = 'failed_unknown'
    (run/'RESULT.json').write_text(json.dumps(result), encoding='utf-8')
    with gzip.open(run/'trajectory.json.gz', 'wt', encoding='utf-8') as f:
        json.dump(dict(segments=[native(0., 1., True), native(1., 2.5)],
            summary=dict(requested_end_minute=3.)), f)
    api().export_recovery_tables([run], tmp_path/'export',
        references={'prefix': dict(external_fault_clear_minute=0., actual_source_restore_minute=0.)})
    row = rows(tmp_path/'export'/'RUNS.csv')[0]
    assert row['metrics_scope'] == 'observed_prefix'
    assert row['critical_union_recovery_minutes'] == ''
    assert float(row['observed_critical_union_recovery_minutes']) == 1.
    assert row['critical_union_recovery_absolute_minute'] == ''
    assert float(row['observed_critical_union_recovery_absolute_minute']) == 1.


def test_explicit_inapplicable_reference_is_exported_separately_from_absent_data(tmp_path):
    run = write_run(tmp_path, 'complete')
    api().export_recovery_tables([run], tmp_path/'export', references={'complete': dict(
        external_fault_clear_minute='not_applicable', actual_source_restore_minute=dict(c='not_applicable'))})
    row = rows(tmp_path/'export'/'RUNS.csv')[0]
    assert row['critical_union_recovery_status'] == 'observed'
    assert float(row['critical_union_recovery_absolute_minute']) == 1.
    assert row['critical_union_recovery_external_reference_status'] == 'not_applicable'
    assert row['critical_union_recovery_source_reference_status'] == 'not_applicable'
    assert row['critical_union_recovery_minutes'] == ''
