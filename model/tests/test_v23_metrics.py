"""Actual delivered-service recovery has separate candidates and confirmations."""
from copy import deepcopy
import importlib

import pytest


def api():
    try:
        return importlib.import_module('tools.model_correctness_v23.metrics')
    except ModuleNotFoundError as exc:
        if exc.name == 'tools.model_correctness_v23.metrics':
            pytest.fail('V23 sustained-service reliability evaluator is missing')
        raise


def segment(start, end, shortages=()):
    requested = [[100., 100., 100.] for _ in range(4)]
    cut = [[0., 0., 0.] for _ in range(4)]
    for priority, carrier in shortages:
        cut[int(priority[1])]['ehc'.index(carrier)] = 25.
    return dict(start_minute=start, end_minute=end,
        service_request_ledger=dict(original_demand_kW=requested,
            active_cut_kW=cut, connected_unserved_kW=[[0., 0., 0.] for _ in range(4)],
            delivered_kW=[[100.-v for v in row] for row in cut]))


def payload(segments, end=None):
    return dict(segments=segments, summary=dict(requested_end_minute=end or segments[-1]['end_minute']))


def test_micro_gaps_are_candidates_but_not_repeated_thermal_recovery():
    gap = 1e-7
    raw = payload([segment(0., 1., [('P0', 'c')]), segment(1., 1.+gap),
        segment(1.+gap, 2., [('P0', 'c')]), segment(2., 3.5)])
    before = deepcopy(raw)
    row = api().build_reliability_metrics(raw)['carrier_service']['P0']['c']
    assert row['interruption_count'] == 2
    assert row['material_minutes'] == pytest.approx(2.-gap)
    assert row['engineering_interruption_count'] == 1
    assert row['longest_engineering_minutes'] == 2.
    assert row['candidate_recoveries'][0]['status'] == 'rejected'
    assert row['recovery_absolute_minute'] == 2.
    assert row['recovery_confirmation_minute'] == 3.
    assert row['recovery_status'] == 'observed'
    assert row['ENS_kWh'] == pytest.approx(25.*(2.-gap)/60.)
    assert raw == before


def test_real_thermal_recovery_then_reloss_produces_two_episodes():
    row = api().build_reliability_metrics(payload([segment(0., 1., [('P1', 'h')]),
        segment(1., 2.5), segment(2.5, 3., [('P1', 'h')]), segment(3., 5.)]))['carrier_service']['P1']['h']
    assert row['engineering_interruption_count'] == 2
    assert [e['confirmed_recovery_minute'] for e in row['engineering_episodes']] == [2., 4.]


@pytest.mark.parametrize('seconds', [10., 15.])
def test_real_short_electric_interruptions_and_short_restoration_remain_visible(seconds):
    duration = seconds/60.
    row = api().build_reliability_metrics(payload([segment(0., duration, [('P0', 'e')]),
        segment(duration, duration+.001), segment(duration+.001, 2.*duration+.001, [('P0', 'e')]),
        segment(2.*duration+.001, 2.)]))['carrier_service']['P0']['e']
    assert row['engineering_interruption_count'] == 2
    assert row['longest_engineering_minutes'] == pytest.approx(duration)
    assert row['engineering_episodes'][0]['confirmed_recovery_minute'] == duration


def test_empty_storage_no_actual_recharge_does_not_create_metric_recovery():
    row = api().build_reliability_metrics(payload([segment(0., 3., [('P0', 'c')])]))['carrier_service']['P0']['c']
    assert row['recovery_status'] == 'censored'
    assert row['recovery_absolute_minute'] is None
    assert row['observation_end_minute'] == 3.
    assert row['engineering_interruption_count'] == 1


def test_legitimate_recharge_redelivery_confirms_only_the_actual_service_ledger():
    records = [segment(0., 1., [('P0', 'c')]), segment(1., 2.5),
        segment(2.5, 3., [('P0', 'c')]), segment(3., 4.5)]
    # Storage is evidence, never a substitute for actual delivered cold.
    records[0]['m21_storage'] = dict(cold_store=dict(charged_input_kWh=1., final_kWh=.95))
    records[1]['m21_storage'] = dict(cold_store=dict(discharged_kWh=.9, final_kWh=0.))
    row = api().build_reliability_metrics(payload(records))['carrier_service']['P0']['c']
    assert row['engineering_interruption_count'] == 2
    assert row['recovery_absolute_minute'] == 3.
    assert row['recovery_confirmation_minute'] == 4.


def test_statuses_distinguish_never_interrupted_censored_missing_and_observed():
    normal = api().build_reliability_metrics(payload([segment(0., 2.)]))
    assert normal['critical_union']['recovery_status'] == 'not_applicable'
    short_tail = api().build_reliability_metrics(payload([segment(0., 1., [('P0', 'h')]), segment(1., 1.5)]))
    assert short_tail['critical_union']['recovery_status'] == 'censored'
    assert short_tail['carrier_service']['P0']['h']['candidate_recovery_absolute_minute'] == 1.
    assert short_tail['carrier_service']['P0']['h']['candidate_recovery_status'] == 'observed_unconfirmed'
    missing = api().build_reliability_metrics(dict(summary=dict(requested_end_minute=2.)))
    assert missing['critical_union']['recovery_status'] == 'missing'
    assert missing['critical_union']['engineering_interruption_count'] is None


def test_reference_waits_keep_known_absolute_recovery_and_signed_early_offsets():
    data = payload([segment(0., 1., [('P0', 'c')]), segment(1., 3.)])
    row = api().build_reliability_metrics(data, references=dict(external_fault_clear_minute=2.,
        actual_source_restore_minute=dict(c=0.5)))['carrier_service']['P0']['c']
    assert row['recovery_absolute_minute'] == 1.
    assert row['recovery_relative_to_external_fault_clear_minutes'] == -1.
    assert row['recovery_wait_after_external_fault_clear_minutes'] == 0.
    assert row['recovery_wait_after_actual_source_restore_minutes'] == .5
    assert row['recovery_external_reference_status'] == 'observed'
    missing = api().build_reliability_metrics(data)['carrier_service']['P0']['c']
    assert missing['recovery_status'] == 'observed'
    assert missing['recovery_absolute_minute'] == 1.
    assert missing['recovery_source_reference_status'] == 'missing'


def test_union_requires_each_affected_carriers_own_confirmation():
    data = payload([segment(0., 1., [('P0', 'c')]), segment(1., 1.5),
        segment(1.5, 1.75, [('P1', 'e')]), segment(1.75, 4.)])
    metrics = api().build_reliability_metrics(data)
    union = metrics['critical_union']
    assert union['interruption_count'] == 2
    assert union['engineering_interruption_count'] == 1
    assert union['recovery_absolute_minute'] == 1.75
    assert union['recovery_confirmation_minute'] == 2.
    assert metrics['carrier_service']['P1']['e']['recovery_confirmation_minute'] == 1.75


def test_missing_observed_middle_is_missing_not_a_false_restoration():
    data = payload([segment(0., 1., [('P0', 'c')]), segment(2., 4.)])
    assert api().build_reliability_metrics(data)['critical_union']['recovery_status'] == 'missing'


def test_service_ledger_inconsistency_is_explicit_failure():
    data = payload([segment(0., 2.)])
    data['segments'][0]['service_request_ledger']['delivered_kW'][0][2] = 50.
    with pytest.raises(ValueError, match='ledger'):
        api().build_reliability_metrics(data)


def test_thermal_confirmation_sensitivity_is_explicit_and_never_changes_raw_energy():
    data = payload([segment(0., 1., [('P0', 'c')]), segment(1., 2.),
        segment(2., 3., [('P0', 'c')]), segment(3., 6.)])
    short = api().build_reliability_metrics(data, confirmation_seconds=dict(e=0., h=30., c=30.))
    long = api().build_reliability_metrics(data, confirmation_seconds=dict(e=0., h=120., c=120.))
    a, b = [m['carrier_service']['P0']['c'] for m in (short, long)]
    assert a['engineering_interruption_count'] == 2
    assert b['engineering_interruption_count'] == 1
    assert a['ENS_kWh'] == b['ENS_kWh']
    assert a['material_minutes'] == b['material_minutes']
    assert short['definition']['confirmation_seconds']['c'] == 30.
    assert long['definition']['confirmation_seconds']['c'] == 120.
    with pytest.raises(ValueError, match='Electric'):
        api().build_reliability_metrics(data, confirmation_seconds=dict(e=1., h=60., c=60.))


def test_explicit_inapplicable_reference_differs_from_missing_and_keeps_late_recovery():
    data = payload([segment(0., 1., [('P0', 'h')]), segment(1., 3.)])
    row = api().build_reliability_metrics(data, references=dict(
        external_fault_clear_minute='not_applicable',
        actual_source_restore_minute=dict(h='not_applicable')))['carrier_service']['P0']['h']
    assert row['recovery_status'] == 'observed'
    assert row['recovery_absolute_minute'] == 1.
    assert row['recovery_confirmation_minute'] == 2.
    assert row['recovery_external_reference_status'] == 'not_applicable'
    assert row['recovery_source_reference_status'] == 'not_applicable'
    assert row['external_fault_clear_minute'] is None
    assert row['recovery_wait_after_actual_source_restore_minutes'] is None
    missing = api().build_reliability_metrics(data)['carrier_service']['P0']['h']
    assert missing['recovery_source_reference_status'] == 'missing'


def test_union_uses_applicable_carrier_references_but_never_hides_missing_one():
    data = payload([segment(0., 1., [('P0', 'h'), ('P1', 'c')]), segment(1., 3.)])
    refs = dict(actual_source_restore_minute=dict(h='not_applicable', c=.5))
    row = api().build_reliability_metrics(data, references=refs)['critical_union']
    assert row['recovery_source_reference_status'] == 'observed'
    assert row['actual_source_restore_minute'] == .5
    assert row['recovery_wait_after_actual_source_restore_minutes'] == .5
    refs['actual_source_restore_minute']['c'] = 'not_applicable'
    row = api().build_reliability_metrics(data, references=refs)['critical_union']
    assert row['recovery_source_reference_status'] == 'not_applicable'
    del refs['actual_source_restore_minute']['c']
    row = api().build_reliability_metrics(data, references=refs)['critical_union']
    assert row['recovery_source_reference_status'] == 'missing'
