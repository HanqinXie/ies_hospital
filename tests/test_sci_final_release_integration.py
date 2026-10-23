"""Metadata-only integration fixtures. No real sealed input values or outcomes."""
from pathlib import Path
from datetime import datetime, timezone
from types import SimpleNamespace
import copy
import json
import shutil
import sys

import pytest

STAGE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(STAGE), str(STAGE / 'src')]
from tools.sci_engineering import design, runner, release


def fixture(tmp_path, *, normalized=True, aliases=('R','M','F','C','A'), configs=None,history_binding=True):
    root=tmp_path/'workspace';(root/'src').mkdir(parents=True)
    (root/'src/core.py').write_text('frozen=1\n',encoding='utf8')
    runner.write_new(root/release.ORIGINAL_AUTHORITY,dict(p2b_time_blocks=[
        dict(time_block_id='sealed_'+str(d),reserved_only=True) for d in design.SEALED_DAYS]))
    ep=tmp_path/'EXPOSURE.json';runner.write_new(ep,design.scan_historical_support(root))
    specs=[dict(policy_id=a+'_fixture',kind='sci_engineering',display_alias=a,config={}) for a in aliases]
    configs=configs if configs is not None else dict(H0={})
    selection=dict(selection_finalized=True,development_completed=True,
        selected_policy_ids=[p['policy_id'] for p in specs],selected_configuration_ids=list(configs),
        finalized_utc=datetime.now(timezone.utc).isoformat())
    if history_binding:
        selection['historical_exposure_authority']=dict(canonical_root=str(root.resolve()),
            registry_path=str(ep.resolve()),registry_sha256=runner.sha(ep))
    sp=tmp_path/'selection.json';runner.write_new(sp,selection)
    fp=tmp_path/'FREEZE.json';runner.create_freeze(root,fp,specs,configs,
        dict(max_actual_attempts=100,max_experiment_wall_hours=1),selection_record=selection)
    scenes=[dict(id='final_'+str(d),source_block_id='final_block_'+str(d),stage=1,
        start_hour=d*24+4,duration_steps=4,step_minutes=15.,canonical_class='L1',
        role='final_blind',is_final_blind=True,selection_allowed=False,fit_allowed=False,
        requires_policy_freeze=True) for d in design.SEALED_DAYS]
    panel=dict(model_id=design.MODEL_ID,phase='final_blind',scenarios=scenes)
    if normalized:panel=runner.normalize_panel(panel)
    pp=tmp_path/'PANEL.json';runner.write_new(pp,panel)
    proof=tmp_path/'SYNTHETIC_PILOT_PROOF.json'
    runner.write_new(proof,dict(completed=1,failed=0,pending=0,pilot_qualified=True,synthetic_fixture=True))
    return SimpleNamespace(root=root,fp=fp,sp=sp,pp=pp,ep=ep,specs=specs,configs=configs,
        panel=panel,directory=tmp_path/'final_registered',proof=proof)


def grant_and_register(f):
    grant=release.issue_grant(f.root,f.fp,f.sp,f.pp,f.ep)
    plan=runner.register_campaign(f.root,f.directory,f.panel,f.specs,f.configs,f.fp,
        phase='final_blind',raw_root=f.directory.parent/'raw',pilot_proof=f.proof,
        final_release_grant=grant)
    return grant,plan


def test_normalization_is_idempotent_and_unnormalized_panel_cannot_be_granted(tmp_path):
    f=fixture(tmp_path,normalized=False)
    with pytest.raises(ValueError,match='normaliz'):
        release.issue_grant(f.root,f.fp,f.sp,f.pp,f.ep)
    normalized=runner.normalize_panel(f.panel)
    assert runner.normalize_panel(normalized)==normalized
    assert normalized['scenarios'][0]['id']==f.panel['scenarios'][0]['id']


@pytest.mark.parametrize('kwargs',[dict(aliases=('R','M','F','C')),
    dict(configs=dict(H0={},H1={})),dict(configs={'H0':{'sci_engineering':{'ups':{'capacity_kWh':5}}}})])
def test_final_freeze_is_exactly_five_main_aliases_and_h0_without_hardware(tmp_path,kwargs):
    f=fixture(tmp_path,**kwargs)
    with pytest.raises(ValueError,match='five|H0|hardware'):
        release.issue_grant(f.root,f.fp,f.sp,f.pp,f.ep)


def test_final_registration_binds_exact_normalized_panel_once(tmp_path):
    f=fixture(tmp_path);grant,plan=grant_and_register(f)
    assert plan['panel']==f.panel
    assert plan['final_release_grant_signature']==grant['signature']
    receipt=runner.read(f.root/release.EXECUTION_AUTHORITY)
    assert 'receipt' not in json.dumps(runner.read(f.fp)).lower()
    assert release.validate_input_release(f.root,sorted(design.allowed_hours(f.panel)),design.SEALED_DAYS,receipt)
    with pytest.raises(FileExistsError):
        runner.register_campaign(f.root,f.directory.parent/'other',f.panel,f.specs,f.configs,f.fp,
            phase='final_blind',raw_root=f.directory.parent/'raw2',pilot_proof=f.proof,
            final_release_grant=grant)
    assert not (f.directory.parent/'other').exists()


def test_all_other_phases_and_missing_grant_keep_sealed_rejected(tmp_path):
    f=fixture(tmp_path);grant=release.issue_grant(f.root,f.fp,f.sp,f.pp,f.ep)
    for phase in ('pilot','development','formal','mechanism','boundary','hardware','sensitivity','reproduction'):
        with pytest.raises(ValueError,match='sealed|final|release'):
            runner.register_campaign(f.root,f.directory.parent/phase,f.panel,f.specs,f.configs,f.fp,
                phase=phase,raw_root=f.directory.parent/'raw',pilot_proof=f.proof,final_release_grant=grant)
    with pytest.raises(ValueError,match='grant|release'):
        runner.register_campaign(f.root,f.directory,f.panel,f.specs,f.configs,f.fp,
            phase='final_blind',raw_root=f.directory.parent/'raw',pilot_proof=f.proof)


def test_final_run_passes_runtime_receipt_and_retains_failed_attempt(tmp_path):
    f=fixture(tmp_path);grant,plan=grant_and_register(f);received=[]
    def env_factory(root,**kwargs):
        received.append(kwargs)
        assert 'sci_final_release_receipt' not in kwargs['model_config']
        release.validate_input_release(root,sorted(kwargs['allowed_hours']),kwargs['sealed_days'],
            kwargs['sci_final_release_receipt'])
        return SimpleNamespace()
    def fail(*args):raise RuntimeError('synthetic final failure remains visible')
    result=runner.run_registered(f.root,f.directory,env_factory=env_factory,
        policy_factory=lambda value:value,episode_fn=fail,max_pairs=1)
    assert result['failed']==1 and result['pending']==59 and len(received)==1
    assert result['final_blind_release_signature']==received[0]['sci_final_release_receipt']['signature']
    records=[runner.read(path) for path in f.directory.glob('pairs/*/attempt_*/RESULT.json')]
    assert len(records)==1 and records[0]['status']=='failed_unknown'
    assert records[0]['final_blind_release_signature']==result['final_blind_release_signature']
    assert 'synthetic final failure' in records[0]['error']['message']


def test_drift_and_wrong_execution_directory_reject_before_env(tmp_path):
    f=fixture(tmp_path);grant,plan=grant_and_register(f)
    copied=f.directory.parent/'copied_registration';shutil.copytree(f.directory,copied)
    with pytest.raises(ValueError,match='directory'):
        runner.run_registered(f.root,copied,env_factory=lambda *a,**k:pytest.fail('must not access inputs'))
    assert not (copied/'ATTEMPTS.jsonl').exists()
    (f.root/'src/core.py').write_text('frozen=2\n',encoding='utf8')
    with pytest.raises(ValueError,match='drift'):
        runner.run_registered(f.root,f.directory,env_factory=lambda *a,**k:pytest.fail('must not access inputs'))
    assert not (f.directory/'ATTEMPTS.jsonl').exists()


def test_authentic_fixture_receipt_reaches_input_stub_only_after_full_validation(tmp_path,monkeypatch):
    from tools.autonomous_dispatch import selective_data
    f=fixture(tmp_path);grant,plan=grant_and_register(f)
    receipt=runner.read(f.root/release.EXECUTION_AUTHORITY)
    class StopBeforeNumericRead(Exception):pass
    entered=[]
    def stop(path,**kwargs):entered.append(path);raise StopBeforeNumericRead
    monkeypatch.setattr(selective_data,'_identity',stop)
    with pytest.raises(StopBeforeNumericRead):
        selective_data.selective_inputs(f.root,sorted(design.allowed_hours(f.panel)),
            sealed_days=design.SEALED_DAYS,release_receipt=receipt)
    assert len(entered)==1


def test_selective_inputs_none_and_fake_receipt_fail_before_csv(tmp_path,monkeypatch):
    from tools.autonomous_dispatch import selective_data
    def forbidden(*a,**k):pytest.fail('Unauthorized numeric CSV access')
    monkeypatch.setattr(selective_data,'_identity',forbidden)
    for receipt in (None,dict(approved=True)):
        with pytest.raises(ValueError,match='sealed|release|receipt'):
            selective_data.selective_inputs(tmp_path,[25],sealed_days=[1],release_receipt=receipt)


def test_old_initializer_forwards_only_explicit_runtime_envelope(tmp_path,monkeypatch):
    from tools.autonomous_dispatch import environment as legacy,selective_data
    received=[]
    class StopBeforePhysicalLoad(Exception):pass
    def capture(*a,**k):received.append(k.get('release_receipt'));raise StopBeforePhysicalLoad
    monkeypatch.setattr(selective_data,'selective_inputs',capture)
    for envelope in (None,dict(metadata_fixture=True)):
        obj=legacy.SelectiveHospitalIESEnv.__new__(legacy.SelectiveHospitalIESEnv)
        if envelope is not None:obj.sci_final_release_receipt=envelope
        with pytest.raises(StopBeforePhysicalLoad):
            legacy.SelectiveHospitalIESEnv.__init__(obj,tmp_path,allowed_hours=[1],sealed_days=design.SEALED_DAYS)
    assert received==[None,dict(metadata_fixture=True)]


def test_m21_initializer_places_envelope_before_inherited_input_loading(tmp_path,monkeypatch):
    from tools.sci_engineering import environment as envmod
    seen=[]
    class StopBeforePhysicalLoad(Exception):pass
    def inherited(self,*a,**k):
        seen.append(copy.deepcopy(getattr(self,'sci_final_release_receipt',None)))
        assert 'sci_final_release_receipt' not in k.get('model_config',{})
        raise StopBeforePhysicalLoad
    monkeypatch.setattr(envmod,'model_contract',lambda *a,**k:({},dict(sci_engineering={})))
    monkeypatch.setattr(envmod.base.OperatingHospitalIESEnv,'__init__',inherited)
    receipt=dict(metadata_fixture=True)
    with pytest.raises(StopBeforePhysicalLoad):
        envmod.OperatingHospitalIESEnv(tmp_path,allowed_hours=[1],sealed_days=design.SEALED_DAYS,
            sci_final_release_receipt=receipt)
    assert seen==[receipt]


def test_m21_reset_only_exact_registered_scene_and_complete_hours(tmp_path,monkeypatch):
    from tools.sci_engineering import environment as envmod
    f=fixture(tmp_path);grant,plan=grant_and_register(f)
    receipt=runner.read(f.root/release.EXECUTION_AUTHORITY);scene=f.panel['scenarios'][0]
    obj=envmod.OperatingHospitalIESEnv.__new__(envmod.OperatingHospitalIESEnv)
    obj.root=f.root;obj.allowed_hours=frozenset(design.allowed_hours(dict(scenarios=[scene])))
    obj.sealed_days=frozenset(design.SEALED_DAYS);obj.sci_final_release_receipt=receipt
    obj.default_diesel_initial_energy_kWh=10.;obj.config={}
    calls=[]
    monkeypatch.setattr(envmod,'isolated',lambda *a,**k:lambda env,**kwargs:calls.append(kwargs) or 'fixture-reset')
    assert obj.reset(scenario=scene)=='fixture-reset'
    assert obj.sealed_days==frozenset(design.SEALED_DAYS) and len(calls)==1
    changed=copy.deepcopy(scene);changed['id']='unregistered_same_hours'
    with pytest.raises(ValueError,match='registered|scene'):obj.reset(scenario=changed)
    obj.allowed_hours=frozenset([scene['start_hour']])
    with pytest.raises(ValueError,match='support|hours'):obj.reset(scenario=scene)
    obj.allowed_hours=frozenset(design.allowed_hours(dict(scenarios=[scene])))
    obj.sci_final_release_receipt=None
    with pytest.raises(ValueError,match='Sealed|sealed'):obj.reset(scenario=scene)
    assert len(calls)==1


def test_cli_normalize_grant_register_complete_metadata_route(tmp_path,monkeypatch):
    from scripts import sci_campaign as cli
    f=fixture(tmp_path,normalized=False);normalized_path=tmp_path/'NORMALIZED_PANEL.json'
    monkeypatch.setattr(cli,'ROOT',f.root)
    assert cli.main(['normalize-panel','--panel',str(f.pp),'--output',str(normalized_path)])==0
    assert cli.main(['grant-final','--freeze',str(f.fp),'--selection-record',str(f.sp),
        '--panel',str(normalized_path),'--exposure-registry',str(f.ep)])==0
    assert cli.main(['register','--panel',str(normalized_path),'--freeze',str(f.fp),
        '--directory',str(f.directory),'--raw-root',str(tmp_path/'raw'),'--phase','final_blind',
        '--pilot-proof',str(f.proof),'--final-release-grant',str(f.root/release.AUTHORITY)])==0
    assert runner.read(f.directory/'PLAN.json')['phase']=='final_blind'
    assert (f.root/release.EXECUTION_AUTHORITY).is_file()
    assert not (f.directory/'ATTEMPTS.jsonl').exists()


def test_grant_requires_history_authority_in_the_closed_frozen_selection(tmp_path):
    f=fixture(tmp_path,history_binding=False)
    with pytest.raises(ValueError,match='historical|registry|authority'):
        release.issue_grant(f.root,f.fp,f.sp,f.pp,f.ep)
    assert not (f.root/release.AUTHORITY).exists()


def test_unrelated_empty_fixture_history_cannot_replace_bound_actual_history(tmp_path):
    f=fixture(tmp_path);scene=f.panel['scenarios'][0]
    runner.write_new(f.root/'outputs/known_actual/manifest.json',dict(scenarios=[scene]))
    (f.root/'outputs/known_actual/ATTEMPTS.jsonl').write_text(
        json.dumps(dict(event='started',scenario_id=scene['id']))+'\n',encoding='utf8')
    unrelated=tmp_path/'unrelated_empty_history';unrelated.mkdir()
    wrong=tmp_path/'UNRELATED_EXPOSURE.json'
    runner.write_new(wrong,design.scan_historical_support(unrelated))
    with pytest.raises(ValueError,match='historical|registry|authority'):
        release.issue_grant(f.root,f.fp,f.sp,f.pp,wrong)
    assert not (f.root/release.AUTHORITY).exists()


def test_bound_registry_content_cannot_rewrite_its_historical_root(tmp_path):
    f=fixture(tmp_path);unrelated=tmp_path/'unrelated_history';unrelated.mkdir()
    value=runner.read(f.ep);value['root']=str(unrelated.resolve());value=runner._signed(value)
    f.ep.write_text(json.dumps(value),encoding='utf8')
    with pytest.raises(ValueError,match='historical|registry|authority'):
        release.issue_grant(f.root,f.fp,f.sp,f.pp,f.ep)
    assert not (f.root/release.AUTHORITY).exists()


def test_registry_root_must_equal_the_frozen_canonical_history_authority(tmp_path):
    f=fixture(tmp_path);unrelated=tmp_path/'unrelated_history';unrelated.mkdir()
    selection=runner.read(f.sp)
    selection['historical_exposure_authority']['canonical_root']=str(unrelated.resolve())
    f.sp.write_text(json.dumps(selection),encoding='utf8')
    freeze=runner.read(f.fp);freeze['selection_record']=selection
    f.fp.write_text(json.dumps(runner._signed(freeze)),encoding='utf8')
    with pytest.raises(ValueError,match='historical|registry|authority'):
        release.issue_grant(f.root,f.fp,f.sp,f.pp,f.ep)
    assert not (f.root/release.AUTHORITY).exists()
