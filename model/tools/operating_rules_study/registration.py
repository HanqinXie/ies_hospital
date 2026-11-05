"""Preregister bounded comparisons without consulting candidate outcomes."""
from __future__ import annotations
import copy, json
from pathlib import Path
from . import METHODS, STUDY_ID
from .environment import method_config, response_windows, _number
from tools.sci_engineering.design import SEALED_DAYS, source_support, digest

ROOT=Path(__file__).resolve().parents[2]

def load_registry():
    return json.loads((ROOT/'configs/model_review_v30/cases.json').read_text(encoding='utf8'))

def _source(registry,source_id,method):
    alias='C' if method=='C' else 'M'
    source=next((c for c in registry['cases'] if c['id']==source_id),None)
    if source is None:raise ValueError('Unknown source case')
    if source['policy']['alias']!=alias:
        related=[c for c in registry['cases'] if c.get('v30_context')==source.get('v30_context')
                 and c.get('v30_mode')==source.get('v30_mode') and c['policy']['alias']==alias]
        if len(related)!=1:raise ValueError('Missing matching frozen policy configuration')
        source=related[0]
    return source

def validate_case(case,registry):
    meta=case.get('operating_rules_study',{})
    if meta.get('study_id')!=STUDY_ID or meta.get('method') not in METHODS:
        raise ValueError('Explicit study registration required')
    old=_source(registry,meta['base_case_id'],meta['method'])
    if 'policy_override_alias' in case:raise ValueError('Runtime policy configuration override is not permitted')
    if case['policy']!={'alias':old['policy']['alias'],'config':method_config(old['policy']['config'],meta['method'])}:
        raise ValueError('Frozen method configuration changed')
    if case['configuration'] not in registry['hardware_profiles']:raise ValueError('Unknown hardware profile')
    for key in ('registry','model_config','hardware_profiles','model_closure_v22','model_correctness_v23','engineering_completion_v24','model_correction_v25'):
        if key in case:raise ValueError('Physical overrides are not allowed: '+key)
    s=case['scenario'];end=float(s['duration_steps'])*float(s['step_minutes'])
    protected=('step_minutes','pre_event_warmup_steps','pre_event_grid_limit_kW','grid_limit_kW',
        'automatic_fast_shedding','initial_reserve_kWh','initial_reserve_semantics','initialization_seed',
        'precontrol_sheddable_fraction_by_priority','thermal_disturbance','actual_p0_load_multiplier',
        'original_p0_load_multiplier','clinical_coefficients_are_research_assumptions')
    for key in protected:
        if s.get(key)!=old['scenario'].get(key):raise ValueError('Physical/execution field changed: '+key)
    if s.get('diesel_on_grid_allowed',False)!=old['scenario'].get('diesel_on_grid_allowed',False):
        raise ValueError('Physical diesel connection permission changed')
    for key in ('operating_rule_id','v27_load_overlay'):
        if case.get(key)!=old.get(key):raise ValueError('Physical operating/input boundary changed: '+key)
    if end>1440. or end<=0.:raise ValueError('This finite study does not extend control qualification past 1440 minutes')
    if s.get('is_final_blind') or s.get('original_final12_evaluated'):raise ValueError('Final sealed evaluation is not authorized')
    support=source_support(s)
    if support['raw_start_minute']<0 or support['raw_end_minute']>8760.*60.:raise ValueError('Source year support exceeded')
    if any(support['raw_start_minute']<(day+1)*1440. and day*1440.<support['raw_end_minute'] for day in SEALED_DAYS):
        raise ValueError('Source window overlaps a sealed final day')
    if s['grid_limit_kW']!=old['scenario']['grid_limit_kW'] or s['initial_reserve_kWh']!=old['scenario']['initial_reserve_kWh']:
        raise ValueError('Physical capacity or initial DG budget changed')
    if s.get('automatic_fast_shedding')!=old['scenario'].get('automatic_fast_shedding'):
        raise ValueError('Execution capability changed')
    response_windows(s,meta['clock_contract'])
    from tools.model_review_v30.validation import resolve_case
    from tools.model_closure_v22.validation import validate_exporter_metadata
    resolve_case(case,registry);validate_exporter_metadata(s)
    return copy.deepcopy(case)

def make_case(registry,source_id,method,*,mode='source',clock_contract='event_triggered',start_hour=None,
              delay_minutes=None,event_start_minute=None,duration_minutes=None,faults=None,auxiliary_faults=None,
              configuration=None,background_role='seen_mechanism',case_suffix=''):
    if method not in METHODS or mode not in ('source','N','F','D','FD'):
        raise ValueError('Unknown method or event combination')
    old=_source(registry,source_id,method);case=copy.deepcopy(old);s=case['scenario']
    if mode!='source':
        fault=copy.deepcopy(old['scenario']['faults'] if faults is None else faults)
        aux=copy.deepcopy(old['scenario']['auxiliary_faults'] if auxiliary_faults is None else auxiliary_faults)
        shock=copy.deepcopy(old['scenario']['shock_by_zone'])
        if mode in ('F','FD') and not (fault or aux):raise ValueError('A locked fault target is required for this source')
        if mode in ('D','FD') and not shock:raise ValueError('A registered local demand increment is required for this source')
        has_f=mode in ('F','FD');has_d=mode in ('D','FD')
        native='L4b' if has_f and has_d else 'L1' if has_f or not has_d else 'L2'
        s.update(faults=fault if has_f else {},auxiliary_faults=aux if has_f else {},shock={},shock_by_priority={},
            shock_by_zone=shock if has_d else {},fault_duration_minutes=60. if has_f else 0.,
            shock_duration_minutes=60. if has_d else 0.,demand_amplitude=.25 if has_d else 0.,
            event_class=native,stage={'L1':1,'L2':2,'L4b':4}[native],enable_propagation=native=='L4b',
            canonical_class='L4' if has_f and has_d else 'L1' if has_f else 'L3' if has_d else 'N',
            exogenous_events=[],model_closure_events=[],normal_verification=not(has_f or has_d),
            normal_exogenous_fault_present=has_f,normal_clinical_shock_present=has_d)
    if start_hour is not None:
        if isinstance(start_hour,bool) or int(start_hour)!=start_hour:raise ValueError('Integer source hour required')
        s['start_hour']=int(start_hour)
        s.pop('full_source_support',None);s.pop('initialization_source_hours',None)
        s.update(source_day_index=(int(start_hour)-1)//24,source_calendar_days=[(int(start_hour)-1)//24],
            source_block_id='operating_rules_h'+str(start_hour),family_id='operating_rules_h'+str(start_hour),
            root_family_id='operating_rules_h'+str(start_hour),design_family='operating_rules_h'+str(start_hour),
            parent_template_id='operating_rules_h'+str(start_hour),new_independent_background=False,
            historical_exposure_status='not_certified_unseen_pending_history_audit',
            base_state_id='operating_rules_initial:'+digest(dict(hour=start_hour,seed=s['initialization_seed'],hardware=configuration or case['configuration'])))
    if delay_minutes is not None:s['response_delay_minutes']=_number(delay_minutes,'wait')
    if event_start_minute is not None:
        t=_number(event_start_minute,'event start')
        s.update(fault_start_minute=t,shock_start_minute=t,first_event_minute=t,recovery_start_minute=t+60.,
                 history_interval_minutes=[0.,t],tail_interval_minutes=[t+60.,float(s['duration_steps'])*s['step_minutes']])
    if duration_minutes is not None:
        end=_number(duration_minutes,'observation duration')
        if end%15.:raise ValueError('Observation duration must use the unchanged 15 minute decision grid')
        s.update(duration_steps=int(end/15.),tail_interval_minutes=[s['recovery_start_minute'],end])
        s.pop('full_source_support',None)
    if mode!='source' or start_hour is not None or duration_minutes is not None:
        support=source_support(s)
        s['full_source_support']={k:support[k] for k in ('raw_start_minute','raw_end_minute')}
        s.update(new_independent_background=False,original_final12_evaluated=False,is_final_blind=False,
                 sampling_cohort='operating_rules_20261029',purpose='operating_rule_conditional_comparison',
                 background_independence='reported from explicit historical support audit; not inferred from this constructor',
                 normal_is_new_independent_confirmation_background=False)
    case['configuration']=configuration or case['configuration']
    case['policy']={'alias':old['policy']['alias'],'config':method_config(old['policy']['config'],method)}
    key=dict(source=source_id,method=method,mode=mode,clock=clock_contract,hour=s['start_hour'],wait=s['response_delay_minutes'],
             event=s['fault_start_minute'],hardware=case['configuration'],duration=s['duration_steps'],suffix=case_suffix,
             identity_version=2,physical_scenario_sha256=digest(s),policy_config_sha256=digest(case['policy']))
    case['id']='ors_'+digest(key)[:18]
    case.update(study_id=STUDY_ID,evaluation_role=background_role,
        operating_rules_study=dict(study_id=STUDY_ID,method=method,mode=mode,clock_contract=clock_contract,
            base_case_id=source_id,background_role=background_role,registration_key=key,raw_inputs_unchanged=True,
            same_information=True,scope='central useful-energy service; no qualified room temperature or endogenous overload cascade'))
    return validate_case(case,registry)
