"""Pure current-observation GT preparation through the ordinary queued target.

The scalar critical request only activates this rule. It never aggregates
carrier service into a score or proves that a start, sink or service is feasible.
"""
from __future__ import annotations

import copy
import math
from collections.abc import Mapping
from numbers import Real


SCHEMA = 'hospital_ies.annual_readiness.current_GT_preparation.v2'
GT_NAMES = ('GT_1', 'GT_2', 'GT_3')


def _number(value, name, *, positive=False, maximum=None):
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError('Finite current numeric value required: '+name)
    result = float(value)
    if not math.isfinite(result) or result < 0. or (positive and result == 0.):
        raise ValueError('Invalid finite current value: '+name)
    if maximum is not None and result > maximum:
        raise ValueError('Current value exceeds allowed range: '+name)
    return result


def _vector(values, name):
    if isinstance(values, (str, bytes, Mapping)):
        raise ValueError('Exactly three current GT values required: '+name)
    try:
        if len(values) != 3:
            raise ValueError('Exactly three current GT values required: '+name)
        return [_number(value, name+'['+str(i)+']') for i, value in enumerate(values)]
    except TypeError as exc:
        raise ValueError('Exactly three current GT values required: '+name) from exc


def prepare_online_readiness(dispatch, *, actual_gt_kW, availability, rated_kW,
                             minimum_fraction, critical_request_kW):
    """Request one eligible GT's original minimum without altering execution.

    Existing finite requests are preserved, including those above the nominal
    preparation reference. The original queued executor alone projects against
    current ambient capacity, hardware, ramp, startup and service constraints.
    Actual output is a separate ledger and may precede a new hardware loss.
    """
    if not isinstance(dispatch, dict) or 'gt_electric_kW' not in dispatch:
        raise ValueError('Ordinary dispatch with three GT targets required')
    original = _vector(dispatch['gt_electric_kW'], 'gt_electric_kW')
    actual = _vector(actual_gt_kW, 'actual_gt_kW')
    if not isinstance(availability, Mapping) or any(name not in availability for name in GT_NAMES):
        raise ValueError('Three current GT hardware availabilities required')
    hardware = [_number(availability[name], name+' availability', maximum=1.) for name in GT_NAMES]
    rated = _number(rated_kW, 'rated_kW', positive=True)
    fraction = _number(minimum_fraction, 'minimum_fraction', positive=True, maximum=1.)
    critical = _number(critical_request_kW, 'critical_request_kW')
    minimum = rated*fraction
    capacities = [rated*value for value in hardware]
    above_reference = [target > reference for target, reference in zip(original, capacities)]

    online = [value > 0. for value in actual]
    eligible = [capacity >= minimum for capacity in capacities]
    preferred = [i for i in range(3) if online[i] and eligible[i]]
    candidates = preferred or [i for i in range(3) if eligible[i]]
    selected = candidates[0] if critical > 0. and candidates else None
    target = original.copy()
    if selected is not None:
        target[selected] = max(original[selected], minimum)
    result = copy.deepcopy(dispatch)
    result['gt_electric_kW'] = target
    reason = ('no_current_GT_capacity_reaches_original_minimum'
        if critical > 0. and selected is None else None)
    status = ('inactive_no_current_critical_request' if critical == 0.
        else 'unavailable' if selected is None else 'ordinary_minimum_requested')
    trace = dict(schema=SCHEMA, status=status, selected_GT=None if selected is None else GT_NAMES[selected],
        selection_basis=('current_actual_online_eligible_GT' if preferred else 'lowest_current_eligible_GT')
            if selected is not None else None,
        input_target_kW=original, observed_actual_gt_kW=actual, actual_online_mask=online,
        observable_availability=dict(zip(GT_NAMES, hardware)), effective_capacity_kW=capacities,
        # Keep the v1 numeric alias for consumers; it was never the original
        # executor's temperature-dependent physical capacity certificate.
        nominal_preparation_reference_kW=capacities.copy(),
        capacity_reference_basis='nominal_rating_times_current_availability',
        effective_capacity_is_physical_limit=False,
        preexisting_above_reference_mask=above_reference,
        preexisting_above_reference_kW=[max(0.,target-reference)
            for target,reference in zip(original,capacities)],
        preparation_delta_kW=[after-before for after,before in zip(target,original)],
        preexisting_targets_never_reduced=True, original_execution_projection_required=True,
        original_rated_kW=rated, original_minimum_fraction=fraction, minimum_stable_kW=minimum,
        eligible_mask=eligible, requested_target_kW=target.copy(), changed=target != original,
        critical_request_kW=critical, critical_request_activation_only=True,
        cross_carrier_service_score_computed=False, cannot_prepare_reason=reason,
        ordinary_queued_target_only=True, current_observation_only=True,
        future_event_information_used=False, pending_target_credited_as_actual=False,
        queue_bypassed=False, actual_supply_guaranteed=False,
        electric_sink_feasibility='unknown_not_checked_by_this_rule',
        startup_and_service_feasibility='original_execution_constraints_required')
    return result, trace
