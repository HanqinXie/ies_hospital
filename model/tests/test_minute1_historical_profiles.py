"""Run fixed historical identities in the real, immutable 7fa native context.

These positives qualify that historical test context only. The current minute
source requires its separate admission and physical gates.
"""
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

import pytest
from conftest import historical_manifest as manifest,historical_path,historical_output_root

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
ROUTES=ROOT/'tests/minute1_historical_routes.json'


def test_default_entry_includes_all_minute_contract_modules_and_exact_route_hook(pytestconfig):
    expected={p.relative_to(ROOT).as_posix() for p in (ROOT/'tests').glob('test_minute1_*.py')}
    assert expected<=set(pytestconfig.getini('testpaths'))
    assert (ROOT/'conftest.py').is_file()


def test_routes_cover_exact_observed_failures_without_modifying_old_assertions():
    value=manifest();routes=value['routes']
    assert len(routes)==len({r['nodeid'] for r in routes})==value['expected_nodes']==323
    assert Counter(r['cause'] for r in routes)==value['cause_counts']
    assert len({r['module'] for r in routes})==13
    for module,pin in value['original_test_sha256'].items():
        assert hashlib.sha256((ROOT/module).read_bytes()).hexdigest()==pin
        assert (ROOT/module).read_bytes()==(historical_path(value['frozen_root'])/module).read_bytes()
    assert all(r['historical_profile'] and r['explanation'] for r in routes)


def test_current_minute_source_cannot_borrow_historical_native_or_recorder_identity():
    from tools.annual_program import flow_source_contracts as contracts,witness
    from tools.sci_engineering.runner import source_binding
    observed=source_binding(ROOT)
    native={name:observed[name] for name in contracts.LEGACY_NATIVE_SOURCE_CONTRACT}
    recorder=hashlib.sha256(Path(witness.__file__).read_bytes()).hexdigest()
    # This new native revision changes only the actual thermal selector. It
    # must not borrow the older FUEL leaf identity or omit its full admission.
    essential='tools/essential_fixes/execution.py'
    assert native[essential]=='8eb3b210c6615a7bd72bac2c5e7c6fe495164e92f850dc78cd7d70e8e69a2309'
    assert {k:v for k,v in native.items() if k!=essential}=={
        k:v for k,v in contracts.SCI_FUEL_NATIVE_SOURCE_CONTRACT.items() if k!=essential}
    assert contracts.match_native_source_profile(native) is None
    assert contracts.match_native_source_profile(observed) is None
    assert any(observed[name]!=pin for name,pin in contracts.SCI_FUEL_CAPTURE_EXTRA_SOURCE_CONTRACT.items())
    assert contracts.classify_recorder_source_pair(recorder,native)['supported'] is False
    with pytest.raises(ValueError,match='registered capture source profile|frozen source bindings'):
        witness.compiled_origin(ROOT,observed)


# This helper executes original test functions from the real frozen repository.
# It is written under the package test-results or explicit work root, never
# into the read-only historical source checkout.
CHILD=r'''
import json,sys
from pathlib import Path
import pytest
request=json.loads(Path(sys.argv[1]).read_text(encoding='utf8'))
root=Path(request['root']).resolve()
sys.path[:0]=[str(root),str(root/'src'),str(root/'tests')]
class Audit:
    def __init__(self):self.collected=[];self.reports={}
    def pytest_collection_finish(self,session):self.collected=[i.nodeid for i in session.items]
    def pytest_runtest_logreport(self,report):
        self.reports.setdefault(report.nodeid,{})[report.when]=report.outcome
audit=Audit()
args=['-q','-p','no:cacheprovider','--basetemp='+request['basetemp'],
      '--junitxml='+request['junit'],'-p','portable_historical_legacy_fixtures',*request['nodes']]
result=int(pytest.main(args,plugins=[audit]))
wrong_modules={name:str(Path(module.__file__).resolve()) for name,module in sys.modules.copy().items()
    if (name=='tools' or name.startswith('tools.') or name=='hospital_ies' or name.startswith('hospital_ies.'))
    and getattr(module,'__file__',None) and not Path(module.__file__).resolve().is_relative_to(root)}
receipt=dict(exit_code=result,collected=audit.collected,reports=audit.reports,
    source_root=str(root),wrong_loaded_module_paths=wrong_modules,
    native_context=True,parse_only_fixture_execution_claim=False)
Path(request['result']).write_text(json.dumps(receipt,indent=2,sort_keys=True)+'\n',encoding='utf8')
raise SystemExit(result)
'''


def _sha(path):
    value=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):value.update(block)
    return value.hexdigest()


def _frozen_state(value):
    frozen=historical_path(value['frozen_root'])
    head=subprocess.check_output(['git','-C',str(frozen),'rev-parse','HEAD'],text=True).strip()
    status=subprocess.check_output(['git','-C',str(frozen),'status','--porcelain'],text=True).strip()
    snapshot=historical_path(value['snapshot']['path'])
    assert _sha(snapshot)==value['snapshot']['sha256'],'Frozen tracked-file snapshot drift'
    pins=json.loads(snapshot.read_text(encoding='utf8'))['files']
    paths=subprocess.check_output(['git','-C',str(frozen),'ls-files','-z']).decode().rstrip('\0').split('\0')
    assert set(paths)==set(pins),'Frozen tracked-file inventory drift'
    observed={name:_sha(frozen/name) for name in paths}
    assert head==value['frozen_head'] and not status,'Frozen source HEAD/worktree drift'
    assert observed==pins,'Frozen source bytes changed'
    return dict(head=head,tracked_files=len(pins),source_snapshot_sha256=_sha(snapshot),
        worktree_clean=True,all_tracked_bytes_match=True)


@pytest.fixture(scope='session')
def historical_run():
    value=manifest();before=_frozen_state(value)
    folder=historical_output_root(value)/(time.strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:8])
    folder.mkdir(parents=True,exist_ok=False)
    child=folder/'frozen_pytest_child.py';child.write_text(CHILD,encoding='utf8')
    groups={module:sorted(r['nodeid'] for r in value['routes'] if r['module']==module)
        for module in sorted({r['module'] for r in value['routes']})}
    assert sum(map(len,groups.values()))==323
    results={};frozen=historical_path(value['frozen_root'])
    plugin=historical_path(value['legacy_fixture_plugin']['path'])
    assert _sha(plugin)==value['legacy_fixture_plugin']['sha256'],'Historical fixture path migration plugin drift'
    env=os.environ.copy();env['PYTHONPATH']=os.pathsep.join(map(str,(frozen,frozen/'src',frozen/'tests',plugin.parent)))
    env['IES_HISTORICAL_CONTEXT_ROOT']=str(frozen)
    env['PYTEST_ADDOPTS']='';env['PYTEST_PLUGINS']='';env['PYTEST_DISABLE_PLUGIN_AUTOLOAD']='1'
    env['PYTHONDONTWRITEBYTECODE']='1';env['TEMP']=env['TMP']=str(folder)
    for module,nodes in groups.items():
        stem=Path(module).stem
        paths={name:str(folder/(stem+suffix)) for name,suffix in (
            ('request','.request.json'),('result','.result.json'),('junit','.junit.xml'),('log','.log'),('basetemp','.tmp'))}
        request=dict(root=str(frozen),nodes=nodes,**paths)
        Path(paths['request']).write_text(json.dumps(request,indent=2),encoding='utf8')
        state_before=_frozen_state(value)
        command=[sys.executable,'-B',str(child),paths['request']]
        with Path(paths['log']).open('w',encoding='utf8') as stream:
            process=subprocess.run(command,cwd=frozen,env=env,stdout=stream,stderr=subprocess.STDOUT,check=False)
        state_after=_frozen_state(value)
        body=json.loads(Path(paths['result']).read_text(encoding='utf8')) if Path(paths['result']).is_file() else {}
        results[module]=dict(command=command,exit_code=process.returncode,expected_nodes=nodes,
            before=state_before,after=state_after,paths=paths,result=body)
    after=_frozen_state(value)
    receipt=dict(schema='hospital_ies.minute1.historical_test_execution.v1',
        route_manifest_sha256=_sha(ROUTES),frozen_source_before=before,frozen_source_after=after,
        expected_nodes=323,executed_nodes=sum(len(r['result'].get('collected',[])) for r in results.values()),
        passed_nodes=sum(sum(phases.get('call')=='passed' and all(v=='passed' for v in phases.values())
            for phases in r['result'].get('reports',{}).values()) for r in results.values()),
        modules=results,scope='Original immutable historical tests executed in real frozen native source; no minute physical qualification')
    (folder/'EXECUTION_RECEIPT.json').write_text(json.dumps(receipt,indent=2,sort_keys=True)+'\n',encoding='utf8')
    print('Historical original-node execution receipt:',folder/'EXECUTION_RECEIPT.json')
    return receipt


@pytest.mark.parametrize('module',sorted({r['module'] for r in manifest()['routes']}))
def test_frozen_historical_module(module,historical_run):
    value=historical_run['modules'][module];body=value['result']
    assert value['exit_code']==body.get('exit_code')==0,value['paths']['log']
    assert sorted(body['collected'])==value['expected_nodes']
    assert sorted(body['reports'])==value['expected_nodes']
    assert not body['wrong_loaded_module_paths']
    for node,phases in body['reports'].items():
        assert phases.get('call')=='passed' and all(v=='passed' for v in phases.values()),node
    assert historical_run['executed_nodes']==historical_run['passed_nodes']==323
