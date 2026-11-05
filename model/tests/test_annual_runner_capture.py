"""Output capture surrounds actual step only, never policy/reset observations."""
from copy import deepcopy
import pytest

from test_annual_program_runner import setup,run
from test_annual_compiled_flow_input import fixture as leaf_values


@pytest.mark.parametrize('fail',[False,True])
def test_scope_is_local_to_step_and_restored_after_success_or_failure(tmp_path,monkeypatch,fail):
    from tools.annual_program.compiled_capture import capture_enabled
    module,env,service,policy,journal=setup(tmp_path,monkeypatch,fail_step=0 if fail else None)
    checked=[]
    original_step=env.step;original_decide=policy.decide;original_reset=env.reset
    def reset(**kwargs):
        assert not capture_enabled();return original_reset(**kwargs)
    def decide(record):
        assert not capture_enabled();return original_decide(record)
    def step(request):
        checked.append(capture_enabled());assert capture_enabled()
        return original_step(request)
    monkeypatch.setattr(env,'reset',reset);monkeypatch.setattr(policy,'decide',decide)
    monkeypatch.setattr(env,'step',step)
    origin=deepcopy(leaf_values()['source_origin'])
    result=run(module,env,service,policy,journal,tmp_path,compiled_capture_origin=origin)
    assert result['status']==('failed' if fail else 'completed')
    assert checked==[True]*(1 if fail else 3)
    assert not capture_enabled()
