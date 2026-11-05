"""White-box metadata fixtures only: no annual numeric CSV or sealed result exists."""
from pathlib import Path
from datetime import datetime,timezone
import importlib.util
import json
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from tools.sci_engineering import design,runner


def api():
    from tools.sci_engineering import release
    return release


def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value),encoding='utf8')


def fixture(tmp_path):
    r=api();root=tmp_path/'workspace';(root/'src').mkdir(parents=True)
    (root/'src/core.py').write_text('x=1\n',encoding='utf8')
    authority=root/'outputs/v43_p2b_20261013_run01/experiment/global_manifest.json'
    write(authority,dict(p2b_time_blocks=[dict(time_block_id='p2b_day_'+str(d),reserved_only=True) for d in design.SEALED_DAYS]))
    registry=design.scan_historical_support(root);ep=tmp_path/'EXPOSURE.json';write(ep,registry)
    specs=[dict(policy_id=a,display_alias=a,config={}) for a in ('R','M','F','C','A')];configs=dict(H0={})
    selection=dict(selection_finalized=True,development_completed=True,selected_policy_ids=['R','M','F','C','A'],
        selected_configuration_ids=['H0'],finalized_utc=datetime.now(timezone.utc).isoformat(),
        historical_exposure_authority=dict(canonical_root=str(root.resolve()),
            registry_path=str(ep.resolve()),registry_sha256=runner.sha(ep)))
    sp=tmp_path/'selection.json';write(sp,selection)
    fp=tmp_path/'FREEZE.json';runner.create_freeze(root,fp,specs,configs,
        dict(max_actual_attempts=100,max_experiment_wall_hours=2),selection_record=selection)
    scenes=[]
    for d in design.SEALED_DAYS:
        scenes.append(dict(id='sealed_'+str(d),start_hour=d*24+4,duration_steps=48,step_minutes=15.,
            stage=1,source_block_id='sealed_block_'+str(d),role='final_blind',is_final_blind=True,
            fit_allowed=False,selection_allowed=False,requires_policy_freeze=True))
    panel=runner.normalize_panel(dict(schema='test_metadata',model_id=design.MODEL_ID,phase='final_blind',scenarios=scenes))
    pp=tmp_path/'PANEL.json';write(pp,panel)
    return r,root,fp,sp,pp,ep


def bind(tmp_path,r,root,grant,freeze_path,panel_path,phase='final_blind'):
    freeze=runner.read(freeze_path);panel=runner.read(panel_path)
    manifest=runner._signed(dict(phase=phase,execution_model_id=design.MODEL_ID,
        freeze_signature=freeze['signature'],scenarios=panel['scenarios'],
        policy_specs_sha256=design.digest(freeze['policy_specs']),
        configuration_signatures={k:design.digest(v) for k,v in freeze['configurations'].items()},
        source_sha256=freeze['source_sha256']))
    mp=tmp_path/'MANIFEST.json';write(mp,manifest)
    plan=runner._signed(dict(phase=phase,model_id=design.MODEL_ID,panel=panel,
        freeze_signature=freeze['signature'],manifest_signature=manifest['signature'],
        policy_specs=freeze['policy_specs'],configurations=freeze['configurations']))
    planpath=tmp_path/'PLAN.json';write(planpath,plan)
    return r.bind_execution(root,grant,planpath,mp)


def test_one_final_grant_and_exact_execution_receipt_only(tmp_path):
    r,root,fp,sp,pp,ep=fixture(tmp_path)
    grant=r.issue_grant(root,fp,sp,pp,ep)
    with pytest.raises(FileExistsError):r.issue_grant(root,fp,sp,pp,ep)
    receipt=bind(tmp_path,r,root,grant,fp,pp)
    first=runner.read(pp)['scenarios'][0]
    hours=sorted(design.allowed_hours(dict(scenarios=[first])))
    valid=r.validate_input_release(root,hours,design.SEALED_DAYS,receipt)
    assert valid['phase']=='final_blind' and valid['released_hours']==hours
    assert valid['metadata_only_nonexposure_audit']['actual_sealed_support_overlap_count']==0
    with pytest.raises(ValueError,match='release|receipt'):
        r.validate_input_release(root,hours,design.SEALED_DAYS,dict(approved=True))


def test_development_cannot_bind_release(tmp_path):
    r,root,fp,sp,pp,ep=fixture(tmp_path)
    grant=r.issue_grant(root,fp,sp,pp,ep)
    with pytest.raises(ValueError,match='final_blind'):
        bind(tmp_path,r,root,grant,fp,pp,phase='development')


def test_unselected_policies_and_configurations_are_rejected(tmp_path):
    r,root,fp,sp,pp,ep=fixture(tmp_path)
    grant=r.issue_grant(root,fp,sp,pp,ep);freeze=runner.read(fp);panel=runner.read(pp)
    with pytest.raises(ValueError,match='policy'):
        r.validate_final_registration(root,grant,'final_blind',panel,freeze['policy_specs'][:1],freeze['configurations'])
    with pytest.raises(ValueError,match='configuration'):
        r.validate_final_registration(root,grant,'final_blind',panel,freeze['policy_specs'],dict(H1={}))


def test_staged_selective_input_fake_receipt_fails_before_any_csv_read(tmp_path,monkeypatch):
    release=api();monkeypatch.setitem(sys.modules,'tools.sci_engineering.release',release)
    path=ROOT/'tools/autonomous_dispatch/selective_data.py'
    spec=importlib.util.spec_from_file_location('isolated_selective_data_staged',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    def forbidden(*args,**kwargs):pytest.fail('Fake receipt attempted numeric input parsing')
    monkeypatch.setattr(module,'_identity',forbidden)
    with pytest.raises(ValueError,match='release|final'):
        module.selective_inputs(tmp_path,[25],sealed_days=[1],release_receipt=dict(approved=True))


def test_selection_must_be_finalized_before_grant(tmp_path):
    r,root,fp,sp,pp,ep=fixture(tmp_path)
    value=runner.read(sp);value['selection_finalized']=False;write(sp,value)
    with pytest.raises(ValueError,match='selection'):
        r.issue_grant(root,fp,sp,pp,ep)


def test_release_cannot_extend_hours_or_peek_partial_support(tmp_path):
    r,root,fp,sp,pp,ep=fixture(tmp_path)
    grant=r.issue_grant(root,fp,sp,pp,ep);receipt=bind(tmp_path,r,root,grant,fp,pp)
    scene=runner.read(pp)['scenarios'][0];hours=sorted(design.allowed_hours(dict(scenarios=[scene])))
    for bad in ([1],hours+[1],hours[:1]):
        with pytest.raises(ValueError,match='support|hours'):
            r.validate_input_release(root,bad,design.SEALED_DAYS,receipt)


def test_source_or_panel_change_after_grant_invalidates_receipt(tmp_path):
    r,root,fp,sp,pp,ep=fixture(tmp_path)
    grant=r.issue_grant(root,fp,sp,pp,ep);receipt=bind(tmp_path,r,root,grant,fp,pp)
    (root/'src/core.py').write_text('x=2\n',encoding='utf8')
    hours=sorted(design.allowed_hours(dict(scenarios=[runner.read(pp)['scenarios'][0]])))
    with pytest.raises(ValueError,match='drift'):
        r.validate_input_release(root,hours,design.SEALED_DAYS,receipt)


def test_started_historical_sealed_metadata_blocks_grant_without_reading_results(tmp_path):
    r,root,fp,sp,pp,ep=fixture(tmp_path)
    scene=runner.read(pp)['scenarios'][0]
    write(root/'outputs/old/manifest.json',dict(scenarios=[scene]))
    journal=root/'outputs/old/ATTEMPTS.jsonl'
    journal.write_text(json.dumps(dict(event='started',scenario_id=scene['id']))+'\n',encoding='utf8')
    with pytest.raises(ValueError,match='historical|sealed'):
        r.issue_grant(root,fp,sp,pp,ep)
