"""A repaired round uses a fresh freeze without replacing its predecessor."""
import copy
import importlib
import json
from pathlib import Path

import pytest


def fixture(monkeypatch, tmp_path):
    r=importlib.import_module('tools.execution_recovery.runner')
    monkeypatch.setattr(r,'RUN',tmp_path)
    monkeypatch.setattr(r,'_experiment_path',lambda p:Path(p).resolve())
    monkeypatch.setattr(r,'model_contract',lambda *args:('test',{'execution_recovery':{}}))
    monkeypatch.setattr(r,'factory',lambda spec:None)
    monkeypatch.setattr(r,'source_binding',lambda:{'test.py':'a'*64})
    monkeypatch.setattr(r,'_snapshot',lambda binding:tmp_path/'source_snapshot')
    monkeypatch.setattr(r,'build_manifest',lambda p,*args,**kwargs:copy.deepcopy(p))
    spec=dict(policy_id='test_policy',config={})
    frozen=dict(kind='v18_final_shared_model_freeze',source_sha256={'test.py':'a'*64},
        model_id='M18_execution_recovery',model_config={'execution_recovery':{}},policies={'C':spec})
    frozen['signature']=r.digest(frozen)
    old=tmp_path/'MODEL_FREEZE.json';old.write_text('{"old_round":true}',encoding='utf8')
    new=tmp_path/'MODEL_FREEZE_REPAIR01.json';new.write_text(json.dumps(frozen),encoding='utf8')
    proposal=dict(scenarios=[dict(id='scene',requires_policy_freeze=True)],freeze_signature=frozen['signature'])
    return r,spec,frozen,proposal,old,new


def test_registration_accepts_explicit_new_freeze_and_keeps_old_bytes(monkeypatch,tmp_path):
    r,spec,frozen,proposal,old,new=fixture(monkeypatch,tmp_path)
    before=old.read_bytes()
    plan=r.register(proposal,tmp_path/'experiments/new',[spec],model_id=frozen['model_id'],
        model_config=frozen['model_config'],freeze_path=new.name)
    manifest=json.loads((tmp_path/'experiments/new/manifest.json').read_text(encoding='utf8'))
    assert manifest['freeze_signature']==frozen['signature']
    assert plan['source_sha256']==frozen['source_sha256']
    assert old.read_bytes()==before


@pytest.mark.parametrize('outside',('../other.json','nested/freeze.json','MODEL_FREEZE.txt'))
def test_explicit_freeze_is_a_direct_json_file_in_current_run(monkeypatch,tmp_path,outside):
    r,spec,frozen,proposal,old,new=fixture(monkeypatch,tmp_path)
    with pytest.raises(ValueError,match='freeze'):
        r.register(proposal,tmp_path/'experiments/new',[spec],model_id=frozen['model_id'],
            model_config=frozen['model_config'],freeze_path=outside)
    assert not (tmp_path/'experiments/new').exists()


def test_explicit_freeze_still_rejects_wrong_signature(monkeypatch,tmp_path):
    r,spec,frozen,proposal,old,new=fixture(monkeypatch,tmp_path)
    proposal['freeze_signature']='wrong'
    with pytest.raises(ValueError,match='freeze'):
        r.register(proposal,tmp_path/'experiments/new',[spec],model_id=frozen['model_id'],
            model_config=frozen['model_config'],freeze_path=new)
    assert not (tmp_path/'experiments/new').exists()


def test_default_freeze_keeps_existing_registration_semantics(monkeypatch,tmp_path):
    r,spec,frozen,proposal,old,new=fixture(monkeypatch,tmp_path)
    old.write_bytes(new.read_bytes())
    r.register(proposal,tmp_path/'experiments/new',[spec],model_id=frozen['model_id'],
        model_config=frozen['model_config'])


def test_campaign_reads_explicit_freeze_without_replacing_default(monkeypatch,tmp_path):
    r,spec,frozen,proposal,old,new=fixture(monkeypatch,tmp_path)
    c=importlib.import_module('tools.execution_recovery.campaign')
    monkeypatch.setattr(c,'B',tmp_path)
    monkeypatch.setattr(c,'OLD',tmp_path/'old')
    (tmp_path/'old/design').mkdir(parents=True)
    original=dict(scenarios=[dict(id='scene')])
    (tmp_path/'old/design/MAIN_PROPOSAL.json').write_text(json.dumps(original),encoding='utf8')
    monkeypatch.setattr('tools.execution_recovery.data_design.panel',lambda name,scenes,**kwargs:dict(kwargs,scenarios=scenes))
    p=c.proposal('main',freeze_path=new.name)
    assert p['freeze_signature']==frozen['signature']
    assert json.loads(old.read_text(encoding='utf8'))=={'old_round':True}
