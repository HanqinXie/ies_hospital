"""Strict U regression probes; no initialization, optimizer, or trajectories."""
from copy import deepcopy
from dataclasses import dataclass, field
from types import SimpleNamespace

import numpy as np
import pytest

from test_minute1_environment import create, FakeEnvironment
from tools.essential_fixes.execution import EssentialResponseMixin


@dataclass
class ProbeState:
    disconnected_loads: np.ndarray = field(default_factory=lambda: np.zeros((4, 3)))


class LowerOptimizationReached(RuntimeError):
    pass


def install_native_probe(monkeypatch, *, at_final_boundary=False):
    """Exercise the real advance entry until its first physical _solve call."""
    attempts = []

    def native_step(env, action=None):
        env._issue_upper_command({} if action is None else action)
        core = env.core
        core.env = SimpleNamespace()
        core.segments = []
        if at_final_boundary:
            # Actual EssentialResponseMixin.advance ends with self.boundary().
            core.time += env.dt_minutes
            def final_boundary():
                attempts.append(('terminal_boundary', core.time))
                raise LowerOptimizationReached('legacy terminal boundary optimization reached')
            core.boundary = final_boundary
            return EssentialResponseMixin.advance(core, core.time)
        core.state = ProbeState()
        core.active_command = None
        core.boundary = lambda: None
        loads = np.ones((4, 3))
        core.data_at = lambda time: (1, None, loads.copy(), {})
        core._surplus_interlock = lambda *args: None
        core._next_boundary = lambda end: end
        core._local_interval_action = lambda end, faults, row: ({}, np.zeros(3), np.zeros(3))

        def solve(*args, **kwargs):
            attempts.append((args[3], core.time))
            raise LowerOptimizationReached('legacy fixed_execution lower optimization reached')

        core._solve = solve
        return EssentialResponseMixin.advance(core, core.time + env.dt_minutes)

    monkeypatch.setattr(FakeEnvironment, 'step', native_step)
    return attempts


@pytest.mark.parametrize('minute,terminal', [(60., False), (59., True)])
def test_strict_u_blocks_legacy_lower_optimization_before_native_step(monkeypatch, minute, terminal):
    attempts = install_native_probe(monkeypatch, at_final_boundary=terminal)
    _, env, *_ = create(monkeypatch, 'unattended')
    env.reset(scenario={'step_minutes': 1., 'duration_steps': 180}, seed=7)
    env.core.time = minute
    before = (env.core.time, deepcopy(env.core.pending_commands), deepcopy(env.history), deepcopy(env.core.generated))
    with pytest.raises(RuntimeError, match='strict_unattended_forward_unavailable'):
        env.step(None)
    assert attempts == []
    assert (env.core.time, env.core.pending_commands, env.history, env.core.generated) == before


@pytest.mark.parametrize('minute,terminal,phase', [
    (60., False, 'fixed_execution'), (59., True, 'terminal_boundary')])
def test_unwrapped_legacy_native_execution_reaches_lower_or_terminal_probe(monkeypatch, minute, terminal, phase):
    """Retain the counterexample even after the public-step refusal is added."""
    attempts = install_native_probe(monkeypatch, at_final_boundary=terminal)
    _, env, *_ = create(monkeypatch, 'unattended')
    env.reset(scenario={'step_minutes': 1., 'duration_steps': 180}, seed=7)
    env.core.time = minute
    with pytest.raises(LowerOptimizationReached):
        FakeEnvironment.step(env, None)
    assert attempts == [(phase, 60.)]
