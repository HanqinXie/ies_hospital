"""Metadata/runner tests use synthetic records only; never start a physical episode."""
import copy
import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


def api(name='runner'):
    try:
        return importlib.import_module('tools.model_closeout.' + name)
    except ModuleNotFoundError as exc:
        pytest.fail('Missing V19 registration adapter: ' + str(exc))


def seen_scenes():
    root = Path(__file__).resolve().parents[1]
    return json.loads((root/'outputs/v18_execution_recovery_20261018_run01/design/MAIN_FINAL_R1_PROPOSAL.json').read_text(encoding='utf8'))['scenarios']


def signed(module, value):
    value = copy.deepcopy(value)
    value['signature'] = module.digest(value)
    return value


def test_v19_ids_and_baselines_preserve_original_preferences():
    r = api()
    from tools.execution_recovery.runner import policy_specs
    assert r.RUN.name == 'v19_model_closeout_20261018_run01'
    assert r.MODEL_ID == 'M19_model_closeout'
    specs = r.policy_specs()
    assert specs['E'] == policy_specs()['E']
    assert specs['K1'] == policy_specs()['K1']
    assert specs['C19']['policy_id'] == 'C19_critical_reserve_v19r1'
    assert specs['C18_M19']['policy_id'] == 'C18_service_rich_M19compat'
    for key, reserve in [('C19', True), ('C18_M19', False)]:
        assert specs[key]['config'] == dict(policy_specs()['C']['config'], critical_reserve=reserve)


def test_binding_contains_inherited_chain_native_core_and_new_package():
    r = api()
    from tools.execution_recovery.runner import source_binding
    binding = r.source_binding()
    assert set(source_binding()) <= set(binding)
    for name in ('src/hospital_ies/state_transition.py', 'src/hospital_ies/model.py',
                 'src/hospital_ies/control_env.py', 'tools/model_closeout/runner.py',
                 'tools/model_closeout/data_design.py', 'tools/model_closeout/gate.py',
                 'tools/model_closeout/freeze.py', 'tools/model_closeout/campaign.py'):
        assert binding[name] == r.sha(r.ROOT/name)


def test_path_gate_rejects_old_outputs_and_own_parent(tmp_path, monkeypatch):
    r = api()
    monkeypatch.setattr(r, 'RUN', tmp_path/'v19')
    for path in (tmp_path/'v18'/'experiments'/'x', r.RUN/'experiments'):
        with pytest.raises(ValueError):
            r._experiment_path(path)
    assert r._experiment_path(r.RUN/'experiments'/'fresh') == (r.RUN/'experiments'/'fresh').resolve()


def test_seen_panel_retains_exact_source_identity_and_exogenous_fields():
    d = api('data_design'); original = seen_scenes()[1]
    p = d.panel('seen_case', [original])
    assert {k:p['scenarios'][0][k] for k in original} == original
    assert p['old80_role'] == 'seen_regression'
    assert p['historical_unseen_claim'] is False
    assert p['scenarios'][0]['v19_seen_regression'] is True
    assert p['signature'] == d.digest({k:v for k,v in p.items() if k!='signature'})


def confirmation_fixture(d):
    # Existing coordinates are only synthetic schema fixtures here. No pool is
    # selected and no metadata fixture is registered or physically executed.
    scenes = copy.deepcopy(seen_scenes()[:32])
    for i, scene in enumerate(scenes):
        scene['root_family_id'] = 'synthetic_root_' + str(i//4)
    reg = signed(d, dict(schema='hospital_ies.v19.confirmation_isolation.v1',
        freeze_signature='test_freeze', historical_training_included=True,
        v19_development_included=True, source_inventory_sha256={'synthetic.json':'a'*64},
        excluded_support=[]))
    return scenes, reg


def test_small_confirmation_accepts_eight_per_class_with_signed_isolation():
    d = api('data_design'); scenes, reg = confirmation_fixture(d)
    p = d.panel('synthetic_confirmation', scenes, freeze_signature='test_freeze',
        evaluation_role='independent_confirmation', isolation_registration=reg)
    assert p['canonical_class_counts'] == dict.fromkeys(('L1','L2','L3','L4'), 8)
    assert p['confirmation_background_count'] == 8
    assert p['historical_unseen_claim'] is False
    assert not any(s['v19_seen_regression'] for s in p['scenarios'])


@pytest.mark.parametrize('problem', ['freeze', 'overlap', 'unbound', 'classes'])
def test_confirmation_fails_closed_for_missing_freeze_or_support_proof(problem):
    d = api('data_design'); scenes, reg = confirmation_fixture(d); freeze = 'test_freeze'
    if problem == 'freeze': freeze = None
    if problem == 'overlap':
        reg.pop('signature')
        reg['excluded_support'] = [scenes[0]['full_source_support']]
        reg = signed(d, reg)
    if problem == 'unbound': reg['v19_development_included'] = False
    if problem == 'classes': scenes.pop()
    with pytest.raises(ValueError):
        d.panel('invalid_confirmation', scenes, freeze_signature=freeze,
            evaluation_role='independent_confirmation', isolation_registration=reg)


def freeze_fixture(r):
    spec = r.policy_specs()['C19']
    f = signed(r, dict(kind='v19_final_shared_model_freeze', source_sha256={'source.py':'a'*64},
        model_id='M19_model_closeout', model_config={'execution_recovery':{
            'grid_open_transfer_minutes':.25, 'local_contact_restore_delay_minutes':0.}},
        policies={'C19':spec}, evaluation_contract={'full_window_minutes':[0,600]}))
    return f, spec


def test_final_freeze_binds_model_policy_and_declared_clock_sensitivity():
    r = api(); f, spec = freeze_fixture(r)
    r.validate_final_freeze(f, f['source_sha256'], f['model_id'], f['model_config'], [spec])
    changed = copy.deepcopy(f['model_config']); changed['execution_recovery']['local_contact_restore_delay_minutes'] = .25
    reg = signed(r, dict(schema='hospital_ies.v19.assumption_preregistration.v1',
        source_freeze_signature=f['signature'], source_sha256=f['source_sha256'],
        model_id=f['model_id'], model_config=changed, policy_specs=[spec], reason='Finite contact clock'))
    r.validate_final_freeze(f, f['source_sha256'], f['model_id'], changed, [spec], sensitivity=reg)
    for mutation in ('source','policy','physical','unlisted_recovery'):
        binding=copy.deepcopy(f['source_sha256']); specs=[copy.deepcopy(spec)]; cfg=copy.deepcopy(changed); pr=copy.deepcopy(reg)
        if mutation=='source': binding['source.py']='b'*64
        if mutation=='policy': specs[0]['config']['critical_reserve']=False
        if mutation=='physical': cfg['capacity']=999999
        if mutation=='unlisted_recovery': cfg['execution_recovery']['invented_fast_path']=True
        if mutation in ('physical','unlisted_recovery'):
            pr.pop('signature');pr['model_config']=cfg;pr=signed(r,pr)
        with pytest.raises(ValueError):
            r.validate_final_freeze(f,binding,f['model_id'],cfg,specs,sensitivity=pr)


def test_freeze_rejects_stale_source_bound_test_gate():
    f = api('freeze')
    gate=dict(passed=True,exit_code=0,source_before={'x':'a'},source_after={'x':'a'},
        test_files_sha256={'test.py':'b'},test_files_after_sha256={'test.py':'b'})
    f.validate_test_gate(gate, {'x':'a'}, {'test.py':'b'})
    with pytest.raises(ValueError): f.validate_test_gate(gate, {'x':'changed'}, {'test.py':'b'})


def test_campaign_reuses_source05_metadata_without_touching_old_files():
    c = api('campaign'); r = api(); source=seen_scenes(); p=c.proposal('main')
    assert len(p['scenarios']) == 80
    for actual, original in zip(p['scenarios'],source):
        for key in ('id','start_hour','duration_steps','initialization_seed','faults','shock','exogenous_events'):
            assert actual[key] == original[key]
    assert p['source_metadata_bindings']
    assert r.RUN.name in str(c.B)


def fake_registration(r, tmp_path, monkeypatch):
    root=tmp_path/'workspace';root.mkdir();(root/'source.py').write_text('x=1\n',encoding='utf8')
    monkeypatch.setattr(r,'ROOT',root);monkeypatch.setattr(r,'RUN',root/'outputs'/'v19')
    binding={'source.py':r.sha(root/'source.py')}
    monkeypatch.setattr(r,'source_binding',lambda:binding)
    monkeypatch.setattr(r,'model_contract',lambda model, config: ({},copy.deepcopy(config)))
    monkeypatch.setattr(r,'factory',lambda spec:object())
    def manifest(proposal,source,model_id,config,**kw):
        return dict(scenarios=proposal['scenarios'],execution_model_id=model_id,model_config=config,
            model_config_signature=r.digest(config),source_sha256=source)
    monkeypatch.setattr(r,'build_manifest',manifest)
    scene=dict(id='synthetic_case',analysis_class='L1',start_hour=1,duration_steps=1,step_minutes=15)
    plan=r.register({'scenarios':[scene]},r.RUN/'experiments'/'fresh',[r.policy_specs()['E']],
        model_id=r.MODEL_ID,model_config={})
    return r.RUN/'experiments'/'fresh',plan


def test_unhandled_attempt_is_retained_with_unknown_tail_and_no_zero_substitute(tmp_path,monkeypatch):
    r=api();directory,plan=fake_registration(r,tmp_path,monkeypatch)
    monkeypatch.setattr(r,'allowed_hours',lambda manifest:{1})
    monkeypatch.setattr(r,'OperatingHospitalIESEnv',lambda *a,**k:SimpleNamespace(model_id=r.MODEL_ID,core=SimpleNamespace(time=0)))
    def fail(*args,**kw):raise RuntimeError('Deliberate synthetic runner fault')
    monkeypatch.setattr(r,'run_episode',fail)
    monkeypatch.setattr(r,'export_rows',lambda records,out:{'synthetic':True,'records':len(records)})
    result=r.run_batch(directory)
    assert result['actual_attempts']==result['unknown_full']==1
    assert result['complete_full']==0
    pair=next((directory/'pairs').iterdir())
    record=r.read(pair/'RESULT.json')
    assert record['status']=='runner_unhandled_exception' and record['analysis'] is None
    assert record['raw_path'] is None
    assert r.read(pair/'UNHANDLED_ATTEMPT_ERROR.json')['physical_tail'].startswith('unknown')
    assert [json.loads(line)['event'] for line in (directory/'ATTEMPTS.jsonl').read_text(encoding='utf8').splitlines()]==['started','finished']
    with pytest.raises(FileExistsError):r.run_batch(directory)


def test_baseline_never_rehashes_old_full_trajectory_payloads(tmp_path):
    b=api('baseline');old=tmp_path/'outputs'/'v18_execution_recovery_20261018_run01'
    (old/'baseline').mkdir(parents=True);(tmp_path/'src').mkdir();(tmp_path/'src'/'native.py').write_text('x=1')
    raw='outputs/v18_execution_recovery_20261018_run01/experiments/x/trajectory.json.gz'
    (old/'baseline'/'initial.json').write_text(json.dumps({'files':{'src/native.py':b.sha(tmp_path/'src'/'native.py'),raw:'a'*64}}),encoding='utf8')
    freeze={'kind':'v18_final_shared_model_freeze','source_sha256':{'src/native.py':b.sha(tmp_path/'src'/'native.py')}}
    freeze['signature']=b.digest(freeze)
    (old/'MODEL_FREEZE_RECOVERY01.json').write_text(json.dumps(freeze),encoding='utf8')
    protected=b.protected_files(tmp_path)
    assert 'src/native.py' in protected and raw not in protected
    assert 'outputs/v18_execution_recovery_20261018_run01/MODEL_FREEZE_RECOVERY01.json' in protected


def test_real_metadata_manifest_binds_new_model_and_confirmation_denominator():
    r=api();d=api('data_design');scenes,reg=confirmation_fixture(d)
    p=d.panel('synthetic_confirmation',scenes,freeze_signature='test_freeze',
        evaluation_role='independent_confirmation',isolation_registration=reg)
    _,config=r.model_contract(r.MODEL_ID,{})
    policies=[r.policy_specs()[k]['policy_id'] for k in ('E','K1','C19')]
    manifest=d.build_manifest(p,r.source_binding(),r.MODEL_ID,config,policies=policies)
    assert manifest['execution_model_id']==r.MODEL_ID
    assert manifest['new_independent_background_count']==8
    assert manifest['confirmation_background_count']==8
    assert manifest['model_config']==config
    assert manifest['schemas']['action']==d.ACTION_SCHEMA
    assert set(manifest['policy_action_routes'])==set(policies)
    assert manifest['source_proposal_signature']==p['signature']


def test_changed_or_missing_final_artifact_rejected_before_environment_creation(tmp_path,monkeypatch):
    r=api();directory,plan=fake_registration(r,tmp_path,monkeypatch)
    manifest=r.read(directory/'manifest.json');manifest['scenarios'][0]['requires_policy_freeze']=True
    manifest.pop('signature');manifest['signature']=r.digest(manifest)
    (directory/'manifest.json').write_text(json.dumps(manifest),encoding='utf8')
    plan.update(manifest_signature=manifest['signature'],manifest_sha256=r.sha(directory/'manifest.json'))
    (directory/'plan.json').write_text(json.dumps(plan),encoding='utf8')
    def forbidden(*a,**k):pytest.fail('Environment was constructed before final freeze validation')
    monkeypatch.setattr(r,'OperatingHospitalIESEnv',forbidden)
    with pytest.raises(ValueError,match='freeze'):
        r.run_batch(directory)
    assert not (directory/'ATTEMPTS.jsonl').exists()


def test_normal_campaign_refuses_parallel_workers_before_launch(tmp_path,monkeypatch):
    c=api('campaign');monkeypatch.setattr(c,'B',tmp_path)
    batch=tmp_path/'batch';batch.mkdir()
    (batch/'manifest.json').write_text(json.dumps({'scenarios':[{'duration_steps':288,'step_minutes':15}]}),encoding='utf8')
    (tmp_path/'NORMAL_REGISTRATION.json').write_text(json.dumps([{'directory':str(batch)}]),encoding='utf8')
    def forbidden(*a,**k):pytest.fail('A subprocess was launched for an inadmissible normal campaign')
    monkeypatch.setattr(c.subprocess,'run',forbidden)
    with pytest.raises(ValueError,match='serial'):
        c.run(SimpleNamespace(name='normal',workers=2,indices=None))


def test_runner_cli_returns_nonzero_for_retained_unknowns(monkeypatch,capsys):
    r=api()
    monkeypatch.setattr(r,'run_batch',lambda directory:dict(registered_pairs=1,actual_attempts=1,
        complete_full=0,unknown_full=1,status_counts={'runner_unhandled_exception':1},
        analysis_failure_or_unavailable_count=1))
    assert r.main(['run','--directory','synthetic'])==2


def test_gate_actual_subprocess_success_does_not_hide_source_drift(tmp_path,monkeypatch):
    g=api('gate');r=api();monkeypatch.setattr(r,'RUN',tmp_path)
    bindings=iter([{'x':'before'},{'x':'after'}])
    monkeypatch.setattr(r,'source_binding',lambda:next(bindings))
    monkeypatch.setattr(g,'test_binding',lambda:{'test.py':'unchanged'})
    monkeypatch.setattr(g.sys,'argv',['gate','--name','synthetic_drift','--',g.sys.executable,'-c','pass'])
    assert g.main()==1
    record=r.read(tmp_path/'test_gates'/'synthetic_drift.json')
    assert record['exit_code']==0 and record['passed'] is False


def test_budget_counts_started_failures_and_unfinished_attempts_separately_from_slots(tmp_path,monkeypatch):
    r=api();monkeypatch.setattr(r,'RUN',tmp_path)
    batch=tmp_path/'experiments'/'synthetic';batch.mkdir(parents=True)
    (batch/'plan.json').write_text(json.dumps({'registered_pairs':3}),encoding='utf8')
    rows=[{'event':'started','scenario_id':'x','policy_id':'E','utc':'2026-10-18T00:00:00+00:00'},
          {'event':'finished','scenario_id':'x','policy_id':'E','status':'solver_error'},
          {'event':'started','scenario_id':'x','policy_id':'K1','utc':'2026-10-18T00:00:01+00:00'}]
    (batch/'ATTEMPTS.jsonl').write_text(''.join(json.dumps(x)+'\n' for x in rows),encoding='utf8')
    count=r.attempt_accounting()
    assert count['registered_pairs']==3 and count['actual_started_attempts']==2
    assert count['terminal_attempts']==1 and count['started_without_terminal_record']==1
    assert count['terminal_status_counts']=={'solver_error':1}


def test_freeze_refuses_source_change_during_protected_baseline_check(tmp_path,monkeypatch):
    f=api('freeze');r=api();g=api('gate')
    monkeypatch.setattr(f,'B',tmp_path)
    (tmp_path/'logs').mkdir();(tmp_path/'test_gates').mkdir();(tmp_path/'baseline').mkdir()
    source={'x':'a'}
    gate=dict(passed=True,exit_code=0,source_before=source,source_after=source,
        test_files_sha256={},test_files_after_sha256={})
    (tmp_path/'logs/check.command.json').write_text(json.dumps({'exit_code':0}),encoding='utf8')
    (tmp_path/'test_gates/check.json').write_text(json.dumps(gate),encoding='utf8')
    (tmp_path/'baseline/initial.json').write_text(json.dumps({'files':{}}),encoding='utf8')
    (tmp_path/'M19_MODEL_CONFIG.json').write_text('{}',encoding='utf8')
    bindings=iter([source,{'x':'changed'}])
    monkeypatch.setattr(r,'source_binding',lambda:next(bindings))
    monkeypatch.setattr(r,'model_contract',lambda model,config:({},config))
    monkeypatch.setattr(g,'test_binding',lambda:{})
    monkeypatch.setattr(sys,'argv',['freeze','--name','synthetic_candidate','--tests','check','--main'])
    with pytest.raises(ValueError,match='[Ss]ource|[Tt]est'):
        f.main()
    assert not (tmp_path/'synthetic_candidate.json').exists()


def test_runtime_emission_registry_is_in_source_and_snapshot_binding():
    r=api();path=r.RUN/'references/EMISSIONS_FACTOR_REGISTRY.json'
    assert r.source_binding()[path.relative_to(r.ROOT).as_posix()]==r.sha(path)


def test_evaluation_contract_forces_real_emission_cases_and_nonobjective_scope():
    f=api('freeze');r=api()
    registry=r.read(r.RUN/'references/EMISSIONS_FACTOR_REGISTRY.json')
    contract=f.resolved_evaluation_contract({'emissions':{'is_dispatch_objective':True}})
    assert contract['emissions']['main_case_id']==registry['main_case_id']
    assert contract['emissions']['case_canonical_sha256']==registry['case_canonical_sha256']
    assert contract['emissions']['is_dispatch_objective'] is False
    assert contract['emissions']['registry_sha256']==r.sha(r.RUN/'references/EMISSIONS_FACTOR_REGISTRY.json')


def test_development_subset_keeps_evaluation_role_and_unfrozen_status():
    c=api('campaign');p=c.subset_proposal(c.proposal('key'),[9,10])
    assert p['evaluation_role']=='development'
    assert len(p['scenarios'])==2
    assert all(not s['requires_policy_freeze'] for s in p['scenarios'])
