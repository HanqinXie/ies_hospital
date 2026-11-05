"""Canonical nonblind annual cases; legacy finite-window registration unchanged.

Annual D/FD use native L2 parser labels with propagation disabled, matching the
same annual N/F physical/protection settings. F/FD calendar availability remains
an independent M23 input; compound annual conditions do not imply L4 cascades.
"""
from __future__ import annotations

import copy
from bisect import bisect_right
import math
from numbers import Real

from tools.annual_readiness.registration import make_case as finite_case,load_registry
from tools.model_closure_v22.environment import validate_events
from tools.sci_engineering.design import digest,SEALED_DAYS
from . import MODEL_ID
from .data_contract import requested_interval_support,YEAR_MINUTES

METHODS=('M_available','M_ready','C_ready')
SOURCE_ID='b07_L4_GT_ER_disturbed_M'


def _finite(value,name,*,minimum=0.,maximum=None):
    if isinstance(value,bool) or not isinstance(value,Real) or not math.isfinite(value):
        raise ValueError('Finite registered '+name+' required')
    value=float(value)
    if value<minimum or (maximum is not None and value>maximum):
        raise ValueError('Registered '+name+' range exceeded')
    return value


def make_case(method,*,mode='N',configuration='H2',start_hour=1,
              duration_minutes=YEAR_MINUTES,calendar=None,response_wait_minutes=15.,
              boiler_restart_minutes=1.,requalification_minutes=60.,demand_amplitude=.25):
    if method not in METHODS or mode not in ('N','F','D','FD'):
        raise ValueError('Unregistered annual arrangement or condition')
    if configuration not in ('H0','H1','H2'):raise ValueError('Fixed original H0/H1/H2 hardware required')
    if type(start_hour) is not int or not 1<=start_hour<=8760:raise ValueError('Integer source hour1..8760 required')
    end=_finite(duration_minutes,'duration',minimum=15.,maximum=YEAR_MINUTES)
    if end%15.:raise ValueError('Unchanged15-minute decision grid required')
    support=requested_interval_support((start_hour-1)*60.,(start_hour-1)*60.+end)
    absolute_end=support['raw_end_minute']
    reserve_endpoint=absolute_end<YEAR_MINUTES
    raw_support=dict(raw_start_minute=support['raw_start_minute'],
        raw_end_minute=((math.floor(absolute_end/60.)+1)*60. if reserve_endpoint else YEAR_MINUTES))
    events=validate_events([] if calendar is None else calendar)
    if bool(events)!=(mode in ('F','FD')):
        raise ValueError('F/FD need explicit common calendar; N/D must not contain faults')
    for event in events:
        event['time_minute']=_finite(event['time_minute'],'event time')
        event['availability']={name:_finite(value,'calendar availability',maximum=1.)
            for name,value in event['availability'].items()}
    wait=_finite(response_wait_minutes,'response wait',maximum=120.)
    restart=_finite(boiler_restart_minutes,'boiler restart')
    if restart not in (1.,5.,15.):raise ValueError('Only inherited restart assumptions1/5/15min')
    requalification=_finite(requalification_minutes,'functional requalification')
    if requalification not in (30.,60.,120.):raise ValueError('Only inherited functional assumptions30/60/120min')
    amplitude=_finite(demand_amplitude,'local demand amplitude',maximum=1.)
    parameters=dict(method=method,mode=mode,configuration=configuration,start_hour=start_hour,
        duration_minutes=end,calendar=copy.deepcopy(events),response_wait_minutes=wait,
        boiler_restart_minutes=restart,requalification_minutes=requalification,
        demand_amplitude=amplitude)
    # Reuse only the canonical frozen hardware/policy/current-demand setup. This
    # finite metadata constructor reads no physical numeric rows. Its old scope
    # is not used as the annual admission authority.
    case=finite_case(load_registry(),SOURCE_ID,method,mode='N',configuration=configuration,
        start_hour=1,duration_minutes=180.,response_wait_minutes=wait,
        boiler_restart_minutes=restart,requalification_minutes=requalification)
    scene=case['scenario']
    for key in ('normal_eval_interval_minutes','full_source_support','m21_physical_exogenous_sha256',
        'actual_p0_load_multiplier','source_day_isolation_basis','fixed_complete_source_minutes',
        'season_quarter'):
        scene.pop(key,None)
    scene.update(start_hour=start_hour,duration_steps=int(end/15.),step_minutes=15.,
        fault_start_minute=0.,fault_duration_minutes=0.,
        initialization_source_hours=[start_hour],model_closure_events=copy.deepcopy(events),
        shock={},shock_by_priority={},shock_by_zone=({'ER_TRAUMA1_FLR_1':{'c':amplitude}}
            if mode in ('D','FD') else {}),shock_start_minute=0.,
        shock_duration_minutes=end if mode in ('D','FD') else 0.,
        demand_amplitude=amplitude if mode in ('D','FD') else 0.,
        normal_verification=mode=='N',normal_exogenous_fault_present=bool(events),
        normal_clinical_shock_present=mode in ('D','FD'),canonical_class=mode,
        analysis_class='annual_'+mode,first_event_minute=(events[0]['time_minute'] if events else 0.),
        recovery_start_minute=(events[-1]['time_minute'] if events else end),
        full_source_support=copy.deepcopy(raw_support),
        role='frozen_annual_research',experiment_role='annual_continuous_engineering',
        purpose='given_initial_state_annual_service_and_resource_chain',
        requires_policy_freeze=True,selection_allowed=False,fit_allowed=False,
        is_final_blind=False,original_final12_evaluated=False,
        new_independent_background=False,background_independence='same_original_source_year_nonblind',
        source_day_isolation_basis='full_year_explicit_annual_use_protocol',
        history_interval_minutes=[0.,0.],tail_interval_minutes=[min(end,
            events[-1]['time_minute'] if events else end),end],
        sampling_cohort='annual_program_20261030',
        source_calendar_days=list(range(int(support['raw_start_minute']//1440.),
            int(math.ceil(raw_support['raw_end_minute']/1440.)))))
    if mode in ('D','FD'):
        # Parser encoding only: native L1 rejects even zone-only load growth.
        # M23 consumes the real detached calendar for FD; no fake native fault
        # or class-specific propagation/protection change is introduced.
        scene.update(stage=2,event_class='L2',enable_propagation=False)
    physical={k:scene[k] for k in ('start_hour','duration_steps','faults','auxiliary_faults',
        'shock','shock_by_priority','shock_by_zone','shock_start_minute','shock_duration_minutes',
        'model_closure_events','initial_reserve_kWh','response_delay_minutes')}
    scene['m21_physical_exogenous_sha256']=digest(physical)
    scene['exogenous_stream_id']='annual_program_external:'+digest(physical)
    family='annual_program_'+digest({k:v for k,v in parameters.items() if k!='method'})[:18]
    for key in ('source_block_id','family_id','root_family_id','design_family','parent_template_id'):
        scene[key]=family
    scene['source_day_index']=(start_hour-1)//24
    scene['base_state_id']='annual_given_initial:'+digest(dict(start_hour=start_hour,
        configuration=configuration,initialization_seed=scene['initialization_seed'],
        pre_event_warmup_steps=scene['pre_event_warmup_steps'],
        initial_diesel_shaft_kWh=scene['initial_reserve_kWh'],
        thermal_profile=case['v28_thermal_profile']))
    scene['annual_program_source_contract']=dict(model_id=MODEL_ID,
        integration_interval='[start,end)',terminal_source_hour_reserved=reserve_endpoint,
        source_support=copy.deepcopy(scene['full_source_support']))
    case['annual_readiness'].update(parameters=dict(case['annual_readiness']['parameters'],
        start_hour=start_hour,duration_minutes=end,mode=mode,calendar=copy.deepcopy(events),
        demand_amplitude=amplitude if mode in ('D','FD') else 0.),
        source_support=copy.deepcopy(scene['full_source_support']),case_kind='annual_'+mode)
    case['annual_readiness'].pop('original_final12_untouched',None)
    case.update(study_id=MODEL_ID,evaluation_role='frozen_nonblind_annual_engineering')
    case['annual_program']=dict(model_id=MODEL_ID,parameters=parameters,
        annual_study_is_blind=False,original_benchmark_run=False,
        old_results_read_only=True,sealed_source_hours_authorized=True,
        sealed_source_hours_processed=False,calendar_is_online_policy_input=False,
        original_final12_global_unexposed_claim_allowed=False,
        source_support=copy.deepcopy(scene['full_source_support']),
        endpoint_source_hour_reserved=reserve_endpoint,
        source_quarter_indices=sorted({bisect_right((0,90,181,273,365),day)-1
            for day in scene['source_calendar_days']}),
        recovery_after_source_end='right_censored_no_extrapolation',
        unobserved_calendar_events=[copy.deepcopy(e) for e in events if e['time_minute']>=end],
        functional_requalification_is_DC_energy=False,
        demand_interpretation='ER_TRAUMA1 existing terminal cold-service increment only; no electric heat double counting')
    case.pop('id',None);scene.pop('id',None)
    identifier='ay_'+digest(case)[:20];case['id']=scene['id']=identifier
    return case


def validate_case(case):
    if not isinstance(case,dict) or case.get('annual_program',{}).get('model_id')!=MODEL_ID:
        raise ValueError('Explicit annual program registration required')
    try:expected=make_case(**case['annual_program']['parameters'])
    except (KeyError,TypeError) as exc:raise ValueError('Invalid annual registration parameters') from exc
    if digest(case)!=digest(expected):raise ValueError('Canonical annual physics/policy/condition identity changed')
    return copy.deepcopy(case)


def required_hours(case):
    scene=case['scenario'];meta=case.get('annual_program',{})
    if meta.get('model_id')!=MODEL_ID:raise ValueError('Annual source contract required')
    support=requested_interval_support((scene['start_hour']-1)*60.,
        (scene['start_hour']-1)*60.+scene['duration_steps']*scene['step_minutes'])
    last=(int(math.floor(support['raw_end_minute']/60.))+1
        if support['raw_end_minute']<YEAR_MINUTES else 8760)
    return set(range(support['source_hour_first'],last+1))
