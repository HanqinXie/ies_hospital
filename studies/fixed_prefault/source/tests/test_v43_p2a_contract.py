"""P2A current-path data contracts. No hospital network fitting."""
from pathlib import Path
import copy
import importlib
import json
import sys
import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'src')]

def contract():
    assert importlib.util.find_spec('hospital_ies.data_contract') is not None, 'P2A data contract missing'
    return importlib.import_module('hospital_ies.data_contract')

@pytest.mark.parametrize('first,expected,valid,at_risk', [
    (0,None,False,False), (15,1,True,True), (15.01,0,True,True),
    (None,0,True,True)])
def test_first_failure_endpoints(first,expected,valid,at_risk):
    r=contract().first_failure_label(0,15,first,30,'completed')
    assert (r['value'],r['valid'],r['at_risk'])==(expected,valid,at_risk)

@pytest.mark.parametrize('status,reason', [('completed','administrative_end'),
    ('model_constraint_infeasible','model_error'),('solver_error','solver_error'),
    ('unsupported_model_regime','unsupported_model_regime')])
def test_unknown_tail_not_negative_or_noninformative_censor(status,reason):
    r=contract().first_failure_label(0,30,None,15,status)
    assert r['value'] is None and not r['valid'] and r['unknown_reason']==reason
    assert r['administrative_censoring']==(status=='completed')
    # A short complete prefix and a known early positive survive later errors.
    assert contract().first_failure_label(0,15,None,15,status)['value']==0
    assert contract().first_failure_label(0,30,10,15,status)['value']==1

def test_recovered_failure_stays_outside_first_risk_set():
    assert not contract().first_failure_label(30,15,15,90,'completed')['at_risk']

def test_family_overlap_components_and_stress_ood_isolation():
    m=contract()
    rows=[dict(scenario_id='a',family_id='fa',parent_template_id='parent',base_state_id='b1',
               load_start_minute=0,load_end_minute=90,sampling_cohort='main'),
          dict(scenario_id='a_variant',family_id='fv',parent_template_id='parent',base_state_id='b2',
               load_start_minute=500,load_end_minute=590,sampling_cohort='main'),
          dict(scenario_id='overlap',family_id='fo',parent_template_id='po',base_state_id='bo',
               load_start_minute=550,load_end_minute=650,sampling_cohort='main'),
          dict(scenario_id='ood',family_id='fx',parent_template_id='px',base_state_id='bx',
               load_start_minute=600,load_end_minute=700,sampling_cohort='stress_ood')]
    split=m.family_splits(rows,seed=42,embargo_minutes=60)
    assert len({split[x['scenario_id']]['group_id'] for x in rows})==1
    assert all(split[x['scenario_id']]['split']=='stress_ood' for x in rows)
    assert split==m.family_splits(list(reversed(rows)),seed=42,embargo_minutes=60)

def test_action_schema_is_exactly_six_executable_kw_targets():
    m=contract()
    assert set(m.ACTION_SCHEMA)=={'gt_1','gt_2','gt_3','grid_import','diesel','absorption_cooling_kW'}
    assert set(x['unit'] for x in m.ACTION_SCHEMA.values())=={'kW'}
    for key in m.ACTION_SCHEMA:m.validate_action({'targets':{key:10.}})
    for key in ('boiler_steam_lbph','electric_chiller_cooling_kW_1','unsafe_override'):
        with pytest.raises(ValueError):m.validate_action({'targets':{key:10.}})

def test_contract_mixing_rejected_and_statistical_edges_train_only():
    m=contract()
    with pytest.raises(ValueError):m.require_single_contract([{'execution_contract_id':'a'},{'execution_contract_id':'b'}])
    with pytest.raises(ValueError):m.validate_statistical_edges([dict(edge_type='statistical',fit_split='test',fit_dataset_id='x')])
    m.validate_statistical_edges([dict(edge_type='statistical',fit_split='train',fit_dataset_id='x')])

@pytest.fixture(scope='module')
def env():
    from hospital_ies.control_env import HospitalIESEnv
    return HospitalIESEnv(ROOT,execution_contract=json.loads((ROOT/'configs/p1b_execution_research.json').read_text()))

def scenario(**kw):
    s=dict(id='p2a_contract',stage=4,start_hour=4300,duration_steps=4,step_minutes=15,
           shock={'e':.2},shock_start_minute=30.,enable_propagation=True,response_delay_minutes=0.)
    s.update(kw);return s

def test_future_script_change_does_not_change_current_observation_or_graph(env):
    m=contract()
    s=scenario()
    env.reset(scenario=s);env.core.boundary();a=m.decision_snapshot(env)
    s.update(shock={'e':2.5,'c':1.2},shock_start_minute=45.,shock_duration_minutes=5.)
    env.reset(scenario=s);env.core.boundary();b=m.decision_snapshot(env)
    assert a==b
    assert 'scenario' not in a and 'shock_start_minute' not in json.dumps(a)
    assert a['decision_time_min']==a['observation_time_min']==0

def test_continuous_availability_and_effective_capacity_are_preserved(env):
    m=contract();values=[]
    for av in (0.,.5,1.):
        env.reset(scenario=dict(id='availability',stage=1,start_hour=4300,duration_steps=2,faults={'GT_1':av}))
        env.core.boundary();snap=m.decision_snapshot(env)
        node=next(x for x in snap['graph']['nodes'] if x['id']=='GT_1')
        values.append(node['effective_capacity'])
        assert snap['features']['availability_GT_1']==av
        assert node['availability']==av
    assert values[0]==0 and values[1]==pytest.approx(values[2]/2)

@pytest.mark.parametrize('fault_start',[0.,7.5])
def test_l4b_accepts_simultaneous_and_staggered_events_without_physics_change(env,fault_start):
    from hospital_ies.resilience import validate_scenario
    s=scenario(event_class='L4b',event_schema_version='p2a.events.v1',
        faults={'TOWER_1':.5},fault_start_minute=fault_start,shock_start_minute=0.)
    assert validate_scenario(s)['event_class']=='L4b'
    env.reset(scenario=s)
    assert env.core.data_at(fault_start)[3]['TOWER_1']==.5
    if fault_start:assert env.core.data_at(0)[3]['TOWER_1']==1.
    for bad in ({'event_class':'L4a'},{'event_schema_version':'wrong'},{'event_class':'L4'}):
        with pytest.raises(ValueError):validate_scenario(dict(s,**bad))

def test_algorithm_registry_has_no_false_reproduction_or_training_claim():
    assert importlib.util.find_spec('hospital_ies.method_registry') is not None
    m=importlib.import_module('hospital_ies.method_registry')
    registry=m.registry()
    assert {'dnipc','d_morph_dspce','ols_pce','lar_pce','gaussian_copula','rf','gru','graph_temporal','priority_milp','secure_milp'}<=set(registry)
    assert registry['d_morph_dspce']['status']=='candidate'
    assert all(v['fit_dataset_ids']==[] for v in registry.values())
    with pytest.raises(ValueError):m.validate_method_use('ols_pce',view='decision',online_control=True)
    with pytest.raises(ValueError):m.validate_method_use('rf',view='decision',online_control=True)

def test_observed_exogenous_logs_are_not_marked_as_endogenous_failures():
    from types import SimpleNamespace
    from hospital_ies.dataset_export import observed_events
    env=SimpleNamespace(event_history=[dict(event_id=1,kind='shock_changed',time_minute=5),
        dict(event_id=2,kind='availability_changed',time_minute=5)])
    events=observed_events(env,dict(duration_steps=2),15)
    assert events[0]['origin']=='exogenous'
    assert events[1]['origin']=='state_observation'
