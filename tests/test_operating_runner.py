"""Registration/receipt contracts only; no real physical episodes are run."""
import copy
import importlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]


def api():
    try:return importlib.import_module('tools.operating_response.runner')
    except ModuleNotFoundError as exc:pytest.fail('Missing v15 runner: '+str(exc),pytrace=False)


def proposal():
    from tools.operating_response.data_design import normal_proposal
    return normal_proposal()


def test_legacy_e6_and_f_specs_and_factory_are_exact_not_new_aliases():
    m=api();from tools.engineering_model import runner as old
    specs=m.policy_specs();saved=old.policy_specs()
    assert specs['E']==saved['E'] and specs['F']==saved['F']
    assert type(m.factory(specs['E'])) is type(old.factory(saved['E']))
    assert type(m.factory(specs['F'])) is type(old.factory(saved['F']))
    assert {'K0','K1','K2','A2','C2'}<=set(specs)
    assert len({s['policy_id'] for s in specs.values()})==len(specs)
    assert specs['K2']['kind']=='operating_response'
    assert specs['A2']['config']['phase_constraints_enabled'] is False
    assert specs['C2']['config']['continuity'] is False
    changed=copy.deepcopy(specs['E']);changed['config']['p0_preparation_before_commitment']=False
    with pytest.raises(ValueError,match='[Ff]rozen|[Ii]dentity'):m.factory(changed)


def test_registration_resolves_nested_model_and_is_immutable(tmp_path,monkeypatch):
    m=api();monkeypatch.setattr(m,'RUN',tmp_path/'run')
    p=proposal();dest=m.RUN/'experiments'/'registered';before=copy.deepcopy(p)
    specs=[m.policy_specs()['E'],m.policy_specs()['K1']]
    plan=m.register(p,dest,specs,model_id='M15_local_response',model_config={},scenario_ids=[p['scenarios'][0]['id']])
    assert plan['registered_pairs']==2 and plan['model_id']=='M15_local_response'
    assert plan['model_config']['gt_dynamics']['gt_start_delay_minutes']==10.
    assert plan['model_config']['diesel_local_response']['inertia_H_seconds']==.7359
    assert p==before and m.read(dest/'manifest.json')['model_config']==plan['model_config']
    assert (dest/'source_snapshot/tools/operating_response/diesel.py').exists()
    with pytest.raises(FileExistsError):m.register(p,dest,specs,model_id='M15_local_response',model_config={})


def test_duplicate_policies_unknown_model_and_c_path_fail_before_writes(tmp_path,monkeypatch):
    m=api();monkeypatch.setattr(m,'RUN',tmp_path/'run');p=proposal();spec=m.policy_specs()['E']
    with pytest.raises(ValueError,match='[Dd]istinct'):
        m.register(p,m.RUN/'experiments/duplicate',[spec,spec],model_id='M14_engineering',model_config={})
    with pytest.raises(ValueError):
        m.register(p,m.RUN/'experiments/badmodel',[spec],model_id='M15_instant_fake',model_config={})
    with pytest.raises(ValueError):m.check_output_path(Path('C:/v15_forbidden_data'))
    assert not m.RUN.exists()


def test_canonical_registered_budget_allows_more_than_old336_and_rejects_1200(tmp_path,monkeypatch):
    m=api();monkeypatch.setattr(m,'RUN',tmp_path/'run');p=proposal();spec=m.policy_specs()['E']
    m.write(m.RUN/'experiments/prior/plan.json',{'registered_pairs':336})
    m.register(p,m.RUN/'experiments/allowed',[spec],model_id='M14_engineering',model_config={},scenario_ids=[p['scenarios'][0]['id']])
    # RESOURCE_REVISION_001 retained the 288 real t0 errors and raised the
    # internal ceiling800->1200; keep testing a filled ceiling plus two.
    m.write(m.RUN/'experiments/another/plan.json',{'registered_pairs':863})
    with pytest.raises(ValueError,match='[Cc]eiling|[Bb]udget'):
        m.register(p,m.RUN/'experiments/over',[spec],model_id='M14_engineering',model_config={},scenario_ids=[s['id'] for s in p['scenarios'][:2]])
    assert not (m.RUN/'experiments/over').exists()
    with pytest.raises(ValueError,match='[Cc]anonical|[Rr]un|[Ee]xperiment'):
        m.register(p,tmp_path/'outside',[spec],model_id='M14_engineering',model_config={})


def test_source_drift_rejected_before_environment_or_attempt(tmp_path,monkeypatch):
    m=api();monkeypatch.setattr(m,'RUN',tmp_path/'run');p=proposal();spec=m.policy_specs()['E']
    directory=m.RUN/'experiments/drift'
    m.register(p,directory,[spec],model_id='M14_engineering',model_config={},scenario_ids=[p['scenarios'][0]['id']])
    monkeypatch.setattr(m,'source_binding',lambda:{'changed.py':'b'*64})
    monkeypatch.setattr(m,'OperatingHospitalIESEnv',lambda *a,**kw:pytest.fail('env constructed before drift guard'))
    with pytest.raises(ValueError,match='[Ss]ource|[Cc]hanged'):m.run_batch(directory)
    assert not (directory/'ATTEMPTS.jsonl').exists()


def test_matching_keeps_reset_and_policy_dependent_event_states_separate():
    m=api();a=dict(initial_state={'gt_prev':[100.,0.,0.]},
        scenario={'start_hour':1,'duration_steps':64,'step_minutes':15.,'faults':{'GRID':0.},
                  'fault_start_minute':360.,'enable_propagation':True,'first_event_minute':360.},
        segments=[{'start_minute':0.,'end_minute':360.,'post_state':{'gt_prev':[100.,0.,0.]}},
                  {'start_minute':360.,'end_minute':960.,'pre_state':{'gt_prev':[100.,0.,0.]}}])
    b=copy.deepcopy(a);b['segments'][1]['pre_state']['gt_prev'][0]=200.
    r=m.matching_evidence([a,b])
    assert r['same_initial_state'] and r['same_exogenous_definition']
    assert r['policy_dependent_prefix_states_possible'] and r['same_injection_state'] is False
    b['scenario']['enable_propagation']=False
    assert not m.matching_evidence([a,b])['same_exogenous_definition']


def test_batch_uses_registered_full_not_legacy600_for_long_unknown(tmp_path,monkeypatch):
    m=api();monkeypatch.setattr(m,'RUN',tmp_path/'run');p=proposal();dest=m.RUN/'experiments/unknown'
    m.register(p,dest,[m.policy_specs()['E']],model_id='M14_engineering',model_config={},scenario_ids=[p['scenarios'][0]['id']])
    class NoPhysics:
        def __init__(self,*args,**kwargs):self.model_id=kwargs['model_id']
    monkeypatch.setattr(m,'OperatingHospitalIESEnv',NoPhysics)
    def fake_episode(env,scene,policy,seed,manifest):
        return dict(scenario=scene,initial_state={'state':{}},segments=[],
            summary={'status':'local_response_domain_exceeded','observed_until_minute':600.,'requested_end_minute':1440.},
            error={'type':'LocalResponseDomainError'})
    monkeypatch.setattr(m,'run_episode',fake_episode)
    monkeypatch.setattr(m,'analyze_payload',lambda p,e:{'observed_until_minute':600.,'requested_end_minute':1440.,
        'windows':{'full':{'complete':True},'registered_full':{'complete':False}}})
    monkeypatch.setattr(m,'export_rows',lambda records,out:{'synthetic_test_only':True,'count':len(records)})
    result=m.run_batch(dest)
    assert result['actual_attempts']==1 and result['complete_full']==0 and result['unknown_full']==1
    assert result['complete_legacy_full600']==1
    record=m.read(next((dest/'pairs').glob('*/RESULT.json')))
    assert record['error']['type']=='LocalResponseDomainError'
    assert next((dest/'pairs').glob('*/trajectory.json.gz')).exists()
    with pytest.raises(FileExistsError):m.run_batch(dest)
