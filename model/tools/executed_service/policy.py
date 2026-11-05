"""One frozen actual-service allowance rule over the original fixed planner.

Only validated snapshots are accepted. This class has no environment, raw-data,
future-event or optimizer API. It changes current P2/P3 plan allowances only;
the original P0/P1 ordering, queue and execution capabilities remain inherited.
"""
from __future__ import annotations

import copy

from tools.coordinated_dispatch.interface import validate_action
from tools.coordinated_dispatch.policy import CoordinatedDispatchPolicy
from .history import EXECUTED_SERVICE_HISTORY_CONTRACT_ID, validate_history


EXECUTED_SERVICE_POLICY_CONTRACT_ID = 'executed_service.monotone_allowance.v1'
MONOTONE_FIELDS = ('cumulative_ENS_kWh', 'cumulative_equivalent_loss_minutes',
                   'cumulative_material_minutes')


class ExecutedServicePolicy(CoordinatedDispatchPolicy):
    def __init__(self, config, policy_id):
        if not isinstance(config, dict):
            raise ValueError('Executed-service policy configuration must be a mapping')
        supplied = copy.deepcopy(config)
        fuel_feedback = supplied.pop('budget_feedback', False)
        if type(fuel_feedback) is not bool or fuel_feedback:
            raise ValueError('Executed-service recipe does not enable current-fuel budget release')
        supplied.setdefault('coordination_mode', 'fixed')
        supplied.setdefault('fixed_preference', 'standby_first')
        super().__init__(supplied, policy_id)
        if (self.config['coordination_mode'] != 'fixed'
            or self.config['fixed_preference'] != 'standby_first'
            or self.config['support_hours'] != 2.
            or self.config['service_budget_fractions'] != [.05, .20]
            or self.config['service_budget_enabled'] is not True):
            raise ValueError('Only the preregistered fixed executed-service recipe is supported')

    def decide(self, records):
        # The base policy validates the old whitelist, sensor clocks, command
        # ledger and hidden-outcome prohibition before the extension is read.
        request, diagnostic = super().decide(records)
        previous = None
        histories = []
        for record in records:
            history = validate_history(record.get('executed_service_history'),
                                       record['decision_time_min'])
            if previous is not None:
                for key in MONOTONE_FIELDS:
                    if any(history[key][p][k] < previous[key][p][k]
                           for p in range(2) for k in range(3)):
                        raise ValueError('Cumulative executed-service loss cannot decrease: ' + key)
            histories.append(history)
            previous = history
        current = histories[-1]
        base = copy.deepcopy(self.config['service_budget_fractions'])
        saturation = [beta * 60. * self.config['support_hours'] for beta in base]
        groupmax = [max(row) for row in current['cumulative_equivalent_loss_minutes']]
        effective = [beta * max(0., 1. - loss / threshold)
                     for beta, loss, threshold in zip(base, groupmax, saturation)]
        request['coordinated_joint']['options']['service_budget_fractions'] = effective
        request = validate_action(request)
        diagnostic.update(
            strategy_status='executed_service_monotone_allowance',
            executed_service_history_contract_id=EXECUTED_SERVICE_HISTORY_CONTRACT_ID,
            executed_service_feedback_contract_id=EXECUTED_SERVICE_POLICY_CONTRACT_ID,
            executed_service_feedback_observation_time_min=float(records[-1]['decision_time_min']),
            executed_service_history_observation_count=len(histories),
            cumulative_equivalent_loss_minutes=copy.deepcopy(current['cumulative_equivalent_loss_minutes']),
            cumulative_ENS_kWh=copy.deepcopy(current['cumulative_ENS_kWh']),
            groupmax_equivalent_loss_minutes=groupmax,
            feedback_saturation_equivalent_minutes=saturation,
            base_service_budget_fractions=base,
            effective_service_budget_fractions=copy.deepcopy(effective),
            requested_joint_options=copy.deepcopy(request['coordinated_joint']['options']),
            executed_service_feedback_semantics='base_beta * max(0, 1 - max_carrier(cumulative_equivalent_loss_minutes)/(base_beta*60*support_hours)); past loss is never repaid',
            executed_service_feedback_is_service_guarantee=False)
        return request, diagnostic
