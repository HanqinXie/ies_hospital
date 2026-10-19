"""Small mocked package/metadata checks; never initialize an environment."""
import copy
import hashlib
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))


def api():
    try:
        return importlib.import_module('tools.autonomous_dispatch.portable_replay')
    except ModuleNotFoundError:
        pytest.fail('Selective autonomous portable replay helper is missing')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + '\n', encoding='utf-8')
    return path


def package_fixture(tmp_path, monkeypatch, a, *, model='M0', bad_freeze_sha=False, unsafe_entry=False, standby=False):
    root = tmp_path / 'package'; root.mkdir(parents=True)
    bound = root / 'tools/autonomous_dispatch/numerics.py'; bound.parent.mkdir(parents=True)
    bound.write_bytes(b'unchanged numerical source')
    binding = {'tools/autonomous_dispatch/numerics.py': sha(bound)}
    specs = [{'policy_id': 'priority_milp', 'kind': 'priority'}, {'policy_id': 'secure_milp', 'kind': 'secure'},
             {'policy_id': 'margin15', 'kind': 'joint', 'config': {'variant': 'coupled', 'critical_reserve_multiplier': 1.5}}]
    scene = dict(id='scene', experiment_role='confirmation', is_final_blind=False, start_hour=100,
                 duration_steps=40, step_minutes=15., arbitrary_frozen_config={'fixed': 7})
    old = dict(signature='frozen_manifest_signature', files=[], scenarios=[scene],
        splits={'scene': {'experiment_role': 'confirmation', 'fit_allowed': False}},
        execution_contract={'name': 'original'}, execution_contract_id='original:hash', simulator_hash='physical_hash',
        schemas={'action': 'autonomous_dispatch.legacy_or_joint_action.v3'}, options={'seed': 16},
        execution_model_id=model, numerical_contract_id='unscaled_original_rows.v1',
        planning_interface_id='joint_command.margin.v3', sealed_final_days=[21], strict_final_gate='not_verified')
    if standby:
        specs[-1]['policy_id'] = 'standby15'
        specs[-1]['config']['preserve_one_gt_standby'] = True
        old.update(standby_contract_id='soft_third_gt_exposure.v1', planning_interface_id='joint_command.standby.v5')
        old['schemas']['action'] = 'autonomous_dispatch.legacy_or_joint_action.v5'
    freeze = dict(source_sha256=binding, policy_specs=specs, development_completed=True,
        execution_model_id=model, numerical_contract_id=old['numerical_contract_id'], planning_interface_id=old['planning_interface_id'])
    if standby:
        freeze['standby_contract_id'] = old['standby_contract_id']
    freeze_path = write(root / 'run/design/ORIGINAL_FREEZE.json', freeze)
    preference = dict(original_preferences={'duration': 2}, confirmation_gate=dict(
        policy_freeze_path='D:/unavailable-original-workspace/DO_NOT_OPEN_FREEZE.json',
        policy_freeze_sha256='0' * 64 if bad_freeze_sha else sha(freeze_path)))
    pref_path = write(root / 'run/design/PREFERENCE.json', preference)
    mp = write(root / 'batches/b/evidence/global_manifest.json', old)
    plan = dict(schema=('autonomous_dispatch.standby.matched_full_feedback.v5' if standby else 'autonomous_dispatch.unscaled.matched_full_feedback.v4'), manifest_sha256=sha(mp),
                scenario_ids=['scene'], policy_specs=specs, phase='confirmation',
                preference_sha256=sha(pref_path), source_sha256=binding)
    pp = write(root / 'batches/b/evidence/registered_plan.json', plan)
    package = dict(kind='v9_state_dispatch_selected_raw', batches=[dict(name='b',
        evidence={k:dict(path=p.relative_to(root).as_posix(),sha256=sha(p)) for k,p in [('manifest',mp),('plan',pp)]},
        preference_file=pref_path.relative_to(root).as_posix())], examples=[dict(batch='b',scenario_id='scene')],
        entries=[dict(archive_path='../escape.json' if unsafe_entry else freeze_path.relative_to(root).as_posix(),
                      sha256=sha(freeze_path)),dict(archive_path=pref_path.relative_to(root).as_posix(),sha256=sha(pref_path))])
    monkeypatch.setattr(a.previous, 'verify_integrity', lambda path: package)
    seen = {'factory': [], 'register': [], 'builder': []}
    def build(data_root, scenes, policies, contract, schemas, options, splits):
        seen['builder'].append((data_root,copy.deepcopy(scenes),copy.deepcopy(splits)))
        return dict(signature='builder_signature', files=[], scenarios=scenes, policies=policies,
            execution_contract=contract, execution_contract_id=old['execution_contract_id'],
            simulator_hash=old['simulator_hash'], schemas=schemas,options=options,splits=splits)
    def factory(plan, manifest):
        seen['factory'].append(copy.deepcopy(manifest))
        return lambda *args,**kwargs: pytest.fail('No environment may be initialized in metadata replay tests')
    def register(manifest_path, plan_path, **kwargs):
        manifest=json.loads(Path(manifest_path).read_text(encoding='utf-8'))
        pref=json.loads(Path(kwargs['preference_path']).read_text(encoding='utf-8'))
        freeze_local=Path(pref['confirmation_gate']['policy_freeze_path'])
        assert freeze_local.is_absolute() and sha(freeze_local)==pref['confirmation_gate']['policy_freeze_sha256']
        assert freeze_local.read_bytes()==freeze_path.read_bytes()
        assert json.loads(freeze_local.read_text(encoding='utf-8'))==freeze
        seen['register'].append((manifest,kwargs,pref))
        result=dict(registered_pair_count=len(specs),signature='new_plan')
        write(Path(plan_path),result)
        return result
    backend=SimpleNamespace(_environment_factory=factory,register_batch=register,_source_binding=lambda:binding)
    monkeypatch.setattr(a, '_backend', lambda name,path: backend)
    monkeypatch.setattr(a, 'build_manifest', build)
    return SimpleNamespace(root=root,old=old,specs=specs,scene=scene,freeze=freeze,freeze_path=freeze_path,
                           preference=preference,pref_path=pref_path,bound=bound,seen=seen,backend=backend)


def test_selected_schema_and_source_binding_never_admit_legacy_annual_route(tmp_path, monkeypatch):
    a=api(); old_routes=copy.deepcopy(a.previous.ROUTES); f=package_fixture(tmp_path,monkeypatch,a)
    environment={k:v for k,v in os.environ.items() if k not in {'PYTHONPATH','PYTHONUTF8'}}
    fresh=subprocess.run([sys.executable,'-B','-m','tools.autonomous_dispatch.portable_replay','--help'],
        cwd=Path(__file__).resolve().parents[1],env=environment,capture_output=True,text=True,timeout=30)
    assert fresh.returncode==0,fresh.stderr
    request=a.replay_request(f.root,'b',scenario_ids=['scene'])
    assert request['runner_module']=='tools.autonomous_dispatch.closed_loop_v4'
    assert request['scenarios']==[f.scene]
    assert set(a.ROUTES)=={'autonomous_dispatch.matched_full_feedback.v1',
        'autonomous_dispatch.contacts.matched_full_feedback.v2',
        'autonomous_dispatch.margin.matched_full_feedback.v3',
        'autonomous_dispatch.unscaled.matched_full_feedback.v4',
        'autonomous_dispatch.standby.matched_full_feedback.v5'}
    assert a.previous.ROUTES==old_routes
    with pytest.raises(ValueError,match='selected'):a.replay_request(f.root,'b',scenario_ids=['not-packaged'])
    f.bound.write_bytes(b'changed source')
    with pytest.raises(ValueError,match='source SHA'):a.replay_request(f.root,'b')


def test_prepare_retains_m0_m1_numeric_schema_config_and_selective_factory(tmp_path,monkeypatch):
    a=api()
    for model in ('M0','M1_ordered_boundary'):
        f=package_fixture(tmp_path/model,monkeypatch,a,model=model)
        receipt=a.prepare_replay(f.root,tmp_path/(model+'_output'),'b',scenario_ids=['scene'])
        manifest,kw,pref=f.seen['register'][0]
        for key in ('execution_model_id','numerical_contract_id','planning_interface_id','schemas','execution_contract','options','splits'):
            assert manifest[key]==f.old[key]
        assert manifest['scenarios']==[f.scene] and kw['policy_specs']==f.specs
        assert receipt['episodes_started']==0
        assert f.seen['builder'][0][0]==f.root.resolve() and len(f.seen['factory'])==1
        assert f.seen['factory'][0]['execution_model_id']==model


def test_confirmation_copies_original_freeze_bytes_and_rebinds_only_local_gate(tmp_path,monkeypatch):
    a=api(); f=package_fixture(tmp_path/'valid',monkeypatch,a)
    output=tmp_path/'external';a.prepare_replay(f.root,output,'b')
    manifest,kwargs,pref=f.seen['register'][0]
    gate=Path(pref['confirmation_gate']['policy_freeze_path'])
    assert gate.is_relative_to(output) and gate.read_bytes()==f.freeze_path.read_bytes()
    changed=copy.deepcopy(pref);changed['confirmation_gate']['policy_freeze_path']=f.preference['confirmation_gate']['policy_freeze_path']
    assert changed==f.preference and f.pref_path.read_text(encoding='utf-8')==json.dumps(f.preference,sort_keys=True,indent=2)+'\n'
    with pytest.raises(ValueError,match='outside'):a.prepare_replay(f.root,f.root/'forbidden','b')
    bad=package_fixture(tmp_path/'bad',monkeypatch,a,bad_freeze_sha=True)
    with pytest.raises((ValueError,FileNotFoundError),match='[Ff]reeze'):a.prepare_replay(bad.root,tmp_path/'bad_output','b')
    unsafe=package_fixture(tmp_path/'unsafe',monkeypatch,a,unsafe_entry=True)
    with pytest.raises(ValueError):a.prepare_replay(unsafe.root,tmp_path/'unsafe_output','b')


def test_main_and_run_use_new_prepare_and_do_not_promote_unknown(tmp_path,monkeypatch):
    a=api(); root=tmp_path/'mock_package';root.mkdir();output=tmp_path/'external';calls=[]
    def prepare(*args,**kwargs):
        calls.append((args,kwargs));output.mkdir()
        return dict(root=str(root),runner_module='tools.autonomous_dispatch.closed_loop_v4',
                    plan_path=str(output/'new_plan.json'),results_path=str(output/'results'))
    backend=SimpleNamespace(run_batch=lambda *args:dict(registered_pair_count=3,terminal_pair_count=3,
        unknown_primary_pair_count=1,attempted_this_call=3),audit_batch=lambda *args,**kwargs:dict(
        passed=True,recomputed_pair_count=3,pair_ledger=[dict(full_observation_horizon=False)],errors=[]))
    monkeypatch.setattr(a,'ROOT',root);monkeypatch.setattr(a,'prepare_replay',prepare)
    monkeypatch.setattr(a,'_backend',lambda *args:backend)
    result=a.main(['run','--root',str(root),'--batch','b','--scene','scene','--output',str(output)])
    assert result==1 and len(calls)==1
    saved=json.loads((output/'PHYSICAL_REPLAY_RESULT.json').read_text(encoding='utf-8'))
    assert saved['full_physical_simulator_rerun'] is False


def test_v5_route_preserves_standby_contract_and_rejects_freeze_contract_drift(tmp_path,monkeypatch):
    a=api()
    assert a.ROUTES.get('autonomous_dispatch.standby.matched_full_feedback.v5')=='tools.autonomous_dispatch.closed_loop_v5'
    f=package_fixture(tmp_path/'valid',monkeypatch,a,model='M1_ordered_boundary',standby=True)
    request=a.replay_request(f.root,'b')
    assert request['runner_module']=='tools.autonomous_dispatch.closed_loop_v5'
    receipt=a.prepare_replay(f.root,tmp_path/'v5_output','b')
    manifest,kwargs,pref=f.seen['register'][0]
    for key in ('execution_model_id','numerical_contract_id','standby_contract_id','planning_interface_id','schemas'):
        assert manifest[key]==f.old[key]
    assert receipt['standby_contract_id']==f.old['standby_contract_id'] and receipt['episodes_started']==0
    assert Path(pref['confirmation_gate']['policy_freeze_path']).read_bytes()==f.freeze_path.read_bytes()
    f.freeze['standby_contract_id']='wrong_standby_contract'
    write(f.freeze_path,f.freeze)
    f.preference['confirmation_gate']['policy_freeze_sha256']=sha(f.freeze_path)
    write(f.pref_path,f.preference)
    request['package_entries'][0]['sha256']=sha(f.freeze_path)
    bad_output=tmp_path/'bad_v5_output';bad_output.mkdir()
    with pytest.raises(ValueError,match='[Ss]tandby|contract'):
        a._local_preference(request,f.backend,bad_output)
