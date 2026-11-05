"""Study scenarios are fixed before execution and preserve physical boundaries."""
import copy, importlib, json, sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]

def api():
    try:return importlib.import_module('tools.operating_rules_study.registration')
    except ModuleNotFoundError as exc:pytest.fail('Study registration is missing: '+str(exc))

def reg():return json.loads((ROOT/'configs/model_review_v30/cases.json').read_text(encoding='utf8'))

def test_source_reproduction_preserves_all_physical_scenario_fields():
    r=reg();old=next(c for c in r['cases'] if c['id']=='b07_L4_GT_ER_disturbed_M')
    case=api().make_case(r,old['id'],'M_steam',mode='source',clock_contract='registered_window')
    assert case['scenario']==old['scenario']
    assert case['configuration']==old['configuration']
    assert case['policy']['config']==old['policy']['config']
    assert case['operating_rules_study']['method']=='M_steam'

@pytest.mark.parametrize('mode', ['N','F','D','FD'])
def test_four_conditions_change_events_without_changing_demand_source_or_hardware(mode):
    r=reg();case=api().make_case(r,'b07_L4_GT_ER_disturbed_M','M_both',mode=mode,clock_contract='event_triggered')
    s=case['scenario']
    assert bool(s['faults'])==(mode in ('F','FD'))
    assert bool(s['shock_by_zone'])==(mode in ('D','FD'))
    assert s['start_hour']==3128 and s['initial_reserve_kWh']==2400.
    assert case['configuration']=='H0' and s['grid_limit_kW']==850.
    assert case['policy']['config']['cooling_preparation'] is True
    api().validate_case(case,r)

def test_explicit_new_fault_is_required_when_source_had_none():
    r=reg()
    with pytest.raises(ValueError,match='fault'):
        api().make_case(r,'b05_L3_ER_disturbed_M','M',mode='F',clock_contract='event_triggered')

def test_new_source_window_has_correct_full_support_and_no_false_blind_claim():
    r=reg();case=api().make_case(r,'b07_L4_GT_ER_disturbed_M','M',mode='N',clock_contract='event_triggered',start_hour=2000)
    s=case['scenario']
    assert s['start_hour']==2000
    assert s['full_source_support']=={'raw_start_minute':1999*60.,'raw_end_minute':2003*60.}
    assert s['new_independent_background'] is False
    assert s['original_final12_evaluated'] is False

def test_sealed_window_and_physical_override_are_rejected():
    r=reg()
    with pytest.raises(ValueError,match='sealed'):
        api().make_case(r,'b07_L4_GT_ER_disturbed_M','M',mode='N',clock_contract='event_triggered',start_hour=21*24+1)
    case=api().make_case(r,'b07_L4_GT_ER_disturbed_M','M',mode='N',clock_contract='event_triggered')
    case['policy']['config']['support_minutes']=999.
    with pytest.raises(ValueError,match='configuration'):api().validate_case(case,r)

@pytest.mark.parametrize('key,value', [('diesel_on_grid_allowed',True),('pre_event_grid_limit_kW',2000.),
    ('pre_event_warmup_steps',100),('step_minutes',5.),('initialization_seed',99)])
def test_hand_edited_execution_capability_is_rejected(key,value):
    r=reg();case=api().make_case(r,'b07_L4_GT_ER_disturbed_M','M',mode='N',clock_contract='event_triggered')
    case['scenario'][key]=value
    with pytest.raises(ValueError,match='physical|execution|Physical'):api().validate_case(case,r)

def test_runtime_alias_override_cannot_bypass_registered_method():
    r=reg();case=api().make_case(r,'b07_L4_GT_ER_disturbed_M','M',mode='N',clock_contract='event_triggered')
    case['policy_override_alias']='C'
    with pytest.raises(ValueError):api().validate_case(case,r)

@pytest.mark.parametrize('field,left,right',[
    ('faults',{'GT_1':0.},{'GT_2':0.}),
    ('faults',{'GT_1':0.},{'GT_1':0.5}),
    ('auxiliary_faults',{'AUX_HEAT_PUMP':0.5},{'AUX_CHW_PUMP':0.5})])
def test_different_registered_physical_events_have_distinct_ids(field,left,right):
    r=reg();args=dict(mode='FD',start_hour=2000,case_suffix='same')
    a=api().make_case(r,'b07_L4_GT_ER_disturbed_M','M',**args,**{field:left})
    b=api().make_case(r,'b07_L4_GT_ER_disturbed_M','M',**args,**{field:right})
    assert a['id']!=b['id']
