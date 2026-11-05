"""Offline annual endpoint/audit adapters; all numeric records are synthetic."""
from __future__ import annotations

import copy
import gzip
import hashlib
import importlib
import importlib.util
import inspect
import json
from pathlib import Path
import sys

import pytest

ROOT=Path(__file__).parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]


def api():
    assert (ROOT/'tools/annual_program/analysis.py').is_file(), 'Annual offline analysis adapter is missing'
    return importlib.import_module('tools.annual_program.analysis')


def registered(*,start=1,duration=8760*60):
    from tools.annual_program.registration import make_case
    return make_case('M_ready',start_hour=start,duration_minutes=duration)


@pytest.mark.parametrize('start,duration,observed,expected',[
    (1,8760*60,0.,[]),(1,8760*60,60.,[1]),
    (1,8760*60,60.25,[1,2]),(8760,60.,60.,[8760]),
    (8759,120.,120.,[8759,8760])])
def test_observed_source_hours_are_exact_half_open_support(start,duration,observed,expected):
    assert api().observed_source_hours(registered(start=start,duration=duration),observed)==expected


def test_complete_original_year_never_invents_hour8761():
    hours=api().observed_source_hours(registered(),8760*60.)
    assert hours==list(range(1,8761))


@pytest.mark.parametrize('observed',[-1.,float('nan'),float('inf'),True,61.])
def test_observed_source_hours_reject_invalid_or_unobserved_support(observed):
    with pytest.raises(ValueError):api().observed_source_hours(registered(start=8760,duration=60.),observed)


def test_annual_source_hours_require_unchanged_canonical_case():
    case=registered(start=8760,duration=60.);case['scenario']['start_hour']=8759
    with pytest.raises(ValueError,match='Canonical|canonical|identity'):
        api().observed_source_hours(case,60.)


def old_fixture():
    spec=importlib.util.spec_from_file_location('annual_legacy_analysis_fixture',ROOT/'tests/test_annual_readiness_analysis.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_native_analysis_prices_last_observed_hour_without_parsing_sealed_source_rows(tmp_path,monkeypatch):
    fixture=old_fixture()
    old_run,repo,_=fixture.fixture_run(tmp_path/'synthetic',[[fixture.segment(0.,60.)]],requested=60.)
    # Replace the temporary fixture's single synthetic label, never the actual
    # hospital annual CSV. The new producer gets its own consistent identity.
    hourly=repo/'inputs/loads/hospital_ies_demand_8760.csv'
    hourly.write_text('hour_of_year,timestamp\n8760,2001-01-01T01:00:00\n',encoding='utf8')
    bindings=json.loads((old_run/'BINDINGS.json').read_text(encoding='utf8'))
    bindings['inputs']['inputs/loads/hospital_ies_demand_8760.csv']=hashlib.sha256(hourly.read_bytes()).hexdigest()
    case=registered(start=8760,duration=60.);run=tmp_path/'annual';run.mkdir()
    for name,value in [('CASE.json',{'case':case}),('BINDINGS.json',bindings)]:
        (run/name).write_text(json.dumps(value),encoding='utf8')
    (run/'RESOLVED_CASE.json').write_bytes((old_run/'RESOLVED_CASE.json').read_bytes())
    from tools.sci_engineering.design import digest
    from tools.annual_program import MODEL_ID
    from tools.annual_readiness.journal import BlockJournal
    identity={'case_id':case['id'],'model_id':MODEL_ID,'git_commit':bindings['git_commit'],
        'case_sha256':digest(case),'source_sha256':digest(bindings['source']),
        'input_sha256':digest(bindings['inputs'])}
    with gzip.open(old_run/'evidence/blocks/block_000000.json.gz','rt',encoding='utf8') as stream:block=json.load(stream)
    block['segments'][0]['hour_of_year']=8760
    journal=BlockJournal(run/'evidence',identity=identity);journal.append(block)
    journal.close(status='completed',expected_end_minute=60.)
    (run/'RESULT.json').write_text(json.dumps(dict(identity,status='completed',
        observed_until_minute=60.,economics={'actual_fee_USD2001':None},source_and_inputs_unchanged=True)),encoding='utf8')
    native=importlib.import_module('tools.annual_readiness.analysis');seen=[]
    original=native.ledger._frozen_parameters
    def pricing(root,bound,payload):
        seen.append([r['hour_of_year'] for r in payload['segments']])
        return original(root,bound,payload)
    monkeypatch.setattr(native.ledger,'_frozen_parameters',pricing)
    api()  # Require the new helper before the opt-in native branch is invoked.
    result=native.analyze_run(run,repo=repo)
    assert seen==[[8760]]
    assert result['economics']['accounting_error'] is None


def mock_stream(tmp_path,monkeypatch,*,statuses=('passed','passed')):
    module=api();case=registered(duration=60.);run=tmp_path/'producer';run.mkdir()
    (run/'CASE.json').write_text(json.dumps({'case':case}),encoding='utf8')
    evidence=run/'evidence/blocks';evidence.mkdir(parents=True)
    records=[]
    for index,status in enumerate(statuses):
        name=f'blocks/block_{index:06d}.json.gz'
        (run/'evidence'/name).write_bytes(gzip.compress(json.dumps({'index':index}).encode(),mtime=0))
        records.append({'file':name,'sha256':'synthetic-mocked-witness-reader'})
    native={'schema':'native.fixture','audit_status':'unknown','failed_checks':[],
        'unknown_checks':['native.original_unknown'],'balances':{'steam_full_closure':'unknown'},
        'observation':{'observed_until_minute':60.,'requested_end_minute':60.,'complete':True},
        'provenance':{'producer_files_unchanged':True}}
    monkeypatch.setattr(module.native,'analyze_run',lambda *a,**k:copy.deepcopy(native))
    monkeypatch.setattr(module.journal,'verify_journal',lambda *a,**k:{'integrity_verified':True})
    context={'directory':run,'raw_files_sha256':records,'source_hashes':{'fixture':'not-a-real-primal-proof'},
        'observation':native['observation']}
    monkeypatch.setattr(module.steam,'prepare_steam_context',lambda *a,**k:context)
    visited=[];verified=[]
    def audit(ctx,block,*,block_record,use_actual_witness=False,steam_flow_only=False):
        index=block['index'];visited.append(index);status=statuses[index]
        result={'status':status,'reasons':[] if status=='passed' else ['missing_native_primal'],
            'segments':[{'status':status,'reasons':[] if status=='passed' else ['missing_native_primal']}],
            'summary':{'segment_count':1,'passed_segments':int(status=='passed'),
                'unknown_segments':int(status=='unknown'),'failed_segments':int(status=='failed'),
                'total_metered_waste_lb':5. if status=='passed' else None,
                'maximum_absolute_steam_residual_lbph':0. if status=='passed' else None}}
        if steam_flow_only:result['qualification_scope']='algebraic_steam_flow_only'
        return result
    monkeypatch.setattr(module.steam,'audit_steam_block',audit)
    monkeypatch.setattr(module.steam,'verify_steam_context',lambda ctx,full=True:verified.append(full) or True)
    return module,run,native,visited,verified


def test_streamed_steam_pass_does_not_erase_original_unknowns(tmp_path,monkeypatch):
    module,run,native,visited,verified=mock_stream(tmp_path,monkeypatch)
    result=module.analyze_run(run,repo=tmp_path/'synthetic-source')
    assert visited==[0,1] and verified[-1] is True
    assert result['independent_steam']['status']=='passed'
    assert result['independent_steam']['summary']['total_metered_waste_lb']==10.
    assert result['audit_status']=='unknown' and result['balances']==native['balances']
    assert 'native.original_unknown' in result['unknown_checks']
    assert result['annual_program_analysis']['observed_source_hours']==[1]
    assert result['independent_steam']['maximum_decoded_blocks']==1
    assert result['independent_steam']['full_raw_trajectory_retained'] is False


@pytest.mark.parametrize('opt_in',[False,True])
def test_new_actual_primal_reader_requires_explicit_analysis_opt_in(tmp_path,monkeypatch,opt_in):
    real=api();prepare_signature=inspect.signature(real.steam.prepare_steam_context)
    audit_signature=inspect.signature(real.steam.audit_steam_block)
    module,run,_,_,_=mock_stream(tmp_path,monkeypatch);seen=[]
    original=module.steam.prepare_steam_context
    original_audit=module.steam.audit_steam_block
    def prepare(*args,**kwargs):
        prepare_signature.bind(*args,**kwargs);return original(*args,**kwargs)
    def audit(*args,**kwargs):
        audit_signature.bind(*args,**kwargs)
        seen.append(kwargs.get('use_actual_witness',False));kwargs.pop('use_actual_witness',None)
        return original_audit(*args,**kwargs)
    monkeypatch.setattr(module.steam,'prepare_steam_context',prepare)
    monkeypatch.setattr(module.steam,'audit_steam_block',audit)
    result=module.analyze_run(run,repo=tmp_path/'synthetic-source',use_actual_witness=opt_in)
    assert seen==[opt_in,opt_in]
    assert result['annual_program_analysis']['use_actual_witness'] is opt_in


def test_missing_steam_segment_keeps_total_unknown_and_known_subtotal_separate(tmp_path,monkeypatch):
    module,run,_,visited,verified=mock_stream(tmp_path,monkeypatch,statuses=('passed','unknown'))
    result=module.analyze_run(run,repo=tmp_path/'synthetic-source')
    steam=result['independent_steam']
    assert steam['status']=='unknown' and steam['summary']['total_metered_waste_lb'] is None
    assert steam['summary']['known_metered_waste_lb']==5.
    assert steam['summary']['unknown_segments']==1 and visited==[0,1] and verified[-1]
    assert 'independent_steam.incomplete_native_proof' in result['unknown_checks']


@pytest.mark.parametrize('strict',[False,True])
def test_explicit_steam_flow_scope_reaches_real_block_api_and_preserves_native_unknowns(tmp_path,monkeypatch,strict):
    real=api();signature=inspect.signature(real.steam.audit_steam_block)
    module,run,_,_,_=mock_stream(tmp_path,monkeypatch);seen=[]
    original=module.steam.audit_steam_block
    def audit(*args,**kwargs):
        signature.bind(*args,**kwargs);seen.append(kwargs)
        return original(*args,**kwargs)
    monkeypatch.setattr(module.steam,'audit_steam_block',audit)
    result=module.analyze_run(run,repo=tmp_path/'synthetic-source',
        use_actual_witness=strict,steam_flow_only=True)
    assert len(seen)==2 and all(row['steam_flow_only'] is True for row in seen)
    assert all(row['use_actual_witness'] is strict for row in seen)
    assert result['audit_status']=='unknown' and 'native.original_unknown' in result['unknown_checks']
    steam=result['independent_steam']
    assert steam['qualification_scope']=='algebraic_steam_flow_only'
    assert steam['full_startup_context_validated'] is False and steam['electricity_validated'] is False
    assert steam['whole_system_validated'] is False


def test_contradictory_steam_evidence_cannot_return_overall_pass(tmp_path,monkeypatch):
    module,run,_,_,_=mock_stream(tmp_path,monkeypatch,statuses=('passed','failed'))
    result=module.analyze_run(run,repo=tmp_path/'synthetic-source')
    assert result['audit_status']=='failed'
    assert 'independent_steam.native_witness_failure' in result['failed_checks']


def test_postprocessor_identity_is_separate_from_producer_model_source(tmp_path,monkeypatch):
    module,run,_,_,_=mock_stream(tmp_path,monkeypatch)
    result=module.analyze_run(run,repo=tmp_path/'synthetic-source')
    sources=result['annual_program_analysis']['observer_source_sha256']
    assert sources[str(Path(module.__file__).resolve())]==hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
    assert result['annual_program_analysis']['simulation_rerun'] is False


def test_postprocessor_source_drift_prevents_an_accepted_report(tmp_path,monkeypatch):
    module,run,_,_,_=mock_stream(tmp_path,monkeypatch)
    calls=iter([{'observer':'a'*64},{'observer':'b'*64}])
    monkeypatch.setattr(module,'_observer_bindings',lambda:next(calls),raising=False)
    with pytest.raises(ValueError,match='[Oo]bserver.*changed|[Oo]bserver.*drift'):
        module.analyze_run(run,repo=tmp_path/'synthetic-source')


def test_analysis_cli_writes_only_a_fresh_detached_directory(tmp_path,monkeypatch):
    module,run,_,_,_=mock_stream(tmp_path,monkeypatch)
    before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in run.rglob('*') if p.is_file()}
    destination=tmp_path/'analysis'
    assert module.main(['--run',str(run),'--repo',str(tmp_path/'synthetic-source'),'--output',str(destination)])==0
    assert (destination/'ANALYSIS.json').is_file()
    with pytest.raises(ValueError):module.main(['--run',str(run),'--output',str(destination)])
    with pytest.raises(ValueError):module.main(['--run',str(run),'--output',str(run/'inside')])
    assert before=={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in run.rglob('*') if p.is_file()}
