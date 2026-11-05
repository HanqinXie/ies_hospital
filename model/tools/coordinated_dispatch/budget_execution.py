"""Offline issued-generation budget deviation; consumes an already-open payload.

For command generated at t, integrate actual saved P2/P3 ENS over [t,t+step).
Compare with that generation's held reference kW times step hours plus its
recorded allowed extra kWh. This is neither an execution guarantee nor the
optimal feasible counterfactual at execution time. Original ENS/classifiers are
untouched. Window summaries group by generation time and retain whole original
step intervals; they must not be presented as score-window ENS integrals.
"""
from __future__ import annotations

import math
from numbers import Real

SCHEMA = 'coordinated_dispatch.executed_budget_deviation.v1'
EPS = 1e-9  # Time-coverage arithmetic only; no physical/score threshold changes.


def _n(value, label):
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(float(value)):
        raise ValueError(f'{label} must be a finite number')
    return float(value)


def _matrix(value, label, rows=2):
    if not isinstance(value, (list, tuple)) or len(value) != rows:
        raise ValueError(f'{label} must be {rows}-by-3')
    if any(not isinstance(x, (list, tuple)) or len(x) != 3 for x in value):
        raise ValueError(f'{label} must be {rows}-by-3')
    return [[_n(x, label) for x in row] for row in value]


def _zero():
    return [[0., 0., 0.], [0., 0., 0.]]


def _scale(a, value):
    return [[x * value for x in row] for row in a]


def _plus(a, b):
    return [[x + y for x, y in zip(ar, br)] for ar, br in zip(a, b)]


def _excess(a, b):
    return [[max(0., x - y) for x, y in zip(ar, br)] for ar, br in zip(a, b)]


def extract_executed_budget(payload, scenario):
    """Return JSON-native matrices (P2/P3 rows, e/h/c columns), all in kWh.

    Incomplete intervals retain observed integrals and the known whole target;
    full-interval actual/excess are null. A contiguous observed prefix has its
    own target scaled by prefix duration. If a saved timeline has a hole, later
    observed pieces are separately retained and never fill that missing prefix.
    Missing historical reference/budget is not_applicable, never zero excess.
    """
    step = _n(scenario.get('step_minutes') if isinstance(scenario, dict)
              else getattr(scenario, 'step_minutes', None), 'step_minutes')
    if step <= 0.:
        raise ValueError('step_minutes must be positive')
    segments = []
    for s in payload.get('segments', []):
        a, b = _n(s['start_minute'], 'segment start'), _n(s['end_minute'], 'segment end')
        if b <= a:
            raise ValueError('segment end must follow start')
        segments.append((a, b, _matrix(s['unserved_kW'], 'unserved_kW', 4)[2:]))
    segments.sort(key=lambda x: x[0])
    if any(a[1] > b[0] + EPS for a, b in zip(segments, segments[1:])):
        raise ValueError('Saved segments overlap')
    end = max((s[1] for s in segments), default=0.)
    observed = _n(payload.get('summary', {}).get('observed_until_minute', end), 'observed_until_minute')
    decisions = []
    for decision in payload.get('decisions', []):
        c = decision.get('command_generated')
        if not isinstance(c, dict):
            continue
        t = _n(c['generated_minute'], 'generated_minute')
        if t > observed:
            raise ValueError('Command generated after observed interval')
        diagnostic = c.get('joint_dispatch', {}).get('diagnostic', {})
        ref = diagnostic.get('service_reference')
        budget = diagnostic.get('service_budget')
        applicable = (isinstance(ref, dict) and ref.get('unserved_kW') is not None
                      and isinstance(budget, dict) and budget.get('allowed_extra_kWh') is not None)
        row = dict(command_id=c['id'], generated_minute=t, interval_minutes=[t, t + step],
                   applicable=applicable, budget_enabled=None if not isinstance(budget, dict) else budget.get('enabled'),
                   status='not_applicable', complete=None, observed_minutes=0., observed_prefix_minutes=0.)
        for key in ('reference_kWh', 'allowed_extra_kWh', 'target_kWh', 'planned_slack_kWh',
                    'actual_ENS_observed_prefix_kWh', 'observed_all_segments_ENS_kWh',
                    'observed_prefix_target_kWh', 'observed_prefix_excess_kWh',
                    'actual_all_ENS_kWh', 'actual_all_excess_kWh'):
            row[key] = None
        if applicable:
            reference = _scale(_matrix(ref['unserved_kW'], 'service reference', 4)[2:], step / 60.)
            allowed = _matrix(budget['allowed_extra_kWh'], 'allowed extra kWh')
            target = _plus(reference, allowed)
            actual, prefix, covered, cursor, prefix_open = _zero(), _zero(), 0., t, True
            for a, b, u in segments:
                lo, hi = max(a, t), min(b, t + step, observed)
                if hi <= lo:
                    continue
                amount = _scale(u, (hi - lo) / 60.)
                actual = _plus(actual, amount)
                covered += hi - lo
                if prefix_open and lo <= cursor + EPS:
                    prefix = _plus(prefix, amount)
                    cursor = hi
                else:
                    prefix_open = False
            prefix_minutes = max(0., cursor - t)
            complete = prefix_minutes >= step - EPS and covered >= step - EPS
            prefix_target = _scale(target, prefix_minutes / step)
            row.update(status='complete' if complete else 'partial', complete=complete,
                       observed_minutes=covered, observed_prefix_minutes=prefix_minutes,
                       reference_kWh=reference, allowed_extra_kWh=allowed, target_kWh=target,
                       planned_slack_kWh=None if budget.get('violation_kWh') is None else _matrix(budget['violation_kWh'], 'planned slack kWh'),
                       actual_ENS_observed_prefix_kWh=prefix, observed_all_segments_ENS_kWh=actual,
                       observed_prefix_target_kWh=prefix_target, observed_prefix_excess_kWh=_excess(prefix, prefix_target),
                       actual_all_ENS_kWh=actual if complete else None,
                       actual_all_excess_kWh=_excess(actual, target) if complete else None)
        decisions.append(row)
    answer = dict(schema=SCHEMA, units='kWh', matrix_rows=['P2', 'P3'], matrix_columns=['e', 'h', 'c'],
                  offline_only=True, actual_ENS_guarantee=False, current_feasible_optimum_counterfactual=False,
                  original_ENS_or_P0_classifier_changed=False, observed_until_minute=observed,
                  definition='Per carrier max(0, actual saved ENS in the issued generation step - (held reference kW * step hours + recorded extra allowance kWh)).',
                  decision_rows=decisions)
    for name, start, stop in [('primary', 60., 420.), ('full', 0., 600.)]:
        cohort = [r for r in decisions if start <= r['generated_minute'] < stop]
        applicable = [r for r in cohort if r['applicable']]
        total = _zero()
        for row in applicable:
            if row['complete']:
                total = _plus(total, row['actual_all_excess_kWh'])
        answer[name] = dict(window_minutes=[start, stop], decision_count=len(cohort),
            attribution='generation_cohort_full_step_intervals_not_score_window_ENS',
            cohort_intervals_may_extend_past_window_end=True,
            applicable_count=len(applicable), not_applicable_count=len(cohort)-len(applicable),
            complete_count=sum(r['complete'] is True for r in applicable),
            unknown_count=sum(r['complete'] is False for r in applicable),
            decision_ids=[r['command_id'] for r in cohort],
            unknown_decision_ids=[r['command_id'] for r in applicable if not r['complete']],
            all_registered_excess_kWh=total if cohort and all(r['complete'] is True for r in cohort) else None)
    return answer
