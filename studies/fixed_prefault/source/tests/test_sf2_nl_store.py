"""Transport-only NL CAS/index/session tests; no scientific execution."""
from copy import deepcopy
from pathlib import Path
import gzip,hashlib,importlib,json
import pytest
ROOT=Path(__file__).resolve().parents[1]

def api():
    assert (ROOT/'tools/single_fault_fuel_20261102/nl_store.py').is_file(), 'F3 NL transport is missing'
    return importlib.import_module('tools.single_fault_fuel_20261102.nl_store')
def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode('utf8')
def digest(value):return hashlib.sha256(canonical(value)).hexdigest()
def domain(sequence=1):
    body=dict(schema='hospital_ies.sf2.nonlinear_domain.v1',source_context={'scope':'synthetic_transport'},
        nonlinear_graph={},original={},native={},objective=[],objective_constant=0.,options={},variables=[],rows=[],locks=[])
    return dict(body,domain_sha256=digest(body),scope_sequence=sequence)
def result(reference):
    body=dict(schema='hospital_ies.sf2.nonlinear_result.v1',scope_sequence=reference['scope_sequence'],domain_reference=deepcopy(reference),
        source_context_sha256='a'*64,status=0,raw_x=[],native_fun=0.,mip_dual_bound=0.,mip_gap=0.,attempts=[])
    return dict(body,result_sha256=digest(body))
def create(tmp_path):
    module=api();producer=tmp_path/'producer';producer.mkdir()
    return module.NonlinearStore(producer,bindings={'git_commit':'synthetic','source':{'a':'a'*64},'inputs':{'b':'b'*64}},
        case_id='synthetic',physical_model_spec={'physical_model_id':'SF2'},storage_model_spec={'physical_model_id':'SF1'}),producer
def session(refs,status='closed_completed'):
    return dict(schema='hospital_ies.sf2.nonlinear_evidence_session.v1',status=status,call_count=len(refs),
        completed_call_count=sum(r is not None for d,r in refs),records=[dict(scope_sequence=i+1,domain_reference=d,result_reference=r) for i,(d,r) in enumerate(refs)],error=None)

def test_transport_uses_semantic_cas_exact_index_and_source_bound_session(tmp_path):
    store,producer=create(tmp_path);payload=domain();before=deepcopy(payload)
    d=store(payload);r=store(result(d));evidence=store.finish(session([(d,r)]))
    assert payload==before and d['record_id']=='nl_00000001'
    assert evidence['required'] is True and evidence['call_count']==evidence['completed_call_count']==1
    assert evidence['index']['event_count']==2
    for ref,key in ((d,'domain_sha256'),(r,'result_sha256')):
        path=producer/ref['artifacts'][0]['path'];raw=path.read_bytes()
        assert hashlib.sha256(raw).hexdigest()==ref['artifacts'][0]['sha256'] and raw[4:8]==b'\0'*4
        body=json.loads(gzip.decompress(raw));assert digest(body)==ref[key]
        if key=='domain_sha256':assert 'scope_sequence' not in body and 'domain_sha256' not in body
        else:assert body['domain_reference']==d and body['scope_sequence']==1
    saved=json.loads((producer/evidence['session']['path']).read_text('utf8'))
    assert saved['source_identity']['bindings']['git_commit']=='synthetic'
    assert saved['case_id']=='synthetic' and saved['physical_model_spec']['physical_model_id']=='SF2'
    previous=None
    for line in (producer/evidence['index']['path']).read_text('utf8').splitlines():
        event=json.loads(line);assert event['previous_event_sha256']==previous
        previous=event.pop('event_sha256');assert digest(event)==previous
    assert previous==evidence['index']['last_event_sha256']

@pytest.mark.parametrize('change',['digest','sequence','schema','result_before_domain'])
def test_bad_transport_input_fails_before_false_completion(tmp_path,change):
    store,producer=create(tmp_path);value=domain()
    if change=='digest':value['domain_sha256']='0'*64
    elif change=='sequence':value['scope_sequence']=True
    elif change=='schema':value['schema']='unreviewed'
    else:value=result({'scope_sequence':1,'record_id':'unknown','domain_sha256':'0'*64})
    with pytest.raises(ValueError):store(value)

def test_repeated_semantic_domain_deduplicates_without_reusing_call_identity(tmp_path):
    store,producer=create(tmp_path);d1=store(domain(1));r1=store(result(d1));d2=store(domain(2));r2=store(result(d2))
    assert d1['artifacts']==d2['artifacts'] and d1['record_id']!=d2['record_id']
    assert store.finish(session([(d1,r1),(d2,r2)]))['index']['event_count']==4

def test_missing_completion_and_changed_cas_cannot_finish_successfully(tmp_path):
    store,producer=create(tmp_path);d=store(domain());r=store(result(d))
    (producer/d['artifacts'][0]['path']).write_bytes(b'changed')
    with pytest.raises(ValueError):store.finish(session([(d,r)]))
    assert (producer/'evidence/nonlinear/SESSION.json').is_file()

def test_failed_scope_retains_partial_domain_and_failed_session(tmp_path):
    store,producer=create(tmp_path);d=store(domain())
    evidence=store.finish(session([(d,None)],'closed_failed'))
    saved=json.loads((producer/evidence['session']['path']).read_text('utf8'))
    assert saved['core_session']['status']=='closed_failed' and saved['index']['event_count']==1
