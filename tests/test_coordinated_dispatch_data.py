"""Metadata only; no annual physical parsing, environment or trajectory."""
import copy
import importlib
from collections import Counter
from pathlib import Path
import sys

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))


def api():
    try:
        return importlib.import_module('tools.coordinated_dispatch.data_design')
    except ModuleNotFoundError:
        pytest.fail('Independent v12 metadata data-design module is missing')


def deny_csv(*args, **kwargs):
    pytest.fail('Metadata registration must not parse annual physical CSV rows')


def test_exposed_pool_and_twelve_original_counterexamples_keep_exact_physics(monkeypatch):
    monkeypatch.setattr(pd, 'read_csv', deny_csv)
    d=api();pool=d.build_exposed_pool(ROOT)
    assert len(pool['scenarios'])==576 and len(pool['roots'])==70
    assert pool['registered_definition_count']==840 and pool['unrun_definition_count']==264
    assert Counter(s['analysis_class'] for s in pool['scenarios'])==dict(L1=111,L2=112,L3=112,L4a=55,L4b=116,N2=70)
    original={s['id']:s for s in pool['scenarios']};before=copy.deepcopy(pool)
    targeted=d.build_targeted(pool)
    assert len(targeted['scenarios'])==12 and sum(s['analysis_class']=='N2' for s in targeted['scenarios'])==1
    assert {'v11_confirm_236e68f829de83f073e2e5e4','v11_confirm_484dea56799511eec6ee6946',
            'v11_case_e0332c3a4e66694efcdb152f','v11_regression_54d04ba521cf2f5e2506c0cd'}<=set(s['id'] for s in targeted['scenarios'])
    for scene in targeted['scenarios']:
        source=original[scene['source_v11_scenario_id']]
        assert {k:scene.get(k) for k in d.PHYSICAL_FIELDS}=={k:source.get(k) for k in d.PHYSICAL_FIELDS}
        assert scene['exogenous_stream_id']==source['exogenous_stream_id']
        assert scene['duration_steps']==40 and scene['step_minutes']==15
        assert scene['experiment_role']=='development' and scene['previously_exposed_v11'] is True
    assert pool==before


def test_balanced_real_classes_and_frozen_recipes_preserve_support_and_pairing(monkeypatch):
    monkeypatch.setattr(pd, 'read_csv', deny_csv)
    d=api();pool=d.build_exposed_pool(ROOT);dev=d.build_development_selection(pool)
    assert len(dev['roots'])==24 and len(dev['scenarios'])==140
    assert Counter(r['season_quarter'] for r in dev['roots'])=={1:6,2:6,3:6,4:6}
    assert Counter(s['analysis_class'] for s in dev['scenarios'])==dict(L1=24,L2=24,L3=24,L4a=20,L4b=24,N2=24)
    assert len({s['initiating_device'] for s in dev['scenarios'] if s.get('initiating_device')})==19
    frozen=d.build_frozen_proposal(pool,dev)
    assert len(frozen['scenarios'])==140 and len(frozen['recipes'])==3
    assert frozen['resource_limits']['max_registered_pairs']==2400
    source={s['id']:s for s in pool['scenarios']};groups={}
    for scene in frozen['scenarios']:
        old=source[scene['source_v11_scenario_id']]
        assert scene['start_hour']==old['start_hour'] and scene['base_state_id']==old['base_state_id']
        assert scene['full_source_support']==old['full_source_support'] and scene['duration_steps']==old['duration_steps']==40
        assert scene['requires_policy_freeze'] is True and scene['experiment_role']=='confirmation'
        assert scene['selection_allowed'] is False and scene['fit_allowed'] is False
        assert scene['new_independent_background'] is False
        d.validate_scenario(scene)
        if scene['analysis_class']=='N2':
            assert scene['physical_duplicate_of']==old['id'] and scene['exogenous_stream_id']==old['exogenous_stream_id']
        else:
            assert scene['exogenous_stream_id']!=old['exogenous_stream_id']
            for left,right in zip(old['exogenous_events'],scene['exogenous_events']):
                assert right['start_minute']-left['start_minute']==scene['recipe']['phase_delta_minutes']
                assert right['end_minute']<=480.
        groups.setdefault(scene['root_family_id'],{})[scene['analysis_class']]=scene
    for group in groups.values():
        assert group['L1']['exogenous_events']==group['L3']['exogenous_events']
        assert group['L1']['exogenous_stream_id']==group['L3']['exogenous_stream_id']
        if 'L4a' in group:
            assert group['L2']['exogenous_events']==group['L4a']['exogenous_events']
    assert frozen['new_physical_spec_count']==116 and frozen['normal_physical_repeat_count']==24
    bad=copy.deepcopy(d.FROZEN_RECIPES);bad[0]['duration_scale']=10.
    with pytest.raises(ValueError,match='support|tail|horizon'):
        d.build_frozen_proposal(pool,dev,recipes=bad)


def test_manifest_uses_original_builder_with_injected_contracts_and_rejects_seal(monkeypatch):
    monkeypatch.setattr(pd, 'read_csv', deny_csv)
    d=api();pool=d.build_exposed_pool(ROOT);target=d.build_targeted(pool);seen=[]
    def builder(root,scenes,policies,contract,schemas,options,splits):
        seen.append(copy.deepcopy((scenes,contract,schemas,options,splits)))
        return dict(signature='temporary',scenarios=copy.deepcopy(scenes),policies=policies,
                    execution_contract=contract,schemas=schemas,options=options,splits=splits,
                    simulator_hash='same-physics',execution_contract_id='same-contract')
    monkeypatch.setattr(d,'_build_manifest',builder)
    options=dict(source_sha256={'new_runtime.py':'a'*64},planning_interface_id='coordinated_joint.current.v12',
        action_schema='coordinated_dispatch.action.v12',extra_contracts={'numerical_contract_id':'unscaled_original_rows.v1',
        'standby_contract_id':'soft_third_gt_exposure.v1','service_budget_contract_id':'v12_test'})
    result=d.build_manifest(target['scenarios'],target['roots'],workspace_root=ROOT,**options)
    assert len(seen)==1 and result['source_sha256']==options['source_sha256']
    assert result['planning_interface_id']==options['planning_interface_id'] and result['schemas']['action']==options['action_schema']
    assert result['service_budget_contract_id']=='v12_test' and result['execution_model_id']=='M0'
    assert result['strict_final_gate']=='not_verified' and result['original_final12_evaluated'] is False
    assert len(result['sealed_final_days'])==12 and all(s['experiment_role']=='development' for s in result['splits'].values())
    assert result['signature']==d.digest({k:v for k,v in result.items() if k!='signature'})
    bad=copy.deepcopy(target['scenarios'][0]);bad['start_hour']=21*24+1
    bad['full_source_support']=d.validate_observation_support(bad)
    with pytest.raises(ValueError,match='[Ss]ealed|support'):
        d.build_manifest([bad],target['roots'],workspace_root=ROOT,**options)
    with pytest.raises(ValueError,match='contract'):
        d.build_manifest(target['scenarios'],target['roots'],workspace_root=ROOT,
            **dict(options,extra_contracts={'scenarios':[]}))
