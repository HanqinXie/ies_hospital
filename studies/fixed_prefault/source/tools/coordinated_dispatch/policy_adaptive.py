"""Optional current-fuel budget release over the unchanged coordination policy.

This changes a research service allowance, never physical fuel, source output,
P0/P1 locks, queued commands or observation timing. It is not a fuel forecast.
"""
from __future__ import annotations
import copy

from .policy import CoordinatedDispatchPolicy, EPS
from .interface import validate_action

BUDGET_FEEDBACK_CONTRACT_ID = 'observed_fuel_coverage_budget_release.v1'


class AdaptiveBudgetPolicy(CoordinatedDispatchPolicy):
    def __init__(self, config, policy_id, *, budget_feedback=False):
        if type(budget_feedback) is not bool:
            raise ValueError('budget_feedback must be a boolean')
        super().__init__(config,policy_id)
        self.budget_feedback = budget_feedback

    def decide(self, records):
        # The base policy rejects hidden outcomes and validates all current
        # clocks/sensors before any fuel observation below is read.
        request,diagnostic = super().decide(records)
        current = records[-1]['features']
        power = float(current['diesel_prev_kW'])
        fuel = float(current['diesel_energy_kWh'])
        hours = float(self.config['support_hours'])
        coverage = fuel/power if power > EPS else None
        release = bool(self.budget_feedback and coverage is not None and coverage < hours)
        base = copy.deepcopy(self.config['service_budget_fractions'])
        effective = [1.,1.] if release else copy.deepcopy(base)
        request['coordinated_joint']['options']['service_budget_fractions'] = effective
        request = validate_action(request)
        diagnostic.update(budget_feedback_contract_id=BUDGET_FEEDBACK_CONTRACT_ID,
            budget_feedback_enabled=self.budget_feedback,budget_release_active=release,
            budget_feedback_observation_time_minute=float(records[-1]['decision_time_min']),
            budget_feedback_actual_diesel_kW=power,budget_feedback_fuel_remaining_kWh=fuel,
            budget_feedback_coverage_hours=coverage,budget_feedback_support_hours=hours,
            base_service_budget_fractions=base,effective_service_budget_fractions=copy.deepcopy(effective),
            requested_joint_options=copy.deepcopy(request['coordinated_joint']['options']),
            budget_feedback_semantics='Release current per-decision P2/P3 allowance only when actual DG>0 and observed fuel/DG is below registered support_hours; no future failure duration',
            budget_release_is_actual_service_guarantee=False)
        return request,diagnostic
