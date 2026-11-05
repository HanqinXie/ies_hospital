"""Pure data-design/observable-view contracts; no hospital simulation."""
from __future__ import annotations
import copy,importlib,json
from collections import Counter
from functools import lru_cache
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[1]
def mod(name):return importlib.import_module('tools.integrated_experiment.'+name)

@lru_cache(maxsize=1)
def get_proposal():return mod('data_design').build_proposal(ROOT,seed=20261014)

def test_preregistered_budget_and_group_roles():
    proposal=get_proposal()
    d=mod('data_design');assert len(proposal['fixed_scenarios'])==240 and len(proposal['adaptive_slots'])==60
    assert len(proposal['blocks'])==60
    assert Counter(b['role'] for b in proposal['blocks'])=={'pilot_train':36,'pilot_validation':12,'development_boundary':12}
    assert all(s['experiment_role']=='pilot_train' for s in proposal['adaptive_slots'])
    assert proposal['budget']['initial_specifications']==300 and proposal['budget']['maximum_new_specifications']==1200
    a=d.manifest_components(proposal,proposal['fixed_scenarios'])['splits'];assert len({v['group_id'] for v in a.values()})==60
    assert Counter(v['split'] for v in a.values())=={'pilot_train':144,'pilot_validation':48,'development_boundary':48}

def test_real_old_support_and_sealed_blocks_excluded():
    proposal=get_proposal()
    old=json.loads((ROOT/'outputs/v43_p2b_20261013_run01/experiment/global_manifest.json').read_text(encoding='utf-8'))
    oldblocks=old['p2b_time_blocks'];assert len([b for b in oldblocks if b['reserved_only']])==12
    for b in proposal['blocks']:
        assert all(b['raw_end_minute']<=x['raw_start_minute'] or b['raw_start_minute']>=x['raw_end_minute'] for x in oldblocks)
        assert all(b['raw_end_minute']<=x['raw_start_minute'] or b['raw_start_minute']>=x['raw_end_minute'] for x in proposal['excluded_support'])
    assert len(proposal['exposure']['sealed_final_days'])==12

def test_fixed_crosscoverage_support_and_random_onset():
    proposal=get_proposal()
    d=mod('data_design');assert {s['condition_id'] for s in proposal['fixed_scenarios']}==set(d.CONDITIONS)
    for block in proposal['blocks']:
        specs=[s for s in proposal['fixed_scenarios'] if s['time_block_id']==block['time_block_id']]
        assert len(specs)==4 and sorted(s['pressure_coordinate'] for s in specs)==[0.,1/3,2/3,1.]
        assert len({s['root_family_id'] for s in specs})==1
        for s in specs:
            assert d.approved_pressure([s['shock']['e'],s['shock']['c'],s['shock']['h'],s.get('shock_by_priority',{}).get('P0',{}).get('e',0)])
            assert 75<=s['shock_start_minute']<=135 and s['automatic_fast_shedding'] is False
            assert s['full_source_support']['raw_end_minute']-s['full_source_support']['raw_start_minute']==540
            assert s['full_source_support']['initialization_source_hours']==[s['start_hour']]
            if s['event_class']=='L4b':assert s['response_clock_anchor']=='shock_start_minute'

def test_proposal_reproducible():
    proposal=get_proposal()
    assert mod('data_design').build_proposal(ROOT,seed=20261014)==proposal

def test_adaptive_unknown_is_space_fill_without_fabricated_outcome():
    proposal=get_proposal()
    d=mod('data_design');specs=d.materialize_adaptive(proposal,{})
    assert len(specs)==60 and all(s['experiment_role']=='pilot_train' for s in specs)
    assert all(s['adaptive_reason']=='no_known_bracket_space_fill' for s in specs)
    before=d.manifest_components(proposal,proposal['fixed_scenarios'])['splits'];after=d.manifest_components(proposal,proposal['fixed_scenarios']+specs)['splits']
    assert all(before[k]['group_id']==after[k]['group_id'] for k in before)
    assert len({s['id'] for s in specs})==60 and not {s['id'] for s in specs}&{s['id'] for s in proposal['fixed_scenarios']}

def test_adaptive_rejects_validation_feedback():
    proposal=get_proposal()
    s=next(s for s in proposal['fixed_scenarios'] if s['experiment_role']=='pilot_validation')
    with pytest.raises(ValueError,match='train'):
        mod('data_design').materialize_adaptive(proposal,{s['id']:{'experiment_role':'pilot_validation','score_P0_failure':1}})

def test_background_family_role_change_rejected():
    proposal=get_proposal()
    specs=copy.deepcopy(proposal['fixed_scenarios']);specs[1]['experiment_role']='pilot_validation'
    with pytest.raises(ValueError,match='role|split|family'):
        mod('data_design').manifest_components(proposal,specs)

def test_original_joint_support_not_marginal_rectangle():
    d=mod('data_design');assert d.approved_pressure([0.,0.,.2,0.])
    assert not d.approved_pressure([0.,1.25,.2,.5])
    with pytest.raises(ValueError):d.pressure_values(1.01)

def payload(status='completed',formal=True):
    segments=[]
    for t in range(0,120,15):
        segments.append(dict(start_minute=float(t),end_minute=float(t+15),unserved_kW=[[1e-4,0,0],[0,0,0],[0,0,0],[0,0,0]],
            classification={'p0_guarantee_failure':formal and t==60},violation={'P0':False}))
    if status!='completed':segments=segments[:5]
    ds=[]
    for t in range(0,121 if status!='completed' else 120,15):
        if status!='completed' and t>75:break
        ds.append(dict(decision_time_min=float(t),features={'time_minute':float(t),'P0_e_demand_kW':100.},
            feature_metadata={k:dict(unit=u,source='causal_fixture',available_time_min=float(t),valid_time_min=float(t),measurement='ideal_observation_assumption') for k,u in [('time_minute','min'),('P0_e_demand_kW','kW')]}))
    return dict(summary={'status':status,'observed_until_minute':segments[-1]['end_minute']},segments=segments,decisions=ds)

def test_formal_view_keeps_tiny_raw_abnormal_windows():
    w=mod('firstformal_warning').warning_view(payload(),horizon=60,score_interval=(0,120))
    r=next(r for r in w['rows'] if r['decision_time_min']==15)
    assert r['legacy_any_tiny_abnormal'] is True and r['formal_warning_eligible'] is True and r['label']['value']==1
    assert w['first_classification_minute']==75 and w['first_failure_interval']==[60.,75.]
    assert next(r for r in w['rows'] if r['decision_time_min']==60)['timing_relation']=='within_first_failure_interval'
    assert next(r for r in w['rows'] if r['decision_time_min']==75)['formal_warning_eligible'] is False

def test_negative_label_after_error_is_unknown():
    w=mod('firstformal_warning').warning_view(payload('solver_error',False),horizon=60,score_interval=(0,120))
    r=next(r for r in w['rows'] if r['decision_time_min']==30)
    assert r['label']['value'] is None and not r['label']['full_horizon'] and r['label']['unknown_reason']=='solver_error'
    assert r['label']['model_error_truncated']

def test_administrative_tail_is_not_complete_negative():
    w=mod('firstformal_warning').warning_view(payload(formal=False),horizon=60,score_interval=(0,120))
    r=next(r for r in w['rows'] if r['decision_time_min']==90)
    assert r['label']['value'] is None and r['label']['administrative_censoring']

def test_causal_features_ignore_all_future_changes():
    m=mod('firstformal_warning');p=payload();before=m.causal_feature_view(p,2,history_minutes=60)
    q=copy.deepcopy(p);q['segments'][4]['classification']['p0_guarantee_failure']=False;q['decisions'][4]['features']['P0_e_demand_kW']=99999.
    assert m.causal_feature_view(q,2,history_minutes=60)==before
    assert 'label' not in before and 'first_classification_minute' not in before

def test_feature_future_timestamp_rejected():
    p=payload();p['decisions'][2]['feature_metadata']['P0_e_demand_kW']['available_time_min']=31.
    with pytest.raises(ValueError,match='future|time'):
        mod('firstformal_warning').causal_feature_view(p,2)

def test_raw_payload_unchanged_by_new_view():
    p=payload();before=copy.deepcopy(p);mod('firstformal_warning').warning_view(p,horizon=60,score_interval=(0,120));assert p==before

@pytest.mark.parametrize('clock',['available_time_min','valid_time_min'])
def test_nonfinite_feature_clock_rejected(clock):
    p=payload();p['decisions'][2]['feature_metadata']['P0_e_demand_kW'][clock]=float('nan')
    with pytest.raises(ValueError,match='time'):
        mod('firstformal_warning').causal_feature_view(p,2)
