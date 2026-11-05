"""Equal current-observation boiler ranges for both frozen planners."""
from __future__ import annotations

import copy
from types import SimpleNamespace

from tools.model_closeout_v31.rules import prepare_steam_permission
from tools.model_review_v30.environment import with_terminal
from tools.operating_rules_study.environment import (StudyClockMixin, StudySteamPolicy,
                                                      method_config as original_config)
from . import METHODS, STUDY_ID


def method_config(base, method):
    if method not in METHODS:
        raise ValueError('Unknown registered engineering arrangement')
    if method.startswith('C_'):
        return original_config(base, 'C')
    return original_config(base, 'M_cooling' if method.startswith('M_cooling_') else 'M')


def prepare_available_range(method, dispatch, diagnostic, receipt, now):
    if method not in METHODS:
        raise ValueError('Unknown registered engineering arrangement')
    enabled = method.endswith('_available')
    if enabled:
        result, trace = prepare_steam_permission(dispatch, diagnostic, receipt, now)
        trace['v31_steam_permission'].update(method=method,
            rule_origin='V31.current_observed_healthy_boiler_ceiling')
    else:
        result, trace = copy.deepcopy(dispatch), copy.deepcopy(diagnostic)
    trace['engineering_available_range'] = dict(
        enabled=enabled, method=method, semantics='arrived_boiler_adjustable_ceiling',
        original_common_ceiling_lbph=dispatch['boiler_steam_lbph'],
        generated_ceiling_lbph=result['boiler_steam_lbph'],
        actual_output_is_forced=False, queue_bypassed=False,
        manufacturer_hot_standby_qualification=False,
        future_event_information_used=False)
    return result, trace


def with_engineering_basis(method, *, overlay_spec=None, thermal_profile=None):
    if method not in METHODS:
        raise ValueError('Unknown registered engineering arrangement')
    original = with_terminal(overlay_spec, thermal_profile=thermal_profile)
    mailbox = {}

    class AvailableRangeMixin:
        def _prepare_command_dispatch(self, dispatch, diagnostic, loads):
            value, trace = super()._prepare_command_dispatch(dispatch, diagnostic, loads)
            return prepare_available_range(method, value, trace, mailbox, self.time)

    def create(*args, **kwargs):
        env = original.OperatingHospitalIESEnv(*args, **kwargs)
        env.study_clock_contract = 'event_triggered'
        env.engineering_core_type = type('EngineeringBasisCore',
            (StudyClockMixin, AvailableRangeMixin, env.engineering_core_type), {})
        env.operating_model_config['engineering_basis_study'] = dict(
            study_id=STUDY_ID, method=method, clock_contract='event_triggered',
            physical_transition_code_unchanged=True, online_future_event_access=False,
            ceiling_is_actual_output=False, station_parameters_are_research_assumptions=True)
        return env

    def factory(alias, config=None, plant=None, policy_id=None):
        expected = 'C' if method.startswith('C_') else 'M'
        if alias != expected:
            raise ValueError('Registered method and planner alias disagree')
        if method.endswith('_available'):
            return StudySteamPolicy(alias, config=config, plant=plant, policy_id=policy_id, mailbox=mailbox)
        from tools.model_correctness_v23.policy import SciPolicy
        return SciPolicy(alias, config=config, plant=plant, policy_id=policy_id)

    return SimpleNamespace(validate_scenario=original.validate_scenario,
        validate_events=original.validate_events, OperatingHospitalIESEnv=create,
        run_episode=original.run_episode, controller_factory=factory)
