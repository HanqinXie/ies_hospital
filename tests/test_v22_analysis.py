"""Compact export preserves recorded values, unknown tails and actual counts."""
from copy import deepcopy
import csv
import hashlib
import importlib
import json
from pathlib import Path

import pytest


def api():
    try:
        return importlib.import_module('tools.model_closure_v22.analysis')
    except ModuleNotFoundError as exc:
        if exc.name == 'tools.model_closure_v22.analysis':
            pytest.fail('V22 explicit-run numeric exporter is missing')
        raise


def result():
    cell = dict(requested_kWh=100., delivered_kWh=100., ENS_kWh=0.,
                disconnected_ENS_kWh=0., connected_unserved_ENS_kWh=0.,
                material_minutes=0., longest_material_minutes=0., interruption_count=0)
    union = dict(material_minutes=0., longest_material_minutes=0., interruption_count=0)
    metrics = dict(complete=True, requested_end_minute=120., observed_until_minute=120.,
        unknown_tail_minutes=0.,
        carrier_service={f'P{p}': {k: deepcopy(cell) for k in 'ehc'} for p in range(4)},
        priority_union={f'P{p}': deepcopy(union) for p in range(4)}, critical_union=deepcopy(union),
        storage={'ups': dict(first_receipt=dict(initial_kWh=40., final_kWh=39.),
                            last_receipt=dict(initial_kWh=38., final_kWh=37.),
                            totals=dict(charged_input_kWh=.25, discharged_kWh=3.,
                                        standing_loss_kWh=.001, conversion_loss_kWh=.249))},
        initial_storage={'ups': 42.10526315789474},
        economics=dict(energy_cost_USD=12.345678901234567,
            initial_inventory_grid_input_kWh_conditional=42.10526315789474,
            initial_inventory_fee_USD_conditional=5.67890123456789,
            price_year=2001, price_currency='USD',
            emissions_conditional=dict(total_CO2_kg_conditional=19.123456789012345,
                emissions_factor_sha256='factor-sha', CO2_boundary='conditional operational CO2')),
        actual_energy=dict(grid_import_kWh=30.123456789012345))
    return dict(case_id='case', status='completed', actual_episode_attempts=1,
        historical_attempts_added=0, model_id='M22_model_closure', source_sha256='source-sha',
        git_commit='source-commit', model_config_sha256='config-sha',
        requested_end_minute=120., observed_until_minute=120., unknown_tail_minutes=0.,
        policy=dict(alias='C', policy_id='C_model_closure_v22'), metrics=metrics)


def write_run(root, name, value, *, with_case=True):
    directory = root/name
    directory.mkdir()
    path = directory/'RESULT.json'
    path.write_text(json.dumps(value, allow_nan=False), encoding='utf-8')
    if with_case:
        (directory/'CASE.json').write_text(json.dumps(dict(case=dict(id='case', configuration='H1',
            scenario=dict(id='scenario', source_block_id='source-block')))), encoding='utf-8')
    return path


def rows(path):
    with path.open(encoding='utf-8-sig', newline='') as stream:
        return list(csv.DictReader(stream))


def test_missing_and_failed_results_keep_unknown_values_and_explicit_attempt_counts(tmp_path):
    attempted = result()
    attempted.update(status='failed_unknown', metrics=None, observed_until_minute=None,
                     unknown_tail_minutes=120.)
    before_start = deepcopy(attempted)
    before_start['actual_episode_attempts'] = 0
    first = write_run(tmp_path, 'failed', attempted)
    second = write_run(tmp_path, 'init_failed', before_start)
    missing = tmp_path/'missing'
    missing.mkdir()
    output = tmp_path/'export'
    receipt = api().export_runs([first, second, missing], output, command=['test export'])
    run_rows = rows(output/'RUNS.csv')
    assert len(run_rows) == 3
    assert all(row['P0_e_ENS_kWh'] == '' for row in run_rows)
    assert all(row['energy_cost_USD'] == '' for row in run_rows)
    assert len(rows(output/'SERVICE.csv')) == 36
    assert all(row['ENS_kWh'] == '' for row in rows(output/'SERVICE.csv'))
    assert receipt['counts']['failed_or_unknown_records'] == 3
    assert receipt['counts']['actual_episode_attempts_known_sum'] == 1
    assert receipt['counts']['actual_episode_attempts_total'] is None
    assert receipt['counts']['actual_episode_attempts_unknown_records'] == 1
    assert receipt['counts']['failed_before_episode_attempt_records'] == 1


def test_every_service_cell_and_tiny_recorded_value_survives_without_rounding(tmp_path):
    value = result()
    tiny = 0.000000050000012345
    cell = value['metrics']['carrier_service']['P0']['c']
    cell.update(ENS_kWh=tiny, disconnected_ENS_kWh=tiny, connected_unserved_ENS_kWh=None,
                material_minutes=.000123456789, longest_material_minutes=.000123456789)
    value['metrics']['priority_union']['P0']['longest_material_minutes'] = .012345678901234
    path = write_run(tmp_path, 'complete', value)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    output = tmp_path/'export'
    receipt = api().export_runs([path.parent], output)
    service = rows(output/'SERVICE.csv')
    assert {(row['priority'], row['carrier']) for row in service} == {
        (f'P{p}', k) for p in range(4) for k in 'ehc'}
    target = next(row for row in service if row['priority'] == 'P0' and row['carrier'] == 'c')
    assert float(target['ENS_kWh']) == tiny
    assert float(target['disconnected_ENS_kWh']) == tiny
    assert target['connected_unserved_ENS_kWh'] == ''
    assert float(target['priority_union_longest_material_minutes']) == .012345678901234
    assert receipt['inputs'][0]['RESULT_sha256'] == before
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_failed_recorded_prefix_is_retained_but_never_becomes_a_full_episode_result(tmp_path):
    value = result()
    value.update(status='failed_unknown', observed_until_minute=60., unknown_tail_minutes=60.)
    value['metrics'].update(complete=False, observed_until_minute=60., unknown_tail_minutes=60.)
    value['metrics']['carrier_service']['P1']['h']['ENS_kWh'] = .125
    path = write_run(tmp_path, 'prefix', value)
    output = tmp_path/'export'
    api().export_runs([path], output)
    row = next(r for r in rows(output/'SERVICE.csv') if r['priority'] == 'P1' and r['carrier'] == 'h')
    assert row['metrics_scope'] == 'observed_prefix'
    assert row['ENS_kWh'] == ''
    assert float(row['observed_ENS_kWh']) == .125
    storage = next(r for r in rows(output/'STORAGE.csv') if r['storage'] == 'ups')
    assert storage['last_final_kWh'] == ''
    assert float(storage['observed_last_final_kWh']) == 37.


def test_storage_and_conditional_carbon_use_native_fields_and_paid_initial_inventory(tmp_path):
    value = result()
    value['metrics']['initial_storage']['cold_store'] = {'initial_charged_input_kWh': 63.15789473684211}
    path = write_run(tmp_path, 'complete', value)
    output = tmp_path/'export'
    api().export_runs([path], output)
    run = rows(output/'RUNS.csv')[0]
    assert float(run['total_CO2_kg_conditional']) == 19.123456789012345
    assert run['emissions_factor_sha256'] == 'factor-sha'
    assert run['CO2_boundary'] == 'conditional operational CO2'
    assert run['configuration_id'] == 'H1'
    stores = {r['storage']: r for r in rows(output/'STORAGE.csv')}
    assert float(stores['ups']['first_initial_kWh']) == 40.
    assert float(stores['ups']['last_final_kWh']) == 37.
    assert float(stores['ups']['total_discharged_kWh']) == 3.
    assert float(stores['ups']['paid_initial_charged_input_kWh']) == 42.10526315789474
    assert stores['cold_store']['last_final_kWh'] == ''
    assert float(stores['cold_store']['paid_initial_charged_input_kWh']) == 63.15789473684211


def test_missing_conditional_carbon_and_inconsistent_completion_remain_unknown(tmp_path):
    value = result()
    value['metrics']['economics']['emissions_conditional'] = None
    path = write_run(tmp_path, 'missing_carbon', value)
    output = tmp_path/'export'
    api().export_runs([path], output)
    assert rows(output/'RUNS.csv')[0]['total_CO2_kg_conditional'] == ''
    bad = result()
    bad.update(requested_end_minute=240.)
    bad['metrics']['requested_end_minute'] = 240.
    second = write_run(tmp_path, 'short_tail', bad)
    output2 = tmp_path/'export2'
    api().export_runs([second], output2)
    assert rows(output2/'RUNS.csv')[0]['metrics_scope'] == 'observed_prefix'
    assert rows(output2/'RUNS.csv')[0]['P0_e_ENS_kWh'] == ''


def test_pairs_are_only_explicit_raw_differences_and_unknowns_have_no_difference(tmp_path):
    before, after = result(), result()
    before['metrics']['carrier_service']['P0']['e']['ENS_kWh'] = .00000005
    after['metrics']['carrier_service']['P0']['e']['ENS_kWh'] = .00000004
    after['metrics']['economics']['emissions_conditional'] = None
    first = write_run(tmp_path, 'before', before)
    second = write_run(tmp_path, 'after', after)
    third = write_run(tmp_path, 'unpaired', result())
    output = tmp_path/'export'
    api().export_runs([first, second, third], output, pairs=[(first, second)])
    differences = rows(output/'PAIR_DIFF.csv')
    assert {r['pair_id'] for r in differences} == {'pair_0001'}
    row = next(r for r in differences if r['table'] == 'SERVICE' and r['priority'] == 'P0'
               and r['carrier'] == 'e' and r['metric'] == 'ENS_kWh')
    assert float(row['after_minus_before']) == pytest.approx(-.00000001, abs=1e-22)
    carbon = next(r for r in differences if r['metric'] == 'total_CO2_kg_conditional')
    assert carbon['after_minus_before'] == ''
    inventory = next(r for r in differences if r['table'] == 'STORAGE' and r['storage'] == 'ups')
    assert inventory['unit'] == 'kWh_e'
    assert all('winner' not in r and 'choice' not in r for r in differences)


def test_fresh_output_and_duplicate_explicit_inputs_are_rejected(tmp_path):
    path = write_run(tmp_path, 'run', result())
    existing = tmp_path/'existing'
    existing.mkdir()
    with pytest.raises(FileExistsError):
        api().export_runs([path], existing)
    with pytest.raises(ValueError, match='Duplicate'):
        api().export_runs([path, path.parent], tmp_path/'duplicate')


def test_empty_pair_table_is_written_without_implicit_pairing(tmp_path):
    path = write_run(tmp_path, 'run', result(), with_case=False)
    output = tmp_path/'export'
    receipt = api().export_runs([path], output)
    assert rows(output/'PAIR_DIFF.csv') == []
    assert receipt['inputs'][0]['CASE_sha256'] is None
    assert receipt['counts']['actual_episode_attempts_total'] == 1


def test_result_configuration_is_preserved_when_optional_case_is_absent(tmp_path):
    value = result()
    value['configuration_id'] = 'H2'
    path = write_run(tmp_path, 'run', value, with_case=False)
    output = tmp_path/'export'
    api().export_runs([path], output)
    assert rows(output/'RUNS.csv')[0]['configuration_id'] == 'H2'


def test_unreadable_explicit_result_is_an_unknown_record_not_an_export_crash(tmp_path, monkeypatch):
    path = write_run(tmp_path, 'run', result())
    original = Path.read_bytes
    def read_bytes(current):
        if current == path:
            raise PermissionError('test unreadable result')
        return original(current)
    monkeypatch.setattr(Path, 'read_bytes', read_bytes)
    output = tmp_path/'export'
    receipt = api().export_runs([path], output)
    run = rows(output/'RUNS.csv')[0]
    assert run['status'] == 'unreadable_result'
    assert run['P0_e_ENS_kWh'] == ''
    assert 'PermissionError' in run['RESULT_read_error']
    assert receipt['counts']['actual_episode_attempts_total'] is None
