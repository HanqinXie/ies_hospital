"""Small registration and pin checks; never construct a physical runtime."""
import importlib
from pathlib import Path

import pytest


def runner():
    path = Path(__file__).resolve().parents[1] / 'tools/recovery_dispatch_20261104/run_campaign.py'
    assert path.is_file(), 'Recovery-only finite runner is missing'
    return importlib.import_module('tools.recovery_dispatch_20261104.run_campaign')


def test_case_copy_preserves_exact_baseline_bytes_and_refuses_drift(tmp_path):
    module = runner()
    baseline = tmp_path / 'R0.json'
    target = tmp_path / 'R1.json'
    baseline.write_bytes(b'{\r\n  "original_policy": "unchanged"\r\n}\r\n')
    expected = module.sha(baseline)
    module.copy_case_exact(baseline, target, expected)
    assert target.read_bytes() == baseline.read_bytes()
    assert module.sha(target) == expected
    with pytest.raises(FileExistsError):
        module.copy_case_exact(baseline, target, expected)
    baseline.write_bytes(b'{"changed": true}')
    with pytest.raises(ValueError, match='case'):
        module.copy_case_exact(baseline, tmp_path / 'invalid.json', expected)
    assert not (tmp_path / 'invalid.json').exists()


def test_runtime_wrapper_attaches_before_reset_and_keeps_native_identity(monkeypatch):
    # The factory boundary is stubbed because this is a wiring test, not an
    # authorized trajectory. The real attach function installs the real hook.
    runner()
    from tools.recovery_dispatch_20261104 import run_worker as worker
    from tools.recovery_dispatch_20261104.policy import R1_CONFIG, RecoveryCommandMixin

    class OriginalCore:
        pass

    class OriginalController:
        def reset(self):
            pass

        def decide(self, history):
            return {'original': True}, {'native': True}

    class OriginalEnvironment:
        engineering_core_type = OriginalCore

        def reset(self, **kwargs):
            raise AssertionError('Scaffolding must not reset or start a simulation')

    env = OriginalEnvironment()
    controller = OriginalController()
    config = {'original_physics': True}
    frozen = {'unchanged_core.py': 'a' * 64}
    native_policy = {'alias': 'original_native', 'config': {'secure': .35}}
    calls = []

    def original_factory(case, source_freeze, cooling_config):
        calls.append((case, source_freeze, cooling_config))
        return env, controller, {'policy': native_policy, 'model_config': config}

    monkeypatch.setattr(worker, '_native_build_runtime', original_factory)
    actual_env, actual_controller, resolved = worker.build_runtime(
        {'unchanged_case': True}, frozen, {'unchanged_dynamics': True}, intervention_config=R1_CONFIG)
    assert actual_env is env
    assert issubclass(actual_env.engineering_core_type, RecoveryCommandMixin)
    assert issubclass(actual_env.engineering_core_type, OriginalCore)
    assert actual_controller.base_controller is controller
    assert calls == [({'unchanged_case': True}, frozen, {'unchanged_dynamics': True})]
    assert resolved['policy'] == native_policy
    assert resolved['model_config'] is config
    assert resolved['recovery_dispatch_intervention']['config'] == R1_CONFIG
