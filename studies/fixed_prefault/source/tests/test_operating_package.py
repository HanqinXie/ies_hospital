"""Tiny v15 package fixtures only: no campaign build or physical replay."""
import importlib
import json
from pathlib import Path
import sys
import zipfile

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]


def api():
    try:return importlib.import_module('tools.operating_reporting.package')
    except ModuleNotFoundError as exc:
        pytest.fail('Missing minimal v15 package adapter: '+str(exc),pytrace=False)


def put(root,name,text):
    path=root/name;path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(text,encoding='utf8');return path


def fixture(a,root):
    relative='outputs/v15_fixture';run=root/relative
    for name in ('src/hospital_ies/__init__.py','tools/operating_response/__init__.py',
                 'tools/operating_reporting/__init__.py','tools/engineering_reporting/__init__.py'):
        put(root,name,'')
    put(root,'tools/operating_response/runner.py','from tools.shared import helper\n')
    put(root,'tools/operating_reporting/package.py','from tools.engineering_reporting.package import build\n')
    put(root,'tools/engineering_reporting/package.py','def build(): pass\n')
    helper=put(root,'tools/shared.py','helper=1\n')
    put(root,'tools/unrelated.py','# not in import closure\n')
    put(root,'requirements.txt','numpy>=1.24\n')
    put(root,'docs/v15/SOURCES_AND_ASSUMPTIONS.md','# fixture sources\n')
    put(root,'docs/v15/MODEL_MIGRATION.md','# fixture migration\n')
    put(root,'Hospital_IES_V15_Engineering_Operation_Plan/CODEX_TASK.md','# fixture task\n')
    put(root,'tests/test_operating_tiny.py','def test_tiny(): assert True\n')
    for name in a.PHYSICAL_INPUTS:
        put(root,name,'{}\n' if name.endswith('.json') else 'tiny input bytes\n')
    for name in a.METADATA_DEPENDENCIES:put(root,name,'{}\n')
    batch=run/'experiments/one'
    specs=[{'policy_id':p,'kind':'fixture','config':{}} for p in
           ('fixed_standby_constant_v12b','endurance_restore_p0_ready_v14r6','readiness_handoff_K2_v15')]
    plan={'phase':'development','model_id':'M15_local_response','model_config':{'gt_dynamics':{},'diesel_local_response':{}},
        'policy_specs':specs,'scenario_ids':['s'],'registered_pairs':3,'source_sha256':{'tools/shared.py':a.sha(helper)}}
    put(root,f'{relative}/experiments/one/plan.json',json.dumps(plan))
    put(root,f'{relative}/experiments/one/manifest.json',json.dumps({'scenarios':[{'id':'s','analysis_class':'L4b'}]}))
    put(root,f'{relative}/experiments/one/BATCH_RESULT.json','{}')
    records=[];selected=[];paths=[]
    for i,spec in enumerate(specs):
        policy=spec['policy_id'];raw=batch/f'pairs/s__{policy}/trajectory.json.gz'
        raw.parent.mkdir(parents=True,exist_ok=True);raw.write_bytes(b'compressed fixture '+str(i).encode())
        row={'scenario_id':'s','policy_id':policy,'model_id':'M15_local_response','raw_path':str(raw),
            'raw_sha256':a.sha(raw),'status':'completed' if i==0 else 'local_response_domain_exceeded','analysis':{}}
        put(root,f'{relative}/experiments/one/pairs/s__{policy}/RESULT.json',json.dumps(row))
        records.append(row);paths.append(raw)
        if i!=1:selected.append({'batch':'one','scenario_id':'s','policy_id':policy,'reason':'explicit matched fixture'})
    put(root,f'{relative}/experiments/one/analysis/RESULTS.json',json.dumps({'records':records}))
    proposal=put(root,f'{relative}/design/NORMAL_PROPOSAL.json','{"scenarios":[{"id":"s"}]}')
    report=put(root,f'{relative}/OPERATING_RESPONSE_MODEL_REPORT.md','# actual fixture report\n')
    selection=put(root,f'{relative}/review/LIGHT_PACKAGE_RAW_SELECTION.json',json.dumps({
        'schema':a.SELECTION_SCHEMA,'status':'final','selected_by':'root','all_batches_stopped':True,
        'raw_examples':selected,'replay_proposal':proposal.relative_to(root).as_posix()}))
    return run,selection,report,paths


def test_adapter_keeps_v15_runtime_and_known_counterexample_metadata(tmp_path):
    a=api();run,selection,report,_=fixture(a,tmp_path)
    prepared=a.prepare(tmp_path,run,selection,reports=[report]);names=set(prepared['entries'])
    assert set(a.PHYSICAL_INPUTS)<=names
    assert set(a.METADATA_DEPENDENCIES)<=names
    assert 'tools/operating_response/runner.py' in names
    assert 'tools/shared.py' in names and 'tools/unrelated.py' not in names
    assert 'docs/v15/MODEL_MIGRATION.md' in names
    assert 'tests/test_operating_tiny.py' in names
    assert 'Hospital_IES_V15_Engineering_Operation_Plan/CODEX_TASK.md' in names
    assert any('v14_engineering_model' in p and p.endswith('/manifest.json') for p in a.METADATA_DEPENDENCIES)
    assert prepared['registered_pairs']==3


def test_unselected_raw_never_read_and_all_failed_unknown_rows_retained(tmp_path,monkeypatch):
    a=api();run,selection,report,raws=fixture(a,tmp_path)
    original=Path.open
    def guarded(path,*args,**kwargs):
        if path==raws[1]:pytest.fail('Unselected compressed raw was read')
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'open',guarded)
    prepared=a.prepare(tmp_path,run,selection,reports=[report]);entries=prepared['entries']
    index=json.loads(entries['ALL_REGISTERED_PAIR_INDEX.json']['source'])
    assert len(index)==3
    assert sum(row['status']=='local_response_domain_exceeded' for row in index)==2
    external=json.loads(entries['EXTERNAL_RAW_INDEX.json']['source'])
    assert len(external)==3 and sum(row['included'] for row in external)==2
    assert len([p for p in entries if p.endswith('trajectory.json.gz')])==2


def test_tiny_build_reuses_mature_integrity_verifier_and_fresh_canonical_budget(tmp_path):
    a=api();run,selection,report,_=fixture(a,tmp_path)
    archive=tmp_path/'delivery/fixture.zip'
    receipt=a.build(tmp_path,run,selection,archive,reports=[report])
    assert receipt['verified'] and receipt['physical_reproduction_performed'] is False
    assert a.verify(archive)['verified']
    with zipfile.ZipFile(archive) as bundle:
        names=bundle.namelist();text=bundle.read('README_REPLAY.md').decode('utf8')
        assert not any(p.startswith('outputs/v15_fixture/experiments/') for p in names)
        assert any(p.startswith('evidence/outputs/v15_fixture/experiments/') for p in names)
        assert 'python -B -m tools.operating_response.runner register' in text
        assert 'tools.operating_response.runner run' in text
        assert '--model M15_local_response' in text and '--model-config' in text
        assert '--scenario-id "s"' in text and '--policies F,E,K2' in text
        assert 'gurobi' in text.lower() and 'license' in text.lower()
        assert 'not_verified' in text
        assert json.loads(bundle.read('PACKAGE_INDEX.json'))['schema']==a.SCHEMA
    with pytest.raises(FileExistsError):a.build(tmp_path,run,selection,archive,reports=[report])


def test_running_or_draft_selection_cannot_start_packaging(tmp_path):
    a=api();run,selection,report,_=fixture(a,tmp_path)
    value=json.loads(selection.read_text());value['all_batches_stopped']=False
    selection.write_text(json.dumps(value))
    with pytest.raises(ValueError,match='final|stopped'):a.prepare(tmp_path,run,selection,reports=[report])


def test_missing_old_manifest_is_an_explicit_replay_dependency_failure(tmp_path):
    a=api();run,selection,report,_=fixture(a,tmp_path)
    target=next(p for p in a.METADATA_DEPENDENCIES if 'v14_engineering_model' in p)
    (tmp_path/target).unlink()
    with pytest.raises(FileNotFoundError,match='metadata|dependency|replay'):
        a.prepare(tmp_path,run,selection,reports=[report])


def test_selected_path_escape_or_unmatched_selection_is_rejected(tmp_path):
    a=api();run,selection,report,_=fixture(a,tmp_path)
    value=json.loads(selection.read_text());value['raw_examples'][0]['batch']='../outside'
    selection.write_text(json.dumps(value))
    with pytest.raises(ValueError):a.prepare(tmp_path,run,selection,reports=[report])
    value['raw_examples'][0]['batch']='one';value['raw_examples']=value['raw_examples'][:1]
    selection.write_text(json.dumps(value))
    with pytest.raises(ValueError,match='matched'):a.prepare(tmp_path,run,selection,reports=[report])


def test_explicit_replay_proposal_must_contain_selected_specification(tmp_path):
    a=api();run,selection,report,_=fixture(a,tmp_path)
    put(tmp_path,'outputs/v15_fixture/design/NORMAL_PROPOSAL.json','{"scenarios":[{"id":"other"}]}')
    with pytest.raises(ValueError,match='proposal|scenario'):a.prepare(tmp_path,run,selection,reports=[report])


def test_sensitive_files_and_temp_or_large_old_outputs_are_not_collected(tmp_path):
    a=api();run,selection,report,_=fixture(a,tmp_path)
    for name in ('temp/fake.json','review/credentials.json','secrets/token.json','gurobi.lic','PAPER.md'):
        put(tmp_path,'outputs/v15_fixture/'+name,'sensitive or unrelated fixture')
    put(tmp_path,'outputs/older/huge.json','not needed')
    prepared=a.prepare(tmp_path,run,selection,reports=[report]);names=set(prepared['entries'])
    assert not any(any(x in p for x in ('/temp/','credentials.json','/secrets/','gurobi.lic','PAPER.md','older/huge')) for p in names)


def test_legacy_module_constants_are_not_mutated_by_new_build(tmp_path):
    from tools.engineering_reporting import package as old
    old_schema=old.SCHEMA
    a=api();run,selection,report,_=fixture(a,tmp_path)
    a.build(tmp_path,run,selection,tmp_path/'delivery/tiny.zip',reports=[report])
    assert old.SCHEMA==old_schema and old.SCHEMA!=a.SCHEMA


def test_cancelled_unstarted_registration_keeps_denominator_and_reason(tmp_path):
    a=api();run,selection,report,_=fixture(a,tmp_path)
    plan=json.loads((run/'experiments/one/plan.json').read_text())
    put(tmp_path,'outputs/v15_fixture/experiments/cancelled/plan.json',json.dumps(plan))
    put(tmp_path,'outputs/v15_fixture/experiments/cancelled/manifest.json','{"scenarios":[{"id":"s","analysis_class":"L4b"}]}')
    put(tmp_path,'outputs/v15_fixture/INITIAL_CANCELLATION.json',json.dumps({'batches':['cancelled'],
        'actual_attempts':0,'reason':'registered before source fix, never started'}))
    prepared=a.prepare(tmp_path,run,selection,reports=[report])
    rows=json.loads(prepared['entries']['ALL_REGISTERED_PAIR_INDEX.json']['source'])
    assert len(rows)==6 and sum(row['status']=='cancelled_before_start' for row in rows)==3
    assert all(row.get('cancellation_reason') for row in rows if row['status']=='cancelled_before_start')


def test_pair_result_is_preserved_when_batch_analysis_is_unavailable(tmp_path):
    a=api();run,selection,report,_=fixture(a,tmp_path)
    (run/'experiments/one/analysis/RESULTS.json').unlink()
    prepared=a.prepare(tmp_path,run,selection,reports=[report]);entries=prepared['entries']
    results=[p for p in entries if '/pairs/' in p and p.endswith('/RESULT.json')]
    assert len(results)==3
    index=json.loads(entries['ALL_REGISTERED_PAIR_INDEX.json']['source'])
    assert len(index)==3 and sum(row['status']=='local_response_domain_exceeded' for row in index)==2
