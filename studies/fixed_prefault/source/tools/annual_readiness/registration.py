"""Canonical opt-in continuous cases over the frozen V30 engineering inputs.

Registration may extend observation support; it never constructs an environment,
resets stocks, changes hardware or provides the calendar to an online policy.
"""
from __future__ import annotations

import copy
import math
from numbers import Real

from tools.engineering_basis_study.registration import make_case as engineering_case
from tools.operating_rules_study.registration import load_registry
from tools.sci_engineering.design import digest, source_support, SEALED_DAYS
from . import MODEL_ID


METHODS = ('M_available', 'C_available', 'M_ready', 'C_ready', 'M_cooling_available')
MAX_DURATION_MINUTES = 7.*24.*60.


def _number(value, name, *, low=0., high=None):
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError('Invalid finite '+name)
    value = float(value)
    if not math.isfinite(value) or value < low or (high is not None and value > high):
        raise ValueError('Registered range exceeded: '+name)
    return value


def _frozen_registry(registry):
    """Do not let an altered caller registry redefine a canonical baseline."""
    trusted = load_registry()
    if not isinstance(registry, dict) or digest(registry) != digest(trusted):
        raise ValueError('Supplied registry differs from the frozen physical and policy baseline')
    if trusted['model_closure_v22'].get('research_stress_proxy') is not False:
        raise ValueError('Unqualified endogenous stress proxy must remain disabled')
    if trusted['model_correctness_v23'].get('thermal_coupling') is not None:
        raise ValueError('Unqualified thermal coupling must remain disabled')
    return trusted


def _calendar(value):
    from tools.model_closure_v22.environment import validate_events
    if value is None:
        return []
    events = validate_events(value)
    # Preserve the old event schema, with strict numeric clocks and values.
    for event in events:
        event['time_minute'] = _number(event['time_minute'], 'calendar time')
        event['availability'] = {name:_number(v, 'calendar availability', high=1.)
            for name,v in event['availability'].items()}
    return events


def _build(registry, source_id, method, *, mode='FD', start_hour=None,
           duration_minutes=180., configuration=None, calendar=None,
           requalification_minutes=None, boiler_restart_minutes=1.,
           fault_duration_minutes=60., response_wait_minutes=15., demand_amplitude=.25):
    trusted = _frozen_registry(registry)
    if method not in METHODS or mode not in ('N', 'F', 'D', 'FD'):
        raise ValueError('Unknown registered method or event combination')
    end = _number(duration_minutes, 'observation duration', high=MAX_DURATION_MINUTES)
    if end <= 0. or end % 15.:
        raise ValueError('Observation duration must use the unchanged 15 minute decision grid')
    if start_hour is not None:
        hour = _number(start_hour, 'source hour', low=1., high=8760.)
        if hour != int(hour):
            raise ValueError('Integer source hour required')
        start_hour = int(hour)
    if configuration is not None and configuration not in ('H0', 'H1', 'H2'):
        raise ValueError('Only fixed H0/H1/H2 profiles are registered')
    restart = _number(boiler_restart_minutes, 'boiler restart duration')
    if restart not in (1., 5., 15.):
        raise ValueError('Only named 1/5/15 minute boiler restart assumptions are registered')
    requalification = requalification_minutes
    if requalification is not None:
        requalification = _number(requalification, 'functional requalification duration')
        if requalification not in (30., 60., 120.):
            raise ValueError('Only named 30/60/120 minute functional requalification is registered')
    fault = _number(fault_duration_minutes, 'fault duration', high=720.)
    wait = _number(response_wait_minutes, 'response wait', high=120.)
    amplitude = _number(demand_amplitude, 'local demand amplitude', high=1.)
    events = _calendar(calendar)
    if events and mode != 'N':
        raise ValueError('A calendar requires a clean base N case without overlapping base fault or demand')
    if events and (events[-1]['time_minute'] >= end or end-events[-1]['time_minute'] < 60.):
        raise ValueError('Calendar must leave a complete 60 minute observation tail after its last transition')

    # Reuse every original short-case preparation/physics check exactly once.
    short_end = max(180., math.ceil((60.+max(fault if mode in ('F','FD') else 0.,
        60. if mode in ('D','FD') else 0.)+60.)/15.)*15.)
    if end < short_end:
        raise ValueError('Observation cannot shorten the base event and complete recovery tail')
    underlying = {'M_ready':'M_available', 'C_ready':'C_available'}.get(method, method)
    case = engineering_case(trusted, source_id, underlying, mode=mode, start_hour=start_hour,
        demand_amplitude=amplitude, fault_duration_minutes=fault,
        response_wait_minutes=wait, observation_end_minute=short_end,
        configuration=configuration, boiler_restart_minutes=restart)
    scene = case['scenario']
    scene['duration_steps'] = int(end/15.)
    scene['model_closure_events'] = events
    if events:
        scene.update(normal_verification=False, canonical_class='calendar', analysis_class='calendar',
            normal_exogenous_fault_present=any(v < 1. for e in events for v in e['availability'].values()),
            normal_clinical_shock_present=False, first_event_minute=events[0]['time_minute'],
            recovery_start_minute=events[-1]['time_minute'],
            history_interval_minutes=[0., events[0]['time_minute']])
    scene['tail_interval_minutes'] = [scene['recovery_start_minute'], end]
    # Recompute the complete window, retaining source_support's initialization
    # extension and endpoint-hour lookahead rather than checking event hours only.
    scene.pop('full_source_support', None)
    support = source_support(scene)
    if any(support['raw_start_minute'] < (day+1)*1440.
           and day*1440. < support['raw_end_minute'] for day in SEALED_DAYS):
        raise ValueError('Complete source support overlaps a sealed final day')
    scene['full_source_support'] = support
    days = list(range(int(support['raw_start_minute']//1440.),
        int(math.ceil(support['raw_end_minute']/1440.))))
    parameters = dict(mode=mode, start_hour=scene['start_hour'], duration_minutes=end,
        configuration=case['configuration'], calendar=copy.deepcopy(events),
        requalification_minutes=requalification, boiler_restart_minutes=restart,
        fault_duration_minutes=fault if mode in ('F','FD') else 0.,
        response_wait_minutes=wait, demand_amplitude=amplitude if mode in ('D','FD') else 0.)
    family_key = {key:parameters[key] for key in ('mode','start_hour','duration_minutes','configuration',
        'calendar','fault_duration_minutes','response_wait_minutes','demand_amplitude')}
    family = 'annual_readiness_'+digest(dict(source_id=source_id, **family_key))[:18]
    external = {key:scene.get(key) for key in ('start_hour','faults','auxiliary_faults',
        'fault_start_minute','fault_duration_minutes','shock_by_zone','shock_start_minute',
        'shock_duration_minutes','model_closure_events')}
    scene.update(source_block_id=family, family_id=family, root_family_id=family,
        design_family=family, parent_template_id=family, source_calendar_days=days,
        exogenous_stream_id='annual_readiness_exogenous:'+digest(external),
        sampling_cohort='annual_readiness_20261030', purpose='finite_continuous_functional_readiness',
        new_independent_background=False, original_final12_evaluated=False, is_final_blind=False)
    case.pop('engineering_basis_study', None)
    case.update(study_id=MODEL_ID, evaluation_role='finite_continuous_readiness_research')
    if method.endswith('_ready'):
        case['operating_rule_id'] = 'sustained_critical_60min'
    kind = 'calendar_multi_event' if events else 'normal' if mode == 'N' else 'base_'+mode
    case['annual_readiness'] = dict(model_id=MODEL_ID, source_id=source_id, method=method,
        parameters=parameters, clock_contract='event_triggered', case_kind=kind,
        qualification_is_functional_not_DC_energy=True, original_final12_untouched=True,
        physical_inputs_unchanged=True, calendar_is_online_policy_input=False,
        source_support=dict(**support, source_hour_count=int((support['raw_end_minute']-
            support['raw_start_minute'])/60.), source_day_count=len(days),
            endpoint_source_hour_reserved=True, sealed_final_day_overlap=False),
        recovery_tail_semantics='observation_after_last_transition_no_repair_guarantee' if events
            else 'original_base_event_recovery_tail')
    # Avoid recursive identity: replace the inherited scenario id only after
    # hashing a canonical object without either inherited top-level id.
    identity = copy.deepcopy(case)
    identity.pop('id', None)
    identity['scenario'].pop('id', None)
    identifier = 'arp_'+digest(identity)[:18]
    case['id'] = scene['id'] = identifier
    from tools.model_review_v28.environment import validate_scenario, validate_events
    from tools.model_closure_v22.validation import validate_exporter_metadata
    plain = copy.deepcopy(scene)
    plain.pop('model_closure_events', None)
    validate_scenario(plain)
    validate_events(events)
    validate_exporter_metadata(scene)
    return case


def make_case(registry, source_id, method, *, mode='FD', start_hour=None,
              duration_minutes=180., configuration=None, calendar=None,
              requalification_minutes=None, boiler_restart_minutes=1.,
              fault_duration_minutes=60., response_wait_minutes=15., demand_amplitude=.25):
    case = _build(registry, source_id, method, mode=mode, start_hour=start_hour,
        duration_minutes=duration_minutes, configuration=configuration, calendar=calendar,
        requalification_minutes=requalification_minutes, boiler_restart_minutes=boiler_restart_minutes,
        fault_duration_minutes=fault_duration_minutes, response_wait_minutes=response_wait_minutes,
        demand_amplitude=demand_amplitude)
    return validate_case(case, registry)


def validate_case(case, registry):
    if not isinstance(case, dict):
        raise ValueError('A complete registered annual-readiness case is required')
    meta = case.get('annual_readiness', {})
    if not isinstance(meta, dict) or meta.get('model_id') != MODEL_ID:
        raise ValueError('Explicit annual-readiness registration is required')
    try:
        expected = _build(registry, meta['source_id'], meta['method'], **meta['parameters'])
        matches = digest(case) == digest(expected)
    except (KeyError, TypeError) as exc:
        raise ValueError('Incomplete or unknown canonical registration parameters') from exc
    if not matches:
        raise ValueError('Case differs from canonical physics, policy, scope or identity')
    return copy.deepcopy(case)


def worker_registry(case, registry):
    checked = validate_case(case, registry)
    result = copy.deepcopy({k:v for k,v in registry.items() if k != 'cases'})
    result['model_closure_v22']['station']['boiler_restart_minutes'] = (
        checked['annual_readiness']['parameters']['boiler_restart_minutes'])
    return result
