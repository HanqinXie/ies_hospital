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
        clone_calls=[],clone_environments=[],local_config_calls=[],local_config_exit=0,local_config_error=None,
        selection_missing=False,selection_mismatch=False,remote_commit=COMMIT)
    monkeypatch.setattr(clone_tool,'ROOT',launcher)
    def fake_clone(command,**kwargs):
        if command==['git','config','--local','core.longpaths','true']:
            assert kwargs['cwd']==target and kwargs['check'] is False
            state.local_config_calls.append(command)
            if state.local_config_error:raise state.local_config_error
            kwargs['stdout'].write(b'fixture local Git configuration\n')
            return subprocess.CompletedProcess(command,state.local_config_exit)
        assert command==['git','clone','--branch','main','--single-branch',clone_tool.URL,str(target)]
        assert kwargs['check'] is False and kwargs['env']['GIT_TERMINAL_PROMPT']=='0'
        state.clone_calls.append(command);state.clone_environments.append(kwargs['env'].copy());kwargs['stdout'].write(RAW_LOG)
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
    assert state.local_config_calls==[] and record['local_longpaths_config'] is None


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


@pytest.mark.parametrize('inherited_count',[0,2])
def test_clone_enables_long_paths_without_changing_argv_or_inherited_git_config(git_fixture,monkeypatch,inherited_count):
    import json
    state=git_fixture
    monkeypatch.setenv('GIT_CONFIG_COUNT',str(inherited_count))
    inherited={}
    if inherited_count:
        inherited={'GIT_CONFIG_KEY_0':'http.extraHeader','GIT_CONFIG_VALUE_0':'fixture-sensitive-value-do-not-log',
                   'GIT_CONFIG_KEY_1':'core.longpaths','GIT_CONFIG_VALUE_1':'false'}
        for key,value in inherited.items():monkeypatch.setenv(key,value)
    original=dict(clone_tool.os.environ)
    assert clone_tool.clone(state.target)==0
    env=state.clone_environments[0]
    assert env['GIT_CONFIG_COUNT']==str(inherited_count+1)
    assert env['GIT_CONFIG_KEY_'+str(inherited_count)]=='core.longpaths'
    assert env['GIT_CONFIG_VALUE_'+str(inherited_count)]=='true'
    assert all(env[key]==value for key,value in inherited.items())
    assert dict(clone_tool.os.environ)==original
    record=receipt(state)
    assert record['clone_scoped_git_config']==dict(name='core.longpaths',value='true',
        inherited_count=inherited_count,appended_index=inherited_count,preserved_existing_pairs=True)
    assert state.local_config_calls==[['git','config','--local','core.longpaths','true']]
    local=record['local_longpaths_config']
    assert local['exit_code']==0 and local['command']==state.local_config_calls[0]
    assert local['cwd']==str(state.target)
    assert local['command_log_sha256']==clone_tool.sha(state.target.parent/(state.target.name+'.clone_evidence')/'local_config.log')
    assert (state.target/'publication_checks/clone01/local_config.log').read_bytes()==(state.target.parent/(state.target.name+'.clone_evidence')/'local_config.log').read_bytes()
    encoded=json.dumps(record)+json.dumps(json.loads((state.target.parent/(state.target.name+'.clone_evidence')/'START.json').read_text(encoding='utf8')))
    assert 'fixture-sensitive-value-do-not-log' not in encoded


def test_local_longpaths_failure_preserves_successful_clone_exit_and_real_config_exit(git_fixture):
    state=git_fixture;state.local_config_exit=23
    assert clone_tool.clone(state.target)==2
    record=receipt(state)
    assert record['exit_code']==0 and record['helper_validation_status']=='failed_unknown'
    assert record['local_longpaths_config']['exit_code']==23
    assert record['validation_error'] is not None and state.queries==[]


def test_local_longpaths_launch_error_remains_unknown_not_zero(git_fixture):
    state=git_fixture;state.local_config_error=OSError('fixture local configuration launch failed')
    assert clone_tool.clone(state.target)==2
    record=receipt(state)
    assert record['exit_code']==0 and record['helper_validation_status']=='failed_unknown'
    assert record['local_longpaths_config']['exit_code'] is None
    assert record['local_longpaths_config']['error']['type']=='OSError'
    assert state.queries==[]


@pytest.mark.parametrize('environment',[{'GIT_CONFIG_COUNT':'bad'}, {'GIT_CONFIG_COUNT':'-1'},
    {'GIT_CONFIG_COUNT':'1','GIT_CONFIG_KEY_0':'core.longpaths'}])
def test_invalid_inherited_git_configuration_is_not_silently_replaced(environment):
    with pytest.raises(ValueError):clone_tool.clone_environment(environment)
