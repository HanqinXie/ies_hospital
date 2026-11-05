"""Pure mechanism-case and raw-evidence checks; no producer is launched."""
import importlib
from pathlib import Path
import sys

import pytest

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE))


def implementation():
    try:
        return importlib.import_module('scripts.mechanism_validation')
    except ModuleNotFoundError as error:
        if error.name != 'scripts.mechanism_validation':
            raise
        pytest.fail('Active EC and boiler mechanism gate is missing', pytrace=False)


def test_four_canonical_cases_are_bounded_with_fixed_seen_backgrounds():
    cases = implementation().mechanism_cases()
    assert set(cases) == {kind+'_'+phase for kind in ('ec','boiler','tower','pump') for phase in ('healthy','fault')}
    for name, case in cases.items():
        p = case['annual_program']['parameters']
        assert p['method'] == 'M_ready' and p['configuration'] == 'H2'
        assert p['duration_minutes'] == 180.
        assert p['start_hour'] == (5072 if name.startswith('pump_') else 1 if name.startswith('boiler_') else 4137)
        assert case['scenario']['shock_by_zone'] == {}
        assert case['scenario']['enable_propagation'] is False
        assert case['annual_program']['calendar_is_online_policy_input'] is False
        assert case['scenario']['selection_allowed'] is False
        assert case['scenario']['fit_allowed'] is False


def test_ec_pair_changes_only_the_registered_single_converter_calendar():
    cases = implementation().mechanism_cases()
    healthy, fault = cases['ec_healthy'], cases['ec_fault']
    assert healthy['policy'] == fault['policy']
    assert healthy['scenario']['model_closure_events'] == []
    assert fault['scenario']['model_closure_events'] == [
        dict(time_minute=60.25, availability={'EC_1': 0.}),
        dict(time_minute=120.25, availability={'EC_1': 1.})]


def test_boiler_pair_has_identical_healthy_gt_background_and_one_external_cause():
    cases=implementation().mechanism_cases()
    healthy=cases['boiler_healthy']['execution_revision']['declared_calendar']
    fault=cases['boiler_fault']['execution_revision']['declared_calendar']
    assert healthy==[]
    assert fault==[dict(time_minute=60.25,availability={'BOILER_1':0.}),dict(time_minute=120.25,availability={'BOILER_1':1.})]


def test_mechanism_scope_never_claims_isolated_boiler_annual_validation():
    scope = implementation().mechanism_plan()
    assert scope['boiler']['common_background'] == 'healthy_GT'
    assert scope['boiler']['isolated_boiler_annual_result'] is False
    assert scope['ec']['source_hour'] == 4137
    assert scope['blind_or_unseen'] is False


def test_actual_producer_case_wrapper_is_unwrapped_and_configuration_bound():
    module = implementation()
    case = module.mechanism_cases()['ec_healthy']
    from tools.annual_program.runner import _configuration
    from tools.annual_readiness.registration import load_registry
    saved = dict(case=case, configuration=_configuration(case, load_registry()))
    fn = getattr(module, 'validate_saved_case', None)
    assert callable(fn), 'Actual producer CASE wrapper is not supported'
    assert fn(saved, 'ec_healthy') == case
    saved['configuration']['control_rule_id'] = 'unregistered-rule'
    with pytest.raises(ValueError):
        fn(saved, 'ec_healthy')


def segments(kind, fault=False):
    result = []
    for a, b in zip((0., 60.25, 120.25, 150.25), (60.25, 120.25, 150.25, 180.)):
        hardware = {name: 1. for name in ('GT_1', 'GT_2', 'GT_3', 'EC_1', 'EC_2', 'EC_3',
            'BOILER_1', 'BOILER_2', 'BOILER_3', 'GRID', 'TOWER', 'ABS')}
        active = fault and 60.25 <= a < 120.25
        if active:
            hardware['EC_1' if kind == 'ec' else 'BOILER_1'] = 0.
        result.append(dict(start_minute=a, end_minute=b, hardware_availability=hardware,
            availability=dict(hardware), action=dict(electric_chiller_cooling_kW=[0. if kind == 'ec' and active else 100., 0., 0.],
                boiler_steam_lbph=500., gt_electric_kW=[0., 0., 0.])))
    return result


def evaluate(kind, healthy, fault):
    module = implementation()
    fn = getattr(module, 'evaluate_segments', None)
    assert callable(fn), 'Raw actual mechanism evaluator is missing'
    return fn(kind, healthy, fault)


def test_active_ec_pair_has_positive_healthy_exposure_and_zero_fault_output():
    result = evaluate('ec', segments('ec'), segments('ec', True))
    assert result['status'] == 'passed'
    assert result['healthy_pre_event_EC1_kWh_c'] > 0.
    assert result['healthy_fault_window_EC1_kWh_c'] == 100.
    assert result['fault_window_EC1_kWh_c'] == 0.


def test_inactive_healthy_ec_during_fault_window_is_not_qualified():
    healthy = segments('ec')
    healthy[1]['action']['electric_chiller_cooling_kW'][0] = 0.
    with pytest.raises(ValueError, match='active|exposure'):
        evaluate('ec', healthy, segments('ec', True))


def test_positive_initial_transient_does_not_replace_positive_pre_event_exposure():
    healthy = segments('ec')
    healthy[0]['action']['electric_chiller_cooling_kW'][0] = 0.
    with pytest.raises(ValueError, match='active|exposure'):
        evaluate('ec', healthy, segments('ec', True))


def test_boiler_capacity_change_is_actual_registered_3000_to_2000_to_3000():
    result = evaluate('boiler', segments('boiler'), segments('boiler', True))
    assert result['status'] == 'passed'
    assert result['available_capacity_lbph'] == dict(before=3000., during=2000., after=3000.)
    assert result['healthy_pre_event_steam_lb'] > 0.
    assert result['fault_pre_event_steam_lb'] > 0.
    assert result['isolated_boiler_annual_result'] is False


def test_zero_actual_boiler_steam_is_retained_as_unqualified():
    healthy = segments('boiler')
    healthy[0]['action']['boiler_steam_lbph'] = 0.
    with pytest.raises(ValueError, match='active|exposure'):
        evaluate('boiler', healthy, segments('boiler', True))


@pytest.mark.parametrize('unknown', [None, True, float('nan'), float('inf'), '0'])
def test_unknown_or_malformed_actual_flow_cannot_be_zero_filled(unknown):
    fault = segments('ec', True)
    fault[1]['action']['electric_chiller_cooling_kW'][0] = unknown
    with pytest.raises(ValueError):
        evaluate('ec', segments('ec'), fault)


def test_calendar_mismatch_or_unrelated_fault_cannot_be_ignored():
    fault = segments('ec', True)
    fault[1]['hardware_availability']['EC_2'] = 0.
    with pytest.raises(ValueError, match='availability|calendar'):
        evaluate('ec', segments('ec'), fault)


def test_gap_and_unsplit_fault_boundary_are_not_interpolated():
    fault = segments('ec', True)
    fault[1]['start_minute'] += .25
    with pytest.raises(ValueError, match='contiguous|coverage|boundary'):
        evaluate('ec', segments('ec'), fault)


def test_target_gate_registers_four_mechanism_producers_and_four_auditors(tmp_path):
    module = implementation()
    commands = module.mechanism_commands(PACKAGE, tmp_path)
    assert len(commands) == 16
    assert sum('tools.annual_program.runner' in command for command in commands.values()) == 8
    assert sum(any(item.endswith('audit_engineering_year.py') for item in command) for command in commands.values()) == 8
    assert all('--accepted-result-cache' not in command for command in commands.values())


def test_no_mechanism_raw_evidence_means_no_target_gate(tmp_path):
    with pytest.raises(ValueError, match='Missing|invalid'):
        implementation().verify_mechanism_checks(PACKAGE, tmp_path)


def test_old_tower_only_summary_cannot_qualify_the_expanded_target_gate(tmp_path):
    from scripts.server_validation import verify_gate
    old = dict(status='passed', kind='busy_year_target_scientific_gate', producer_concurrency=1,
        actual_producer_invocations=3, accepted_result_cache=False, artifacts=[])
    with pytest.raises(ValueError, match='incomplete'):
        verify_gate(PACKAGE, tmp_path, old)
