"""Independent raw-ledger contracts; no simulation or optimizer is invoked."""
from copy import deepcopy
import gzip
import hashlib
import importlib
import json
from pathlib import Path

import pytest


def api():
    assert (Path(__file__).parents[1]/'tools/operating_rules_study/ledger.py').is_file(), 'independent ledger is not implemented'
    return importlib.import_module('tools.operating_rules_study.ledger')


PARAMETERS = dict(RT_TO_KW=4., steam_kWh_per_lb=.3, exchanger_eff=.9,
    abs_steam_lb_per_rt=20., abs_aux_kW_per_rt=.2, ec_electric_kW_per_rt=.8,
    boiler_eff=.8, kWh_per_MMBtu=300., gas_price_usd_per_mmbtu=5.,
    dg_shaft_fee_USD_per_kWh=.3, cold_pump_kW_per_kW=.02)


def segment(start=0., end=30., grid=14.):
    # 10 kW direct electricity, 1 kW converter auxiliary, 1 kW station,
    # 1 kW cold pump and 1 kW UPS charging. Four distinct loads.
    demand = [[10., 2.7, 4.], [0., 0., 0.], [0., 0., 0.], [0., 0., 0.]]
    return dict(start_minute=start, end_minute=end, hour_of_year=12,
        service_request_ledger=dict(original_demand_kW=demand,
            delivered_kW=deepcopy(demand), active_cut_kW=[[0.]*3 for _ in range(4)],
            connected_unserved_kW=[[0.]*3 for _ in range(4)]),
        action=dict(gt_electric_kW=[0., 0., 0.], grid_import_kW=grid, diesel_kW=0.,
            absorption_cooling_kW=0., electric_chiller_cooling_kW=[5., 0., 0.],
            boiler_steam_lbph=10., hrsg_steam_lbph=0., gt_fuel_MMBtu_h=2.,
            ups_discharge_kW=0., ups_charge_kW=1., cold_discharge_kW=0., cold_charge_kW=1.,
            cold_storage_pump_kW=1., station_auxiliary_kW=1.,
            boiler_auxiliary_kW=.4, gt_start_electric_kW=.2, boiler_restart_electric_kW=.4,
            recovery_pump_electric_kW=0., recovery_fan_electric_kW=0., recovery_cooling_kW=0.),
        diesel_local_response=dict(mechanical_work_kWh=0., electrical_work_kWh=0.,
            loss_work_kWh=0., rotor_delta_kWh=0., fuel_budget_debit_kWh_equivalent=0.),
        pre_state=dict(diesel_energy_remaining_kWh=100.), post_state=dict(diesel_energy_remaining_kWh=100.))


def payload(*segments):
    return dict(segments=list(segments), continuation_start_minute=0.,
        summary=dict(requested_end_minute=segments[-1]['end_minute'] if segments else 60.),
        m21_initial_storage=dict(ups=0., cold_store=0.))


def audit(p, result=None):
    return api().audit_payload(p, result or {}, parameters=PARAMETERS, tariffs_by_hour={12: .2})


def test_integrates_actual_unequal_segment_durations_and_all_priorities():
    a, b = segment(), segment(30., 45.)
    b['service_request_ledger']['original_demand_kW'][0][0] = 20.
    b['service_request_ledger']['delivered_kW'][0][0] = 17.
    b['service_request_ledger']['active_cut_kW'][0][0] = 1.
    b['service_request_ledger']['connected_unserved_kW'][0][0] = 2.
    out = audit(payload(a,b))
    e = out['carrier_service']['P0']['e']
    assert e['requested_kWh'] == pytest.approx(10.)
    assert e['delivered_kWh'] == pytest.approx(9.25)
    assert e['ENS_kWh'] == pytest.approx(.75)
    assert e['active_cut_kWh'] == pytest.approx(.25)
    assert e['connected_unserved_kWh'] == pytest.approx(.5)
    assert list(out['carrier_service']) == ['P0','P1','P2','P3']
    assert out['observation']['observed_duration_minutes'] == 45.


def test_electric_and_cooling_balance_counts_station_aggregate_once():
    out = audit(payload(segment()))
    for carrier in ('e','c'):
        assert out['balances'][carrier]['maximum_absolute_residual_kW'] == pytest.approx(0.)
        assert out['balances'][carrier]['check'] == 'pass'


def test_signed_cancellation_cannot_hide_segment_balance_failures():
    out = audit(payload(segment(grid=15.), segment(30., 60., grid=13.)))
    e = out['balances']['e']
    assert e['signed_residual_kWh'] == pytest.approx(0.)
    assert e['absolute_residual_kWh'] == pytest.approx(1.)
    assert e['maximum_absolute_residual_kW'] == 1.
    assert e['check'] == 'fail'


def test_unmetered_steam_dump_remains_unknown_and_negative_surplus_fails():
    p = payload(segment())
    p['segments'][0]['action']['hrsg_steam_lbph'] = 20.
    out = audit(p)['balances']['steam']
    assert out['minimum_source_surplus_lbph'] == pytest.approx(20.)
    assert out['full_closure_check'] == 'unknown'
    assert out['maximum_absolute_residual_lbph'] is None
    p['segments'][0]['action']['boiler_steam_lbph'] = 0.
    p['segments'][0]['action']['hrsg_steam_lbph'] = 0.
    assert audit(p)['balances']['steam']['source_sufficiency_check'] == 'fail'


def test_raw_cost_ignores_poisoned_producer_cost_and_adds_shaft_difference_once():
    s = segment()
    s['action']['diesel_kW'] = 2.
    s['action']['grid_import_kW'] = 12.
    s['diesel_local_response'].update(mechanical_work_kWh=1.2, electrical_work_kWh=1., loss_work_kWh=.2)
    s['cost_USD_step'] = 9999999.
    p = payload(s)
    p['m21_initial_storage'] = dict(ups=2., cold_store=4.)
    out = audit(p, dict(metrics=dict(economics=dict(actual_fee_USD2001=9999999.))))['economics']
    expected_prep = (2.+4.*(.8/4.+.02))*.2
    expected = 6.*.2 + 1.*5. + 5.*.3/.8/300.*5. + 1.2*.3 + expected_prep
    assert out['actual_fee_USD2001'] == pytest.approx(expected)
    assert out['DG_electric_fee_USD2001'] == pytest.approx(.3)
    assert out['DG_shaft_difference_USD2001'] == pytest.approx(.06)
    assert out['initial_preparation_fee_USD2001'] == pytest.approx(expected_prep)
    assert out['producer_economics_used'] is False
    assert out['cost_USD_step_used'] is False


def test_continuation_never_charges_original_preparation_a_second_time():
    p = payload(segment(30.,60.))
    p['continuation_start_minute'] = 30.
    p['m21_initial_storage'] = dict(ups=200., cold_store=400.)
    out = audit(p)['economics']
    assert out['initial_preparation_fee_USD2001'] == 0.
    assert out['initial_preparation_reason'] == 'continuation_not_recharged'


def test_missing_receipts_parameters_and_unobserved_tail_are_unknown():
    p = payload(segment())
    p['summary']['requested_end_minute'] = 60.
    del p['segments'][0]['diesel_local_response']
    del p['segments'][0]['action']['station_auxiliary_kW']
    out = api().audit_payload(p, {}, parameters={}, tariffs_by_hour={})
    assert out['observation']['unknown_tail_minutes'] == 30.
    assert out['economics']['actual_fee_USD2001'] is None
    assert out['economics']['unobserved_tail_fee_USD2001'] is None
    assert out['balances']['e']['check'] == 'unknown'
    assert out['storage']['ups']['balance_residual_kWh'] is None


def test_storage_endpoint_balance_and_chain_are_independent_of_recorded_residual():
    p = payload(segment(),segment(30.,60.))
    rows = [dict(initial_kWh=10., final_kWh=9., charged_input_kWh=1., discharged_kWh=1.,
                 standing_loss_kWh=.5, conversion_loss_kWh=.5, balance_residual_kWh=0.),
            dict(initial_kWh=8., final_kWh=7., charged_input_kWh=1., discharged_kWh=1.,
                 standing_loss_kWh=.5, conversion_loss_kWh=.5, balance_residual_kWh=0.)]
    for s,r in zip(p['segments'],rows): s['m21_storage'] = dict(ups=r)
    p['terminal_snapshot'] = dict(snapshot_time_minute=60., inventories=dict(ups=dict(stock_kWh=6.)))
    out = audit(p)['storage']['ups']
    assert out['balance_residual_kWh'] == pytest.approx(1.)
    assert out['receipt_chain_maximum_absolute_gap_kWh'] == pytest.approx(1.)
    assert out['terminal_minus_receipt_kWh'] == pytest.approx(-1.)
    assert out['check'] == 'fail'


def test_storage_loss_dynamics_recomputed_from_frozen_config_and_actual_powers():
    s = segment()
    # Internally balanced receipt can still violate declared eta and action.
    s['m21_storage'] = dict(ups=dict(initial_kWh=10., final_kWh=10.5,
        charged_input_kWh=.5, discharged_kWh=0., standing_loss_kWh=0.,
        conversion_loss_kWh=0., balance_residual_kWh=0.))
    p = payload(s)
    p['model_config'] = dict(sci_engineering=dict(ups=dict(charge_efficiency=.8,
        discharge_efficiency=.9, loss_per_hour=0., capacity_kWh=20., power_kW=10.)))
    out = audit(p)['storage']['ups']
    assert out['balance_residual_kWh'] == 0.
    assert out['dynamics']['maximum_absolute_residual_kWh'] == pytest.approx(.1)
    assert out['dynamics']['check'] == 'fail'


def test_diesel_energy_and_actual_electrical_work_mismatch_is_detected():
    s = segment()
    s['diesel_local_response'].update(mechanical_work_kWh=2., electrical_work_kWh=1.,
        loss_work_kWh=.25, rotor_delta_kWh=.25)
    out = audit(payload(s))['diesel']
    assert out['energy_balance']['maximum_absolute_residual_kWh'] == pytest.approx(.5)
    assert out['electrical_action_match']['maximum_absolute_residual_kWh'] == pytest.approx(1.)
    assert out['energy_balance']['check'] == 'fail'


def test_no_segments_means_no_observed_zero_energy_or_qualified_balances():
    out = audit(payload())
    assert out['carrier_service']['P0']['e']['requested_kWh'] is None
    assert out['balances']['e']['maximum_absolute_residual_kW'] is None
    assert out['observation']['complete'] is False
    assert out['economics']['actual_fee_USD2001'] is None


def test_overlapping_or_nonpositive_segments_are_rejected():
    with pytest.raises(ValueError, match='segment'):
        audit(payload(segment(),segment(29.,45.)))
    with pytest.raises(ValueError, match='segment'):
        audit(payload(segment(0.,0.)))


def test_reader_does_not_mutate_raw_payload_or_result():
    p = payload(segment()); r = dict(metrics=dict(economics=dict(actual_fee_USD2001=1.)))
    before_p, before_r = deepcopy(p),deepcopy(r)
    audit(p,r)
    assert p == before_p
    assert r == before_r


def test_actual_terminal_can_come_from_result_and_errors_remain_visible():
    p = payload(segment())
    p['segments'][0]['m21_storage'] = dict(ups=dict(initial_kWh=4.,final_kWh=4.,
        charged_input_kWh=0.,discharged_kWh=0.,standing_loss_kWh=0.,conversion_loss_kWh=0.))
    p['solver_errors'] = [dict(category='real_solver_failure')]
    r = dict(status='incomplete',error=dict(type='RuntimeError',message='test evidence'),
        terminal_snapshot=dict(snapshot_time_minute=30.,inventories=dict(ups=dict(stock_kWh=4.))))
    out = audit(p,r)
    assert out['storage']['ups']['terminal_stock_kWh'] == 4.
    assert out['execution_status']['producer_status'] == 'incomplete'
    assert out['execution_status']['producer_error'] == r['error']
    assert out['execution_status']['solver_errors'] == p['solver_errors']
    out['execution_status']['solver_errors'].clear()
    assert p['solver_errors']


def make_saved_run(tmp_path):
    repo = tmp_path/'repo'; run = tmp_path/'run'
    run.mkdir()
    names = ('configs/plant_configs.json','inputs/loads/hospital_ies_demand_8760.csv')
    for name in names: (repo/name).parent.mkdir(parents=True,exist_ok=True)
    original = Path(__file__).parents[1]/names[0]
    (repo/names[0]).write_bytes(original.read_bytes())
    (repo/names[1]).write_text('hour_of_year,timestamp\n12,2026-01-05T10:00:00\n',encoding='utf8')
    bindings = dict(source={},inputs={})
    for name in names:
        bindings['source' if name.startswith('configs') else 'inputs'][name] = hashlib.sha256((repo/name).read_bytes()).hexdigest()
    p = payload(segment())
    p['model_config'] = dict(sci_engineering=dict(cold_pump_kW_per_kW=.02))
    with gzip.open(run/'trajectory.json.gz','wt',encoding='utf8') as stream: json.dump(p,stream)
    r = dict(raw_sha256=hashlib.sha256((run/'trajectory.json.gz').read_bytes()).hexdigest(),git_commit='synthetic_integrity_fixture')
    (run/'RESULT.json').write_text(json.dumps(r),encoding='utf8')
    (run/'BINDINGS.json').write_text(json.dumps(bindings),encoding='utf8')
    return repo,run


def test_saved_run_reads_only_hash_bound_sources_and_records_identity(tmp_path):
    repo,run = make_saved_run(tmp_path)
    before = {p.name:p.read_bytes() for p in run.iterdir()}
    out = api().audit_run(run,repo=repo)
    assert out['provenance']['producer_files_unchanged'] is True
    assert out['provenance']['producer_git_commit'] == 'synthetic_integrity_fixture'
    assert len(out['provenance']['producer_files_sha256']) == 3
    assert {p.name:p.read_bytes() for p in run.iterdir()} == before
    assert out['economics']['grid_fee_USD2001'] == pytest.approx(7.*.07356)


def test_saved_run_rejects_unbound_or_changed_source_and_changed_raw(tmp_path):
    repo,run = make_saved_run(tmp_path)
    source = repo/'configs/plant_configs.json'
    source.write_bytes(source.read_bytes()+b'\n')
    with pytest.raises(ValueError,match='SHA mismatch'):
        api().audit_run(run,repo=repo)
    r = json.loads((run/'RESULT.json').read_text(encoding='utf8')); r['raw_sha256'] = '0'*64
    (run/'RESULT.json').write_text(json.dumps(r),encoding='utf8')
    with pytest.raises(ValueError,match='raw SHA'):
        api().audit_run(run,repo=repo)


def test_cli_returns_nonzero_for_a_definite_observed_failure(tmp_path):
    repo,run = make_saved_run(tmp_path)
    output = tmp_path/'audit'
    # This fixture's converter coefficients differ from the frozen source,
    # producing a real, independently visible electric-balance failure.
    assert api().main(['--run',str(run),'--repo',str(repo),'--output',str(output)]) == 2
    out = json.loads((output/'LEDGER.json').read_text(encoding='utf8'))
    assert out['audit_status'] == 'failed'
    assert 'balance.e' in out['failed_checks']
    assert 'steam.full_closure' in out['unknown_checks']


def test_uncertain_steam_and_truncated_tail_are_explicit_unknown_not_passed():
    p = payload(segment())
    p['summary']['requested_end_minute'] = 60.
    out = audit(p)
    assert out['audit_status'] == 'unknown'
    assert out['failed_checks'] == []
    assert 'observation.complete' in out['unknown_checks']
    assert 'steam.full_closure' in out['unknown_checks']
    assert 'balance.e' in out['checks_passed']


def test_missing_result_is_unknown_and_does_not_prevent_reading_actual_prefix(tmp_path):
    repo,run = make_saved_run(tmp_path)
    (run/'RESULT.json').unlink()
    out = api().audit_run(run,repo=repo)
    assert out['execution_status']['producer_status'] is None
    assert out['provenance']['missing_producer_files'] == ['RESULT.json']
    assert 'identity.producer_raw_sha256' in out['unknown_checks']
    assert out['observation']['observed_duration_minutes'] == 30.
