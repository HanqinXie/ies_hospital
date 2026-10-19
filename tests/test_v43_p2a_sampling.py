"""P2A specification-only sampling contracts; these tests run no trajectories."""
from collections import defaultdict
from pathlib import Path
import sys

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
from hospital_ies.resilience import empty_faults
from scenario_sampler import sample_scenarios, coverage_summary


@pytest.fixture(scope='module')
def hours():
    return pd.read_csv(ROOT / 'inputs/loads/hospital_ies_demand_8760.csv')


@pytest.fixture(scope='module')
def specs(hours):
    return sample_scenarios(hours, 100, 12, 20261012)


# Preserve all four original R07 counterexamples with the original seed/count.
@pytest.mark.parametrize('mode,carrier,half', [
    ('e', 'e', .9), ('h', 'h', .5), ('mixed', 'e', .9), ('mixed', 'h', .5),
])
def test_original_r07_conditional_upper_lower(specs, mode, carrier, half):
    values = [s['shock'][carrier] for s in specs
              if s['stage'] == 2 and s['shock_mode'] == mode]
    assert min(values) < half < max(values), (mode, carrier, min(values), max(values))


@pytest.mark.parametrize('mode,carrier,upper', [
    ('e', 'e', 1.8), ('h', 'h', 1.), ('c', 'c', 1.4),
    ('mixed', 'e', 1.8), ('mixed', 'h', 1.), ('mixed', 'c', 1.4),
])
def test_all_active_modes_cover_each_pressure_quartile(specs, mode, carrier, upper):
    values = [s['shock'][carrier] for s in specs
              if s['stage'] == 2 and s['shock_mode'] == mode]
    assert {min(3, int(4 * v / upper)) for v in values} == {0, 1, 2, 3}


@pytest.mark.parametrize('stage', [1, 3])
def test_each_real_device_varies_severity_duration_background_delay(specs, stage):
    by_device = defaultdict(list)
    for s in specs:
        if s['stage'] == stage:
            by_device[s['initiating_device']].append(s)
    assert set(by_device) == set(empty_faults())
    for dev, group in by_device.items():
        assert len({s['faults'][dev] for s in group}) >= 3, dev
        assert len({s['fault_duration_steps'] for s in group}) >= 2, dev
        assert len({s['start_hour'] for s in group}) >= 2, dev
        if stage == 3:
            assert len({s['response_delay_steps'] for s in group}) >= 2, dev


def test_old_four_stage_semantics_and_counts_remain(specs):
    assert {stage: sum(s['stage'] == stage for s in specs)
            for stage in (1, 2, 3, 4)} == {1: 100, 2: 100, 3: 100, 4: 100}
    assert all(not s.get('faults') for s in specs if s['stage'] in (2, 4))
    assert all(not s.get('shock') for s in specs if s['stage'] in (1, 3))


@pytest.fixture(scope='module')
def versioned(hours):
    return sample_scenarios(hours, 100, 12, 20261012,
                            event_classes=('L1', 'L2', 'L3', 'L4a', 'L4b'))


def test_versioned_l4a_l4b_windows_and_exogenous_provenance(versioned):
    assert len(versioned) == 500
    assert all(s['event_schema_version'] == 'p2a.events.v1' for s in versioned)
    a = [s for s in versioned if s['event_class'] == 'L4a']
    b = [s for s in versioned if s['event_class'] == 'L4b']
    assert all(s['stage'] == 4 and not s.get('faults') and s['shock'] for s in a)
    assert all(s['stage'] == 4 and s['faults'] and s['shock'] for s in b)
    offsets = {s['fault_start_minute'] - s['shock_start_minute'] for s in b}
    assert 0 in offsets and min(offsets) < 0 < max(offsets)
    assert any(s['fault_duration_minutes'] != s['shock_duration_minutes'] for s in b)
    for s in b:
        assert not s.get('endogenous_protection_events')
        assert {e['origin'] for e in s['exogenous_events']} == {'exogenous'}
        assert len(s['exogenous_events']) == 2
        for event in s['exogenous_events']:
            assert 0 <= event['start_minute'] < event['end_minute']
        if s['initiating_device'] == 'TOWER':
            assert 'common_mode_fault' in {e['kind'] for e in s['exogenous_events']}


def test_each_l4b_device_varies_all_design_dimensions(versioned):
    by_device = defaultdict(list)
    for s in versioned:
        if s['event_class'] == 'L4b':
            by_device[s['initiating_device']].append(s)
    assert set(by_device) == set(empty_faults())
    for dev, group in by_device.items():
        assert len({s['faults'][dev] for s in group}) >= 3
        assert len({s['fault_duration_minutes'] for s in group}) >= 2
        assert len({s['response_delay_minutes'] for s in group}) >= 2
        assert len({s['start_hour'] for s in group}) >= 2


def test_streams_are_repeatable_and_independent_of_class_order(hours, versioned):
    reverse = sample_scenarios(hours, 100, 12, 20261012,
        event_classes=('L4b', 'L4a', 'L3', 'L2', 'L1'))
    assert sorted(versioned, key=lambda s: s['id']) == sorted(reverse, key=lambda s: s['id'])
    only = sample_scenarios(hours, 100, 12, 20261012, event_classes=('L4b',))
    assert only == [s for s in versioned if s['event_class'] == 'L4b']


def test_identity_stress_distribution_and_effective_p0_multiplier(specs):
    for s in specs:
        for key in ('family_id', 'parent_template_id', 'base_state_id',
                    'exogenous_stream_id', 'distribution_id', 'sample_schema_version'):
            assert isinstance(s.get(key), str) and s[key], key
        assert s['p_reference'] is None
        assert s['q_design_density'] is None
        assert s['importance_weight'] is None
        assert s['sampling_role'] == 'development_stress_design'
        for carrier in ('e', 'h', 'c'):
            expected = (1 + s.get('shock', {}).get(carrier, 0)) * (
                1 + s.get('shock_by_priority', {}).get('P0', {}).get(carrier, 0))
            assert s['actual_p0_load_multiplier'][carrier] == pytest.approx(expected)


def test_coverage_distinguishes_unsupported_unsampled_and_counts(versioned):
    report = coverage_summary(versioned)
    rows = report['device_severity_event_class']
    assert {'covered', 'unsupported'} <= {r['status'] for r in rows}
    partial = coverage_summary(versioned[:1])['device_severity_event_class']
    assert 'unsampled' in {r['status'] for r in partial}
    assert all(not r['supported'] and r['count'] == 0 for r in rows
               if r['event_class'] in ('L2', 'L4a'))
    assert all(r['supported'] for r in rows if r['event_class'] in ('L1', 'L3', 'L4b'))
    assert all(r['count'] == sum(s.get('initiating_device') == r['device']
               and s['event_class'] == r['event_class']
               and s['fault_severity_layer'] == r['severity_layer'] for s in versioned)
               for r in rows)
    assert report['sampling_design_not_failure_probabilities'] is True
    assert report['q_design']['kind'] == 'fixed_stress_counts'
    assert report['p_reference'] is None
    for r in report['device_event_condition_coverage']:
        if r['count']:
            assert r['fault_duration_counts'] and r['background_hour_counts']
            assert r['response_delay_minutes_counts']


@pytest.mark.parametrize('classes', [(), ('L5',), ('L4b', 'L4b')])
def test_invalid_event_class_requests_rejected(hours, classes):
    with pytest.raises(ValueError, match='event_classes'):
        sample_scenarios(hours, 2, 12, 20261012, event_classes=classes)
