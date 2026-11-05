"""Specification and grouping tests only: no hospital environment is created."""
import copy
import importlib.util
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
MODULE = ROOT / 'tools/p2b_experiment/design.py'


def design_module():
    assert MODULE.exists(), 'P2B pure experiment design module is missing'
    spec = importlib.util.spec_from_file_location('p2b_design_under_test', MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope='module')
def designed():
    module = design_module()
    return module, module.build_design(ROOT)


def test_design_deterministic_exact_budget_and_effective_groups(designed):
    module, design = designed
    assert design == module.build_design(ROOT)
    assert design['counts'] == dict(specifications=400, root_families=380,
        used_time_blocks=80, reserved_time_blocks=12, connected_groups=80,
        planned_policy_trajectories=800,
        role_specifications=dict(pilot_train=240, pilot_validation=60,
                                 development_boundary=100))
    assert len({s['id'] for s in design['scenarios']}) == 400
    assert len(design['assignments']) == 400


def test_sampling_seed_does_not_change_original_initialization_seed():
    module = design_module()
    design = module.build_design(ROOT, seed=20261014)
    assert design['seed'] == 20261014
    assert design['initialization_seed'] == 20261012
    assert all(s['initialization_seed'] == 20261012 for s in design['scenarios'])


def test_default_seed_and_physical_cause_metadata_match_preregistration(designed):
    _, design = designed
    assert design['seed'] == 20261014
    assert design['coverage']['protection_cause_classes'] == [
        'residual_stress_protection', 'no_export_isolation',
        'minimum_output_interlock', 'exogenous_device_fault']
    assert 'Only GT overload' not in design['coverage']['propagation_model_limit']


def test_l4b_development_variants_are_adjacent_severity_neighbors(designed):
    _, design = designed
    by_id = {s['id']: s for s in design['scenarios']}
    variants = [s for s in design['scenarios'] if s.get('variant_parent_id')]
    assert len(variants) == 20
    for variant in variants:
        parent = by_id[variant['variant_parent_id']]
        dev = variant['initiating_device']
        assert abs(variant['faults'][dev] - parent['faults'][dev]) == .25
        assert variant['root_family_id'] == parent['root_family_id']
        assert variant['exogenous_stream_id'] == parent['exogenous_stream_id']


def test_study_fixed_settings_completely_specify_event_clock(designed):
    _, design = designed
    for stratum in design['study_distribution']['strata'].values():
        assert stratum['fixed_settings']['shock_start_minute'] == 60.
        assert stratum['fixed_settings']['shock_duration_minutes'] == 240.
        assert stratum['fixed_settings']['automatic_fast_shedding'] is False


def test_complete_history_score_tail_and_reset_support(designed):
    module, design = designed
    for scenario in design['scenarios']:
        support = module.support_interval(scenario)
        assert scenario['duration_steps'] == 32
        assert scenario['history_interval_minutes'] == [0, 60]
        assert scenario['score_interval_minutes'] == [60, 420]
        assert scenario['tail_interval_minutes'] == [420, 480]
        assert all(t + 60 <= 480 for t in range(60, 420, 15))
        assert support['raw_start_minute'] == (scenario['start_hour'] - 1) * 60
        assert support['raw_end_minute'] >= support['raw_start_minute'] + 480
        assert support['initialization_source_hours'] == [scenario['start_hour']]
        assert support['initialization_historical_minutes'] == 0
        assert support['algebraic_initialization_iterations'] == 6


def test_time_support_roles_and_reserved_final_are_disjoint(designed):
    _, design = designed
    blocks = design['time_blocks']
    assert all(a['raw_end_minute'] <= b['raw_start_minute'] or
               b['raw_end_minute'] <= a['raw_start_minute']
               for i, a in enumerate(blocks) for b in blocks[:i])
    for block in blocks:
        assert all(block['raw_end_minute'] <= old['raw_start_minute'] or
                   old['raw_end_minute'] <= block['raw_start_minute']
                   for old in design['known_development_support'])
    finals = {b['time_block_id'] for b in blocks if b['role'] == 'final_reserved'}
    assert len(finals) == 12
    assert not any(s['time_block_id'] in finals for s in design['scenarios'])
    assert not any(a['split'] in ('test', 'final_reserved', 'blind')
                   for a in design['assignments'].values())


def test_distribution_separates_prior_validation_and_no_real_probability(designed):
    _, design = designed
    study = design['study_distribution']
    assert study['p_final_reference'] is None
    assert study['p_reference'] is None
    assert study['mixture_weights'] == {'L2_mixed': 0.5, 'L4a_boundary': 0.5}
    for stratum in study['strata'].values():
        assert stratum['continuous_independence_assumed'] is True
        assert len(stratum['basis_measure']['calendar_hour']['values']) == 30
        assert len(stratum['fit_measure']['calendar_hour']['values']) == 24
        assert len(stratum['validation_measure']['calendar_hour']['values']) == 6
        assert set(stratum['fit_measure']['calendar_hour']['values']).isdisjoint(
            stratum['validation_measure']['calendar_hour']['values'])
    for scenario in design['scenarios']:
        assert scenario['p_reference'] is None
        assert scenario['importance_weight'] is None
        assert scenario['sampling_weight_is_failure_probability'] is False


def test_scenarios_valid_and_both_policies_share_frozen_script(designed):
    _, design = designed
    from hospital_ies.resilience import validate_scenario
    assert design['policies'] == ['priority_milp', 'secure_milp']
    for scenario in design['scenarios']:
        validate_scenario(scenario)
        assert scenario['purpose'] == 'development'
        assert scenario['automatic_fast_shedding'] is False
        assert 'precontrol_sheddable_fraction_by_priority' not in scenario
        assert scenario['initialization_seed'] == design['initialization_seed']
        assert scenario['response_clock_anchor'] == (
            'fault_start_minute' if scenario['stage'] in (1, 3) else 'shock_start_minute')
        assert all(event['origin'] == 'exogenous' for event in scenario['exogenous_events'])
    l4b = [s for s in design['scenarios'] if s['event_class'] == 'L4b']
    offsets = {s['fault_start_minute'] - s['shock_start_minute'] for s in l4b}
    assert min(offsets) < 0 < max(offsets) and 0 in offsets


def test_complete_conditional_coverage_marks_missing_cells(designed):
    _, design = designed
    coverage = design['coverage']
    cells = coverage['device_severity_event_class']
    assert len(cells) == 5 * 20 * 4
    assert {'covered', 'unsupported', 'unsampled'} == {c['status'] for c in cells}
    for event_class in ('L1', 'L3', 'L4b'):
        assert len({s['initiating_device'] for s in design['scenarios']
                    if s['event_class'] == event_class}) == 20
    assert coverage['cross_role_overlapping_support_pairs'] == 0
    assert coverage['continuous_conditions']
    for row in coverage['continuous_conditions']:
        assert row['count'] == (120 if row['role'] == 'pilot_train' else 30)
        assert sum(row['shock_e_quartile_counts']) == row['count']
        assert sum(row['shock_c_quartile_counts']) == row['count']


def test_coverage_never_relabels_fixed_study_as_stress_design(designed):
    _, design = designed
    coverage = design['coverage']
    assert coverage['distribution_counts'] == {
        'p_study.p2b.independent_conditional.v1': 300,
        'q_design.p2b.fixed_coverage.v1': 100}
    assert sum(coverage['q_design']['event_class_counts'].values()) == 100
    assert sum(coverage['p_study']['event_class_counts'].values()) == 300
    assert coverage['distribution_id'] == 'p2b.explicit_study_and_coverage.v1'


def test_same_template_type_is_not_same_parent_and_lineage_is_transitive():
    module = design_module()
    rows = [dict(scenario_id='a', root_family_id='ra', parent_instance_id='pa',
                base_state_id='sa', exogenous_stream_id='ea', template_type='L2',
                raw_start_minute=0, raw_end_minute=10, role='pilot_train'),
            dict(scenario_id='b', root_family_id='rb', parent_instance_id='pb',
                base_state_id='sb', exogenous_stream_id='eb', template_type='L2',
                # Approved design adds 60-min embargo on BOTH sides, so keep
                # this independent-template fixture outside that support.
                raw_start_minute=200, raw_end_minute=210, role='pilot_train')]
    assert module.assign_components(rows)['a']['group_id'] != module.assign_components(rows)['b']['group_id']
    rows.append(dict(rows[1], scenario_id='c', root_family_id='ra'))
    assigned = module.assign_components(rows)
    assert len({a['group_id'] for a in assigned.values()}) == 1


def test_additional_embargo_groups_disjoint_but_near_raw_windows():
    module = design_module()
    rows = [dict(scenario_id='a', role='pilot_train', raw_start_minute=0, raw_end_minute=10),
            dict(scenario_id='b', role='pilot_train', raw_start_minute=20, raw_end_minute=30)]
    assert len({a['group_id'] for a in module.assign_components(rows).values()}) == 1


def test_cross_role_lineage_or_support_conflict_rejected():
    module = design_module()
    a = dict(scenario_id='a', root_family_id='root', parent_instance_id='pa',
             base_state_id='sa', exogenous_stream_id='ea',
             raw_start_minute=0, raw_end_minute=60, role='pilot_train')
    for b in (dict(a, scenario_id='b', role='pilot_validation', raw_start_minute=120,
                   raw_end_minute=180),
              dict(a, scenario_id='b', role='pilot_validation', root_family_id='b',
                   parent_instance_id='pb', base_state_id='sb', exogenous_stream_id='eb')):
        with pytest.raises(ValueError, match='role'):
            module.assign_components([a, b])


def test_old_development_cannot_become_final():
    module = design_module()
    with pytest.raises(ValueError, match='final|blind'):
        module.assign_components([dict(scenario_id='old', role='final_reserved',
            purpose='development', raw_start_minute=0, raw_end_minute=60)])


def test_writer_immutable_manifest_and_tamper_detection(designed, tmp_path):
    module, design = designed
    out = tmp_path / 'frozen'
    module.write_design(design, out)
    assert module.load_design(out) == design
    with pytest.raises(FileExistsError):
        module.write_design(design, out)
    scenarios = out / 'scenario_definitions.json'
    scenarios.write_text(scenarios.read_text(encoding='utf-8') + ' ', encoding='utf-8')
    with pytest.raises(ValueError, match='hash|integrity'):
        module.load_design(out)


def test_no_regeneration_when_support_capacity_too_small():
    module = design_module()
    with pytest.raises(ValueError, match='time blocks|support'):
        module.build_design(ROOT, known_support=[dict(raw_start_minute=0,
            raw_end_minute=8760*60, source='all_days_known')])


def test_support_is_recomputed_for_larger_windows(designed):
    module, design = designed
    old = design['scenarios'][0]
    changed = dict(old, duration_steps=48, tail_interval_minutes=[420, 720])
    assert module.support_interval(changed)['raw_end_minute'] > module.support_interval(old)['raw_end_minute']
    assert module.support_interval(old)['raw_start_minute'] == module.support_interval(changed)['raw_start_minute']
