"""Matched factorial registration preserves the existing M25 physical contract."""
import copy
import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'src')]


def api():
    try:
        return importlib.import_module('tools.model_correction_v26.combined')
    except ModuleNotFoundError as exc:
        if exc.name in ('tools.model_correction_v26', 'tools.model_correction_v26.combined'):
            pytest.fail('V26 matched active-path registration is missing')
        raise


def original():
    return json.loads((ROOT/'configs/model_correction_v25/cases.json').read_text(encoding='utf-8'))


def selection():
    return dict(source_sha256='47345730795cc15eda9e5b15071ddbedb65e916e4341e561c1f51148c75ea278',
        candidates=[dict(case_id='four_normal_summer', start_hour=4001, boiler_max_lbph=0.,
            pre30_gt2_min_kW=125.2075521, pre30_minutes=30., native_sha256='beac067b7d622f446d463ed25a481ba5ff3b0c7bc12eda0a3613fc59d325b154'),
            dict(case_id='four_normal_winter_optional', start_hour=211, boiler_max_lbph=0.,
                pre30_gt2_min_kW=0., pre30_minutes=30., native_sha256='1ffc20d66465134a8017dd2a9311e819f5a253887e483bfdc5a2be5ae40400de')])


def built():
    return api().build_registry(original(), selection())


def main_cases(registry):
    return {c['factorial_variant']: c for c in registry['cases'] if 'factorial_variant' in c}


def test_exact_four_cases_plus_one_public_smoke_and_original_physics_identity():
    mod = api(); registry = built()
    assert [c['id'] for c in registry['cases']] == ['combined_N', 'combined_F', 'combined_D', 'combined_FD', 'smoke_normal']
    assert set(main_cases(registry)) == {'N', 'F', 'D', 'FD'}
    assert registry['physical_model_id'] == mod.MODEL_ID == 'M25_model_correction'
    assert registry['physical_contract_id'] == mod.CONTRACT_ID == 'hospital_ies.model_correction.v25.v1'
    assert registry['study_id'] == mod.STUDY_ID == 'hospital_ies.study.v26.v1'
    assert registry['schemas']['model_correction_v26_study'] == mod.STUDY_ID
    assert registry['hardware_profiles'] == {'H0': original()['hardware_profiles']['H0']}
    assert registry['model_correction_v25'] == original()['model_correction_v25']


def test_common_physical_initialization_and_rules_are_identical():
    cases = main_cases(built()); views = [api().common_spec(c) for c in cases.values()]
    assert all(v == views[0] for v in views)
    for case in cases.values():
        s = case['scenario']
        assert (s['start_hour'], s['duration_steps'], s['step_minutes']) == (4001, 12, 15.)
        assert s['response_delay_minutes'] == 30.
        assert s['initialization_seed'] == 20261023
        assert s['grid_limit_kW'] == s['pre_event_grid_limit_kW'] == 850.
        assert case['configuration'] == 'H0' and case['policy'] == {'alias': 'C', 'config': {}}
        assert case['operating_rule_id'] == 'current_service_bridge'
        assert s['model_closure_events'] == [] and s['exogenous_events'] == []
    assert len({c['scenario']['base_state_id'] for c in cases.values()}) == 1
    assert len({c['scenario']['exogenous_stream_id'] for c in cases.values()}) == 4


def test_only_registered_root_and_original_supported_zonal_increment_differ():
    cases = main_cases(built()); old = next(c for c in original()['cases'] if c['id'] == 'four_L4_boiler_ER_autumn')
    for variant, case in cases.items():
        s = case['scenario']
        assert s['faults'] == ({'GT_2': 0.} if 'F' in variant else {})
        assert s['shock_by_zone'] == (old['scenario']['shock_by_zone'] if 'D' in variant else {})
        assert s['shock'] == s['shock_by_priority'] == s['auxiliary_faults'] == {}
        assert s['fault_start_minute'] == s['shock_start_minute'] == 30.
        assert s['fault_duration_minutes'] == s['shock_duration_minutes'] == 60.
        assert s['history_interval_minutes'] == [0., 30.]
        assert s['tail_interval_minutes'] == [90., 180.]
        assert s['actual_p0_load_multiplier'] == {'e': 1., 'h': 1., 'c': 1.}
    assert [cases[v]['scenario']['canonical_class'] for v in ('N', 'F', 'D', 'FD')] == ['NORMAL', 'L2', 'L3', 'L4']


def test_real_native_parser_and_stage_selected_delay_protection_clocks_match():
    from tools.model_correction_v25.environment import validate_scenario
    from tools.model_closure_v22.validation import validate_exporter_metadata
    from hospital_ies.state_transition import TransitionCore, event_window
    for case in main_cases(built()).values():
        scene = case['scenario']; validate_scenario(scene); validate_exporter_metadata(scene)
        core = SimpleNamespace(env=SimpleNamespace(scenario=scene, dt_minutes=15.))
        assert [TransitionCore.delayed_at(core, t) for t in (0., 29.9, 30., 59.9, 60., 90.)] == [
            (False, 60.), (False, 60.), (True, 60.), (True, 60.), (False, 60.), (False, 60.)]
        kind = 'fault' if scene['stage'] in (1, 3) else 'shock'
        assert event_window(scene, kind) == (30., 90.)
        assert scene['enable_propagation'] is True


def test_stage_parser_labels_are_explicit_and_do_not_relabel_physical_classes():
    cases = main_cases(built())
    assert {v: (c['scenario']['stage'], c['scenario']['event_class']) for v, c in cases.items()} == {
        'N': (3, 'L3'), 'F': (3, 'L3'), 'D': (4, 'L4'), 'FD': (4, 'L4b')}
    assert all(c['scenario']['native_event_class_is_parser_encoding_only'] for c in cases.values())
    assert all(c['scenario']['reference_phase_clocks_are_analysis_only'] for c in cases.values())


def test_future_repair_schedule_is_not_added_to_policy_or_hardware():
    registry = built()
    for case in registry['cases']:
        assert case['policy'] == {'alias': 'C', 'config': {}}
        assert 'operating_rule' not in case
    assert registry['operating_rules'] == {'current_service_bridge': {'name': 'current_service_bridge'}}
    assert registry['model_correctness_v23']['thermal_coupling'] is None


def test_normal_public_entry_is_short_healthy_and_uses_same_hardware_control():
    from tools.model_correction_v25.environment import validate_scenario
    from tools.model_closure_v22.validation import validate_exporter_metadata
    case = built()['cases'][-1]; s = case['scenario']
    validate_scenario(s); validate_exporter_metadata(s)
    assert case['id'] == 'smoke_normal' and s['duration_steps']*s['step_minutes'] == 15.
    assert s['faults'] == s['shock_by_zone'] == {}
    assert s['response_delay_minutes'] == 30.
    assert s['fault_start_minute'] == s['shock_start_minute'] == 0.


@pytest.mark.parametrize('change', ['boiler', 'missing_coverage', 'idle_gt', 'source'])
def test_selection_requires_actual_existing_active_path_and_original_source(change):
    evidence = selection()
    if change == 'boiler': evidence['candidates'][1]['boiler_max_lbph'] = 2001.
    elif change == 'missing_coverage': evidence['candidates'][0]['pre30_minutes'] = 15.
    elif change == 'idle_gt': evidence['candidates'][0]['pre30_gt2_min_kW'] = 0.
    else: evidence['source_sha256'] = 'unknown'
    with pytest.raises(ValueError): api().build_registry(original(), evidence)


def test_construction_does_not_modify_v25_registry_or_selection():
    old = original(); evidence = selection(); before = copy.deepcopy((old, evidence))
    api().build_registry(old, evidence)
    assert (old, evidence) == before


def test_mismatched_common_setting_is_rejected_before_execution():
    registry = built(); registry['cases'][1]['scenario']['response_delay_minutes'] = 15.
    with pytest.raises(ValueError, match='common'): api().validate_registration(registry)


def test_static_shipped_registry_matches_registered_builder_and_run_plan():
    registry = json.loads((ROOT/'configs/model_correction_v26/cases.json').read_text(encoding='utf-8'))
    api().validate_registration(registry)
    assert main_cases(registry) == main_cases(built())
    plan = json.loads((ROOT/'configs/model_correction_v26/RUN_PLAN.json').read_text(encoding='utf-8'))
    assert [r['case'] for r in plan['runs']] == [c['id'] for c in registry['cases']]
    assert all(r['policy'] == 'C' and r['configuration'] == 'H0' and r['operating_rule'] == 'current_service_bridge' for r in plan['runs'])


def test_public_entry_reuses_common_m25_route_and_does_not_invoke_environment(monkeypatch):
    entry = importlib.import_module('tools.model_correction_v26.validation'); calls = []
    monkeypatch.setattr(entry, 'common_main', lambda argv, **kwargs: calls.append((argv, kwargs)) or 17)
    assert entry.main(['--list']) == 17
    assert calls[0][1]['model_version'] == 'v25'
    assert calls[0][1]['registry_path'] == ROOT/'configs/model_correction_v26/cases.json'
