"""SF1 runner contracts use native orchestration doubles; no formal trajectory."""
from copy import deepcopy
import importlib
from pathlib import Path
from types import SimpleNamespace
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


def runner():
    assert (ROOT/'tools/single_fault_model_20261102/runner.py').exists(), 'SF1 producer is missing'
    return importlib.import_module('tools.single_fault_model_20261102.runner')


def test_runner_prepares_canonical_case_then_records_explicit_physics_without_mutation(monkeypatch, tmp_path):
    module = runner()
    case = {'id':'canonical', 'scenario':{'faults':{}, 'auxiliary_faults':{}, 'model_closure_events':[]}}
    before = deepcopy(case)
    calls = []
    output = tmp_path/'attempt'
    def prepare(actual, destination, **kwargs):
        calls.append(('prepare', deepcopy(actual), kwargs))
        destination.mkdir()
        (destination/'temp').mkdir()
        return deepcopy(actual), destination, {'source':{'new_source':'real-sha'}}
    def execute(destination, **kwargs):
        calls.append(('execute', destination, kwargs))
        return {'case_id':'canonical', 'status':'completed'}
    monkeypatch.setattr(module.native, '_prepare_attempt', prepare)
    monkeypatch.setattr(module, '_execute_attempt', execute)
    result = module.run_case(case, output, physical_max_segment_minutes=.5, stopfile=tmp_path/'stop')
    assert result['status'] == 'completed' and case == before
    invocation = module.native._read(output/'INVOCATION.json')
    binding = module.native._read(output/'PHYSICAL_MODEL_BINDING.json')
    assert invocation['single_fault_model']['physical_max_segment_minutes'] == .5
    assert binding['physical_model_spec'] == invocation['single_fault_model']
    assert binding['recording_wrapper_model_id'] == module.native.MODEL_ID
    assert binding['scientific_source_binding'] == {'new_source':'real-sha'}
    assert binding['old_source_byte_equivalence_claimed'] is False
    assert calls[0][1] == case and calls[1][2]['model_spec'].physical_max_segment_minutes == .5
    assert not (output/'RESULT.json').exists()


def test_scope_and_spec_rejected_before_native_attempt_creation(monkeypatch, tmp_path):
    module = runner()
    def forbid(*args, **kwargs): pytest.fail('Native preparation called before scope restriction')
    monkeypatch.setattr(module.native, '_prepare_attempt', forbid)
    with pytest.raises(ValueError):
        module.run_case({'scenario':{'faults':{'GT_1':0., 'ABS':0.}}}, tmp_path/'multi')
    with pytest.raises(ValueError):
        module.run_case({'scenario':{}}, tmp_path/'bad', physical_max_segment_minutes=2.)


def test_native_loop_is_reused_with_explicit_snapshot_only():
    module = runner()
    from tools.single_fault_model_20261102.environment import decision_snapshot
    assert module._execute_loop.__code__ is module.native._execute_loop.__code__
    assert module._execute_loop.__globals__['_snapshot'] is decision_snapshot
    assert module._native_execute_attempt.__code__ is module.native._execute_attempt.__code__
    assert module._native_execute_attempt.__globals__['_execute_loop'] is module._execute_loop


def test_cli_file_path_help_retains_all_required_options():
    executed = subprocess.run([sys.executable, '-B', str(ROOT/'tools/single_fault_model_20261102/runner.py'),
        '--help'], cwd=ROOT, capture_output=True, text=True)
    assert executed.returncode == 0, executed.stderr
    for name in ('--case-file', '--output', '--physical-max-segment-minutes', '--stopfile'):
        assert name in executed.stdout


def test_compiled_origin_rejection_is_not_bypassed_or_relabelled(monkeypatch, tmp_path):
    module = runner()
    from tools.annual_program import witness
    from tools.single_fault_model_20261102.runtime import ModelSpec
    calls = []
    output = tmp_path/'attempt'
    output.mkdir()
    module.native._write_new(output/'BINDINGS.json', {'source':{'new-source':'new-hash'}})
    def reject(root, frozen):
        calls.append(('origin', root, frozen))
        raise ValueError('unregistered scientific source profile')
    def no_build(*a, **kw): pytest.fail('A rejected origin reached native construction')
    def native_attempt(destination, *, runtime_builder):
        try:
            runtime_builder({}, {}, destination, None)
        except ValueError as exc:
            return {'status':'failed', 'message':str(exc)}
        pytest.fail('Origin rejection was suppressed')
    monkeypatch.setattr(witness, 'compiled_origin', reject)
    monkeypatch.setattr(module, '_build_runtime', no_build)
    monkeypatch.setattr(module, '_native_execute_attempt', native_attempt)
    result = module._execute_attempt(output, model_spec=ModelSpec())
    assert result == {'status':'failed', 'message':'unregistered scientific source profile'}
    assert calls == [('origin', ROOT, {'new-source':'new-hash'})]
