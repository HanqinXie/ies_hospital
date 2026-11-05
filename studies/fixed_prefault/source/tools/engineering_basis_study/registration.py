"""Canonical, bounded new cases; no modification of old registered cases."""
from __future__ import annotations

import copy
import math

from tools.operating_rules_study.registration import load_registry, make_case as original_case
from tools.sci_engineering.design import digest, source_support, SEALED_DAYS
from . import METHODS, STUDY_ID


def number(value, name, *, low=0., high=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('Invalid finite '+name)
    if value < low or high is not None and value > high:
        raise ValueError('Registered range exceeded: '+name)
    return float(value)


def _original_method(method):
    if method not in METHODS:
        raise ValueError('Unknown registered engineering arrangement')
    if method.startswith('C_'):
        return 'C'
    return 'M_cooling' if method.startswith('M_cooling_') else 'M'


def _build(registry, source_id, method, *, mode='FD', start_hour=None,
           demand_amplitude=.25, fault_duration_minutes=60., shock_duration_minutes=60.,
           response_wait_minutes=15., event_start_minute=60., observation_end_minute=None,
           configuration=None, faults=None, auxiliary_faults=None, boiler_restart_minutes=1.):
    if mode not in ('N', 'F', 'D', 'FD'):
        raise ValueError('Unknown event combination')
    amplitude = number(demand_amplitude, 'local demand amplitude', high=1.)
    fd = number(fault_duration_minutes, 'fault duration', high=720.)
    sd = number(shock_duration_minutes, 'demand duration', high=720.)
    wait = number(response_wait_minutes, 'response wait', high=120.)
    onset = number(event_start_minute, 'onset', low=15., high=720.)
    restart = number(boiler_restart_minutes, 'boiler restart work time')
    if restart not in (1., 5., 15.):
        raise ValueError('Only separately named 1/5/15 minute restart assumptions are registered')
    has_f, has_d = mode in ('F', 'FD'), mode in ('D', 'FD')
    if has_f and fd <= 0 or has_d and (sd <= 0 or amplitude <= 0):
        raise ValueError('An active event requires positive duration and demand amplitude')
    end = (math.ceil((onset+max(fd, sd, 60.)+60.)/15.)*15.
           if observation_end_minute is None else number(observation_end_minute, 'observation end'))
    event_clear = onset + max(fd if has_f else 0., sd if has_d else 0.)
    if end % 15. or end > 1440. or end < event_clear+60.:
        raise ValueError('Observation must retain the decision grid, qualification and complete recovery tail')
    old_method = _original_method(method)
    case = original_case(registry, source_id, old_method, mode=mode,
        start_hour=start_hour, delay_minutes=wait, event_start_minute=onset,
        duration_minutes=end, faults=faults, auxiliary_faults=auxiliary_faults,
        configuration=configuration, background_role='historically_exposed_engineering_basis')
    scene = case['scenario']
    # The vector changes the actual source-hour increment. The scalar alone does not.
    if has_d:
        source_amplitude = float(scene['demand_amplitude'])
        scene['shock_by_zone'] = {zone: {carrier: float(v)*amplitude/source_amplitude
                                      for carrier, v in values.items()}
                                 for zone, values in scene['shock_by_zone'].items()}
    scene.update(fault_duration_minutes=fd if has_f else 0.,
        shock_duration_minutes=sd if has_d else 0., demand_amplitude=amplitude if has_d else 0.,
        recovery_start_minute=event_clear, tail_interval_minutes=[event_clear, end],
        enable_propagation=mode == 'FD', sampling_cohort='engineering_basis_20261029',
        purpose='matched_adjustable_range_and_conditional_operating_bounds')
    support = source_support(scene)
    if support['raw_start_minute'] < 0 or support['raw_end_minute'] > 8760.*60.:
        raise ValueError('Source-year support exceeded')
    if any(support['raw_start_minute'] < (day+1)*1440. and day*1440. < support['raw_end_minute']
           for day in SEALED_DAYS):
        raise ValueError('Complete source support overlaps a sealed final day')
    scene['full_source_support'] = {k: support[k] for k in ('raw_start_minute', 'raw_end_minute')}
    # original_case already applied the exact frozen preparation once. A second
    # preparation would incorrectly treat the prepared cooling config as its base.
    case.pop('operating_rules_study', None)
    case.update(study_id=STUDY_ID, evaluation_role='historically_exposed_engineering_basis')
    parameters = dict(mode=mode, start_hour=scene['start_hour'],
        demand_amplitude=amplitude if has_d else 0., fault_duration_minutes=fd if has_f else 0.,
        shock_duration_minutes=sd if has_d else 0., response_wait_minutes=wait,
        event_start_minute=onset, observation_end_minute=end, configuration=case['configuration'],
        faults=copy.deepcopy(scene['faults']), auxiliary_faults=copy.deepcopy(scene['auxiliary_faults']),
        boiler_restart_minutes=restart)
    signature = dict(study_id=STUDY_ID, source_id=source_id, method=method,
                     scenario_sha256=digest(scene), policy_sha256=digest(case['policy']),
                     boiler_restart_minutes=restart)
    case['id'] = 'ebs_'+digest(signature)[:18]
    case['engineering_basis_study'] = dict(signature, parameters=parameters,
        clock_contract='event_triggered', input_files_unchanged=True,
        endogenous_damage_enabled=False,
        legacy_enable_propagation_is_schema_flag=mode == 'FD',
        boiler_restart_evidence='unvalidated_research_baseline' if restart == 1. else 'analyst_assumption',
        local_growth_is_not_whole_hospital_growth=True,
        physical_scope='central useful-energy delivery; no qualified cold-start curve or clinical safety')
    return case


def validate_case(case, registry):
    if not isinstance(case, dict):
        raise ValueError('A complete registered case object is required')
    meta = case.get('engineering_basis_study', {})
    if meta.get('study_id') != STUDY_ID:
        raise ValueError('Explicit new engineering registration is required')
    if registry['model_closure_v22'].get('research_stress_proxy') is not False:
        raise ValueError('Unqualified endogenous stress proxy must remain disabled')
    if registry['model_correctness_v23'].get('thermal_coupling') is not None:
        raise ValueError('Unqualified room-temperature coupling must remain disabled')
    try:
        expected = _build(registry, meta['source_id'], meta['method'], **meta['parameters'])
    except (KeyError, TypeError) as exc:
        raise ValueError('Incomplete or unknown registration parameters') from exc
    if digest(case) != digest(expected):
        raise ValueError('Case differs from its canonical registered scenario, policy, identity or physical limits')
    from tools.model_review_v28.environment import validate_scenario, validate_events
    from tools.model_closure_v22.validation import validate_exporter_metadata
    scene = copy.deepcopy(case['scenario'])
    scene.pop('model_closure_events', None)
    validate_scenario(scene)
    validate_events(case['scenario'].get('model_closure_events', []))
    validate_exporter_metadata(case['scenario'])
    return copy.deepcopy(case)


def make_case(registry, source_id, method, **kwargs):
    return validate_case(_build(registry, source_id, method, **kwargs), registry)


def worker_registry(case, registry):
    case = validate_case(case, registry)
    result = copy.deepcopy({k: v for k, v in registry.items() if k != 'cases'})
    result['model_closure_v22']['station']['boiler_restart_minutes'] = (
        case['engineering_basis_study']['parameters']['boiler_restart_minutes'])
    return result
