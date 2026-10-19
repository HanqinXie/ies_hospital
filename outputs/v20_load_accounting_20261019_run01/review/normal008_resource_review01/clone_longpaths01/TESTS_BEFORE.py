"""Clone receipt regressions use fake Git responses; no network or physics."""
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from publication import clone_release as clone_tool
from publication.build_delivery_archive import validate_clone_receipt


COMMIT='b'*40
RAW_LOG=b'fixture clone stdout and stderr\n'


@pytest.fixture
def git_fixture(tmp_path,monkeypatch):
    launcher=tmp_path/'launcher';(launcher/'publication').mkdir(parents=True)
    target=tmp_path/'fresh_clone'
    clone_tool.write_new(launcher/'publication/V20_RELEASE_ROOT.json',dict(status='final',
        source_root=str(tmp_path/'source_original'),original_scientific_root=str(tmp_path/'science_original'),
        original_research_root=str(tmp_path/'research_original')))
    selection=b'{"files":["source.py"],"status":"final"}\n'
    (launcher/'publication/V20_FILE_SELECTION.json').write_bytes(selection)
    state=SimpleNamespace(target=target,launcher=launcher,query_fail_at=None,clone_exit=0,queries=[],
        clone_calls=[],selection_missing=False,selection_mismatch=False,remote_commit=COMMIT)
    monkeypatch.setattr(clone_tool,'ROOT',launcher)
    def fake_clone(command,**kwargs):
        assert command==['git','clone','--branch','main','--single-branch',clone_tool.URL,str(target)]
        assert kwargs['check'] is False and kwargs['env']['GIT_TERMINAL_PROMPT']=='0'
        state.clone_calls.append(command);kwargs['stdout'].write(RAW_LOG)
        if state.clone_exit==0:
            (target/'publication').mkdir(parents=True)
            if not state.selection_missing:
                (target/'publication/V20_FILE_SELECTION.json').write_bytes(b'changed' if state.selection_mismatch else selection)
        return subprocess.CompletedProcess(command,state.clone_exit)
    def fake_git_query(command,**kwargs):
        index=len(state.queries);state.queries.append(dict(command=command,cwd=str(kwargs['cwd'])))
        if index==state.query_fail_at:raise subprocess.CalledProcessError(128,command,output=b'fixture Git query failed')
        if kwargs['cwd']==launcher:
            assert command==['git','rev-parse','HEAD'];return COMMIT.encode()
        assert kwargs['cwd']==target
        if command==['git','rev-parse','HEAD']:return state.remote_commit.encode()
        if command==['git','remote','get-url','origin']:return clone_tool.URL.encode()
        if command==['git','branch','--show-current']:return b'main'
        raise AssertionError('Unexpected mocked Git query')
    monkeypatch.setattr(clone_tool.subprocess,'run',fake_clone)
    monkeypatch.setattr(clone_tool.subprocess,'check_output',fake_git_query)
    return state


def receipt(state):
    evidence=state.target.parent/(state.target.name+'.clone_evidence')
    import json
    record=json.loads((evidence/'COMMAND.json').read_text(encoding='utf8'))
    assert (evidence/'START.json').is_file()
    assert (evidence/'command.log').read_bytes()==RAW_LOG
    assert record['command_log_sha256']==clone_tool.sha(evidence/'command.log')
    if state.target.is_dir():
        assert (state.target/'publication_checks/clone01/COMMAND.json').read_bytes()==(evidence/'COMMAND.json').read_bytes()
        assert (state.target/'publication_checks/clone01/command.log').read_bytes()==RAW_LOG
    return record


@pytest.mark.parametrize('failed_query',range(4))
def test_successful_clone_retains_terminal_receipt_when_any_git_query_fails(git_fixture,failed_query):
    state=git_fixture;state.query_fail_at=failed_query
    assert clone_tool.clone(state.target)==2
    record=receipt(state)
    assert record['exit_code']==0  # The actual clone succeeded; validation failed.
    assert record['helper_validation_status']=='failed_unknown'
    assert record['validation_error']['type']=='CalledProcessError'


def test_nonzero_clone_retains_its_real_exit_code_without_needing_git_queries(git_fixture):
    state=git_fixture;state.clone_exit=17;state.query_fail_at=0
    assert clone_tool.clone(state.target)==17
    record=receipt(state)
    assert record['exit_code']==17 and record['helper_validation_status']=='failed_unknown'
    assert record['validation_error'] is not None
    assert state.queries==[]


@pytest.mark.parametrize('problem',['selection_missing','selection_mismatch','commit_mismatch'])
def test_post_clone_validation_failure_keeps_clone_exit_and_original_log(git_fixture,problem):
    state=git_fixture
    if problem=='commit_mismatch':state.remote_commit='c'*40
    else:setattr(state,problem,True)
    assert clone_tool.clone(state.target)==2
    record=receipt(state)
    assert record['exit_code']==0 and record['helper_validation_status']=='failed_unknown'
    assert record['validation_error'] is not None


def test_success_receipt_remains_compatible_with_archive_gate(git_fixture):
    state=git_fixture
    assert clone_tool.clone(state.target)==0
    record=receipt(state)
    assert record['helper_validation_status']=='passed' and record['validation_error'] is None
    checked=validate_clone_receipt(state.target,{'staged_root':str(state.launcher)},record,
        head=COMMIT,origin=clone_tool.URL,branch='main',origin_main=COMMIT,
        launcher_sha256=clone_tool.sha(Path(clone_tool.__file__)),log_sha256=record['command_log_sha256'])
    assert checked['git_commit']==COMMIT
