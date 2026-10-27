"""Synthetic native-ledger fixtures; these are not hospital simulation runs."""
import copy
import importlib
import importlib.util
import hashlib
import json

import pytest


def api():
    try:
        spec = importlib.util.find_spec('tools.model_review_v30.evaluation')
    except ModuleNotFoundError:
        spec = None
    assert spec is not None, 'V30 evaluation exporter is missing'
    return importlib.import_module('tools.model_review_v30.evaluation')


def fixture():
    zero = [[0., 0., 0.] for _ in range(4)]
    request = copy.deepcopy(zero); request[0] = [10., 6., 8.]
    delivered = copy.deepcopy(request); delivered[0][2] = 4.
    unmet = copy.deepcopy(zero); unmet[0][2] = 4.
    def segment(a, b, restored=False):
        return dict(start_minute=a, end_minute=b, hour_of_year=1,
            service_request_ledger=dict(original_demand_kW=request, active_cut_kW=zero,
                connected_unserved_kW=zero if restored else unmet,
                delivered_kW=request if restored else delivered),
            action=dict(grid_import_kW=2., gt_fuel_MMBtu_h=3., boiler_steam_lbph=4.,
                diesel_kW=5., gt_electric_kW=[0., 2., 0.]),
            diesel_local_response=dict(mechanical_work_kWh=(b-a)/60.*6., electrical_work_kWh=(b-a)/60.*5.),
            grid_tariff_USD_kWh=.2, cost_USD_step=50.,
            post_state=dict(gt_prev=[7., 2., 0.], diesel_status='RUNNING', diesel_energy_remaining_kWh=94.),
            m21_storage={'cold_store': dict(initial_kWh=60. if a == 0 else 58., final_kWh=58. if a == 0 else 57.,
                charged_input_kWh=2., discharged_kWh=3., standing_loss_kWh=.5,
                conversion_loss_kWh=.5, balance_residual_kWh=0.)})
    p = dict(identity={'policy_id': 'synthetic'}, scenario={},
        summary=dict(status='completed', observed_until_minute=61., requested_end_minute=61., completed=True),
        segments=[segment(0., 60.), segment(60., 61., True)], events=[
            dict(kind='gt_start_requested', device='GT_1', time_minute=45., ready_minute=55.),
            dict(kind='gt_start_completed', device='GT_1', time_minute=55.)],
        m21_initial_storage={'cold_store': 60./.95, 'ups': 0.},
        accounting_parameters=dict(steam_kWh_per_lb=.3, boiler_eff=.8, kWh_per_MMBtu=293.0710702,
            gas_price_usd_per_mmbtu=4.84, dg_shaft_fee_USD_per_kWh=.30,
            ec_electric_kW_per_rt=.77, RT_TO_KW=3.51685284, cold_pump_kW_per_kW=.02),
        model_config={'sci_engineering': {'cold_pump_kW_per_kW': .02}},
        evidence_kind='synthetic_test_fixture')
    r = dict(status='completed', observed_until_minute=61., requested_end_minute=61.,
        metrics={'economics': dict(energy_cost_USD=999., DG_fee_correction_USD=.305,
            initial_inventory_fee_USD_conditional=1.4,
            source_economics={'actual_energy_fee_USD': 12.})},
        git_commit='fixture', source_sha256='fixture', raw_sha256='fixture')
    return p, r


def test_actual_fee_excludes_objective_and_adds_only_dg_difference_and_initial_once():
    p, r = fixture(); out = api().export_evaluation(p, r)
    assert out['economics']['actual_fee_USD2001'] == pytest.approx(13.705)
    assert out['economics']['legacy_energy_cost_USD'] == 999.
    assert out['economics']['DG_fee_correction_USD'] == .305
    assert out['economics']['recharge_fee_added_again'] is False


def test_storage_stock_and_preparation_have_distinct_carriers_and_sources():
    p, r = fixture(); out = api().export_evaluation(p, r)['storage']['cold_store']
    assert out['initial_stock']['value'] == 60.
    assert out['preparation_input']['value'] == pytest.approx(60./.95)
    assert out['preparation_input']['unit'] == 'kWh_c'
    assert out['preparation_grid_purchase']['unit'] == 'kWh_e'
    assert out['preparation_grid_purchase']['value'] == pytest.approx(15.091310837118254)
    assert out['actual_charge_input']['value'] == 4.
    assert out['actual_discharge']['value'] == 6.
    assert out['final_stock']['value'] == 57.
    assert out['balance_residual']['value'] == pytest.approx(-1.)
    assert out['recorded_balance_residual_sum']['value'] == 0.


def test_native_request_minus_delivery_keeps_small_ens_and_original_recovery_rule():
    p, r = fixture(); out = api().export_evaluation(p, r)
    row = out['carrier_service']['P0']['c']
    assert row['ENS_kWh'] == pytest.approx(4.)
    assert row['requested_kWh'] == pytest.approx(8.*61./60.)
    assert row['actual_recovery_minute'] == 60.
    assert row['confirmed_recovery_minute'] == 61.
    assert row['final_status'] == 'served'
    assert out['carrier_service']['P0']['e']['recovery_status'] == 'not_applicable'
    assert out['carrier_service']['P0']['e']['actual_recovery_minute'] is None


def test_terminal_uses_last_post_state_and_actual_events_not_last_decision():
    p, r = fixture(); p['decisions'] = [dict(decision_time_min=45., operating_readiness={'GT_1': 'STARTING'})]
    out = api().export_evaluation(p, r)['terminal_snapshot']
    assert out['time_minute'] == 61.
    assert out['GT']['GT_1']['actual_output_kW_e'] == 7.
    assert out['GT']['GT_1']['last_actual_start_completed_minute'] == 55.
    assert out['pending_commands'] is None
    assert out['recovery_queue'] is None
    assert out['completeness'] == 'partial_historical_reconstruction'


def test_live_snapshot_is_copied_and_export_has_no_input_mutation():
    p, r = fixture(); p['terminal_snapshot'] = {'source': 'actual_core_state', 'time_minute': 61., 'pending_commands': []}
    before = json.dumps([p, r], sort_keys=True)
    out = api().export_evaluation(p, r)
    assert json.dumps([p, r], sort_keys=True) == before
    assert out['terminal_snapshot'] == p['terminal_snapshot']
    out['terminal_snapshot']['pending_commands'].append('changed')
    assert p['terminal_snapshot']['pending_commands'] == []


def test_synthetic_truncation_does_not_reuse_full_cost_or_recovery_and_tail_is_unknown():
    p, r = fixture(); p['segments'] = p['segments'][:1]
    p['summary'].update(status='synthetic_truncated', observed_until_minute=60., completed=False)
    p.pop('accounting_parameters')
    out = api().export_evaluation(p, r)
    assert out['observation']['complete'] is False
    assert out['observation']['unknown_tail_minutes'] == 1.
    assert out['economics']['actual_fee_USD2001'] is None
    assert out['economics']['unobserved_tail_fee_USD2001'] is None
    row = out['carrier_service']['P0']['c']
    assert row['actual_recovery_minute'] is None
    assert row['recovery_status'] == 'censored'
    assert row['final_status'] == 'unserved_at_observation_end_unknown_tail'


def test_no_segments_means_unknown_supply_cost_and_terminal_instead_of_zeros():
    p, r = fixture(); p['segments'] = []; p['summary'].update(observed_until_minute=0., completed=False)
    out = api().export_evaluation(p, r)
    assert out['carrier_service']['P0']['e']['ENS_kWh'] is None
    assert out['terminal_snapshot']['time_minute'] is None
    assert out['economics']['actual_fee_USD2001'] is None
    assert out['independent_audit']['actual_fee_USD2001'] is None
    assert out['execution_status']['actual_service_material_interruption'] is None


def test_independent_raw_cost_ignores_recorded_cost_and_economics_aggregates():
    p, r = fixture(); audit = api().independent_energy_audit(p, accounting_parameters=p['accounting_parameters'])
    dt = 61./60.
    raw_fee = (2.*.2 + 3.*4.84 + 4.*.3/.8/293.0710702*4.84 + 6.*.30)*dt
    prep = (60./.95)*(.77/3.51685284+.02)*.2
    assert audit['actual_fee_USD2001'] == pytest.approx(raw_fee+prep)
    assert audit['DG_shaft_work_kWh'] == pytest.approx(6.*dt)
    assert audit['DG_electric_work_kWh'] == pytest.approx(5.*dt)
    assert audit['accounting_objective_cost_used'] is False
    for s in p['segments']: s['cost_USD_step'] = 987654321.
    assert api().independent_energy_audit(p, accounting_parameters=p['accounting_parameters']) == audit


def test_independent_audit_missing_shaft_or_tariff_is_unknown_not_zero():
    p, _ = fixture(); p['segments'][0]['diesel_local_response'].pop('mechanical_work_kWh')
    audit = api().independent_energy_audit(p, accounting_parameters=p['accounting_parameters'])
    assert audit['actual_fee_USD2001'] is None
    assert audit['DG_shaft_work_kWh'] is None
    assert 'DG_shaft_work_kWh' in audit['unknown_fields']


def test_missing_native_service_ledger_is_explicitly_rejected():
    p, r = fixture(); p['segments'][0].pop('service_request_ledger')
    with pytest.raises(ValueError, match='service ledger'):
        api().export_evaluation(p, r)


def test_pending_start_is_never_inferred_complete_from_projected_ready_time():
    p, r = fixture(); p['events'] = p['events'][:1]
    p['segments'][-1]['post_state']['gt_prev'][0] = 0.
    out = api().export_evaluation(p, r)['terminal_snapshot']['GT']['GT_1']
    assert out['actual_state'] == 'unknown_pending_start'
    assert out['last_actual_start_completed_minute'] is None


def test_frozen_accounting_reader_accepts_native_utf8_bom_csv(tmp_path):
    config_path = tmp_path/'configs/plant_configs.json'; config_path.parent.mkdir()
    csv_path = tmp_path/'inputs/loads/hospital_ies_demand_8760.csv'; csv_path.parent.mkdir(parents=True)
    config = dict(engineering_assumptions_not_from_source={'kWh_thermal_per_lb_steam': .3},
        historical_pnnl_performance={'boiler_thermal_efficiency': .8, 'existing_electric_chiller_all_in_kW_per_RT': .77},
        historical_2001_prices_USD={'gas_per_MMBtu': 4.84, 'electricity_onpeak_per_kWh': .09256,
            'electricity_midpeak_per_kWh': .07356, 'electricity_offpeak_per_kWh': .05456})
    config_path.write_text(json.dumps(config), encoding='utf-8')
    csv_path.write_text('timestamp,hour_of_year\n2017-01-09 12:00,1\n', encoding='utf-8-sig')
    bindings = {'source': {'configs/plant_configs.json': hashlib.sha256(config_path.read_bytes()).hexdigest()},
        'inputs': {'inputs/loads/hospital_ies_demand_8760.csv': hashlib.sha256(csv_path.read_bytes()).hexdigest()}}
    _, tariff, _ = api()._load_frozen_accounting(tmp_path, bindings, [{'hour_of_year': 1}])
    assert tariff == {1: .09256}


def test_plant_conversion_electricity_is_separate_from_priority_direct_demand():
    p, r = fixture(); p['accounting_parameters']['abs_aux_kW_per_rt'] = .14
    for segment in p['segments']:
        segment['action'].update(electric_chiller_cooling_kW=[3.51685284, 0., 0.],
            absorption_cooling_kW=703.370568, gt_start_electric_kW=10.)
    out = api().export_evaluation(p, r)['electric_components']
    plant = out['plant_conversion_and_startup']
    assert plant['electric_chiller_input_kWh_e'] == pytest.approx(.77*61./60.)
    assert plant['absorption_auxiliary_kWh_e'] == pytest.approx(28.*61./60.)
    assert plant['gt_start_electric_kWh_e'] == pytest.approx(10.*61./60.)
    assert out['plant_is_priority_direct_demand'] is False


def test_submaterial_ens_is_retained_without_creating_material_interruption():
    p, r = fixture(); p['segments'] = p['segments'][1:]
    p['segments'][0]['start_minute'] = 0.
    ledger = p['segments'][0]['service_request_ledger']
    ledger['delivered_kW'] = copy.deepcopy(ledger['original_demand_kW'])
    ledger['delivered_kW'][0][0] -= 1e-5
    ledger['connected_unserved_kW'] = copy.deepcopy(ledger['active_cut_kW'])
    ledger['connected_unserved_kW'][0][0] = 1e-5
    row = api().export_evaluation(p, r)['carrier_service']['P0']['e']
    assert row['ENS_kWh'] > 0.
    assert row['ENS_kWh'] == pytest.approx(1e-5*61./60.)
    assert row['interruption_count'] == 0
    assert row['recovery_status'] == 'not_applicable'

