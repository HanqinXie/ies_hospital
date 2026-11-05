import copy
import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def api():
    try:
        return importlib.import_module('calendar_plan')
    except ModuleNotFoundError:
        pytest.fail('Accepted common calendar implementation is missing', pytrace=False)


def test_exact_accepted_dates_and_exposure_counts():
    m = api()
    plan = m.candidate_plan()
    assert plan['status'] == 'candidate'
    assert plan['demand']['start_minute'] == 280800.
    assert plan['demand']['duration_minutes'] == 40320.
    assert plan['demand']['amplitude'] == .25
    events = m.availability_events(plan)
    assert events[:2] == [
        {'time_minute': 20760., 'availability': {'GT_3': 0.}},
        {'time_minute': 20820., 'availability': {'GT_3': 1.}}]
    assert [e for e in events if 311880 <= e['time_minute'] <= 311960] == [
        {'time_minute': 311880., 'availability': {'TOWER': .01}},
        {'time_minute': 311910., 'availability': {'TOWER': 1.}},
        {'time_minute': 311915., 'availability': {'TOWER': .01}},
        {'time_minute': 311960., 'availability': {'TOWER': 1.}}]
    # The registered September single-primary-pump exposure adds exactly1h F.
    assert m.exposure_hours(plan) == {'N': 8012., 'D': 669.75, 'F': 76., 'FD': 2.25}


def test_candidates_are_not_frozen_and_copies_do_not_share_state():
    m = api()
    plan = m.candidate_plan()
    with pytest.raises(ValueError):
        m.validate_plan(plan, require_frozen=True)
    plan['demand']['amplitude'] = .99
    assert m.candidate_plan()['demand']['amplitude'] == .25
    with pytest.raises(ValueError):
        m.validate_plan(plan)


@pytest.mark.parametrize('field,value', [('configuration', 'H1'), ('year', 2026),
    ('methods', ['M_ready']), ('status', 'frozen')])
def test_semantic_changes_rejected_even_with_recomputed_plan_signature(field, value):
    m = api()
    plan = m.candidate_plan()
    plan[field] = value
    plan['plan_sha256'] = m.plan_digest(plan)
    with pytest.raises(ValueError):
        m.validate_plan(plan)


def test_frozen_claim_without_actual_reference_proof_is_rejected():
    m = api()
    plan = m.candidate_plan()
    plan.update(status='frozen', reference_proof={'status': 'passed'})
    plan['plan_sha256'] = m.plan_digest(plan)
    with pytest.raises(ValueError):
        m.validate_plan(plan, require_frozen=True)


def test_only_registered_first_three_daily_anchors_can_move():
    m = api()
    plan = m.candidate_plan()
    plan['fault_intervals'][3]['start_minute'] += 1440.
    plan['fault_intervals'][3]['end_minute'] += 1440.
    plan['plan_sha256'] = m.plan_digest(plan)
    with pytest.raises(ValueError):
        m.validate_plan(plan)


def test_sealed_source_support_cannot_select_an_anchor():
    m = api()
    # Day 77 is protected; a window crossing its beginning is also protected.
    assert not m.selection_support_allowed(77 * 1440, 77 * 1440 + 60)
    assert not m.selection_support_allowed(77 * 1440 - 30, 77 * 1440 + 30)
    assert m.selection_support_allowed(76 * 1440, 76 * 1440 + 60)


def test_boolean_availability_is_not_a_numeric_fault_fraction():
    m = api()
    plan = m.candidate_plan()
    plan['fault_intervals'][0]['availability'] = False
    plan['plan_sha256'] = m.plan_digest(plan)
    with pytest.raises(ValueError):
        m.validate_plan(plan)
