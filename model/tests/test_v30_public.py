import copy, importlib, json
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1]

def test_v30_entry_keeps_adopted_model_and_expanded_methods():
    mod=importlib.import_module('tools.model_review_v30.validation')
    reg=json.loads((ROOT/'configs/model_review_v29/cases.json').read_text())
    base=reg['cases'][0]
    for alias in ('C','M'):
        case=mod.resolve_case(base,reg,alias)
        assert case['v28_demand_version']=='V28_adopted'
        assert case['policy']['config']['online_GT_count_order']=='after_cost'
        assert case['study_id'].endswith('v30.v1')
    with pytest.raises(ValueError):mod.resolve_case(base,reg,'R_off')
    assert base==reg['cases'][0]

def test_terminal_adapter_only_attaches_after_real_episode(monkeypatch):
    from types import SimpleNamespace
    mod=importlib.import_module('tools.model_review_v30.environment')
    calls=[]
    adapter=SimpleNamespace(run_episode=lambda *args,**kwargs:{'summary':{'observed_until_minute':90.}},
        validate_scenario=lambda x:x,validate_events=lambda x:x,OperatingHospitalIESEnv=object)
    monkeypatch.setattr(mod,'original_adapter',lambda *a,**k:adapter)
    monkeypatch.setattr(mod,'terminal_snapshot',lambda env,observed:calls.append(observed) or {'snapshot_time_minute':observed})
    wrapped=mod.with_terminal(None,thermal_profile={})
    result=wrapped.run_episode(object(),{},object(),1,{})
    assert result['terminal_snapshot']['snapshot_time_minute']==90.
    assert calls==[90.]

def test_failed_wrapper_retains_live_terminal_without_advancing(monkeypatch):
    from types import SimpleNamespace
    mod=importlib.import_module('tools.model_review_v30.environment')
    def failed(*args,**kwargs):raise RuntimeError('synthetic summary failure after actual advance')
    adapter=SimpleNamespace(run_episode=failed,validate_scenario=lambda x:x,validate_events=lambda x:x,OperatingHospitalIESEnv=object)
    monkeypatch.setattr(mod,'original_adapter',lambda *a,**k:adapter)
    monkeypatch.setattr(mod,'terminal_snapshot',lambda env,minute:{'snapshot_time_minute':minute})
    env=SimpleNamespace(core=SimpleNamespace(time=12.))
    with pytest.raises(RuntimeError,match='summary failure'):mod.with_terminal().run_episode(env,{},None,1,{})
    assert env.v30_terminal_snapshot['snapshot_time_minute']==12.

def test_failed_prefix_copies_solver_rows_without_copying_writer_handle(tmp_path):
    from types import SimpleNamespace
    from tools.model_closure_v22.validation import recover_execution_prefix
    class LoggedCalls(list):pass
    with (tmp_path/'open.log').open('w') as writer:
        calls=LoggedCalls([{'phase':'actual','status':0}]);calls.writer=writer
        core=SimpleNamespace(segments=[dict(start_minute=0.,end_minute=12.)],events=[],solver_calls=calls,errors=[])
        env=SimpleNamespace(core=core,v30_terminal_snapshot={'snapshot_time_minute':12.})
        value=recover_execution_prefix(env,dict(duration_steps=2,step_minutes=15.),'test',{'type':'SyntheticSummaryError'})
        assert value['solver_calls']==list(calls)
        assert value['terminal_snapshot']['snapshot_time_minute']==12.
        assert value['summary']['observed_until_minute']==12.
        assert value['summary']['completed'] is False
        assert not writer.closed
