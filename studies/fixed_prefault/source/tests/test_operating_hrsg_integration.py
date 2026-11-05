"""Versioned HRSG sensitivity uses current actual GT history, not future events."""
from types import SimpleNamespace
from pathlib import Path
import sys
import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
from tools.operating_response.environment import OperatingCommandCore, model_contract
from tools.operating_response.execution import LocalResponseMixin
from tools.engineering_model.environment import EngineeringCommandCore


def test_m15_resolves_explicit_hrsg_delay_and_default_remains_zero():
    _, cfg = model_contract('M15_local_response', {'hrsg_thermal_readiness': {'warmup_delay_minutes': 15.}})
    assert cfg['hrsg_thermal_readiness'] == {'warmup_delay_minutes': 15.}
    _, default = model_contract('M15_local_response', {})
    assert default['hrsg_thermal_readiness']['warmup_delay_minutes'] == 0.
    with pytest.raises(ValueError):
        model_contract('M14_engineering', {'hrsg_thermal_readiness': {'warmup_delay_minutes': 15.}})


def test_same_current_thermal_gate_reaches_planning_and_actual_data(monkeypatch):
    hardware = {f'GT_{i}': 1. for i in (1, 2, 3)}
    hardware.update(HRSG_1=.4, HRSG_2=.8, HRSG_3=.7, GRID=1.)
    monkeypatch.setattr(EngineeringCommandCore, 'data_at', lambda self, minute: (1, 'row', 'loads', hardware))
    core = object.__new__(OperatingCommandCore)
    core.env = SimpleNamespace(model_id='M15_local_response', hrsg_thermal_config={'warmup_delay_minutes': 15.})
    core.gt_clocks = SimpleNamespace(running=np.array([True, False, True]), last_on=np.array([-60., -60., 10.]))
    result = core.data_at(20.)
    assert result[:3] == (1, 'row', 'loads')
    assert [result[-1][f'HRSG_{i}'] for i in (1, 2, 3)] == [.4, 0., 0.]
    assert [core.data_at(25.)[-1][f'HRSG_{i}'] for i in (1, 2, 3)] == [.4, 0., .7]
    assert hardware['HRSG_2'] == .8 and hardware['HRSG_3'] == .7
    assert result[-1]['GRID'] == 1.


def test_thermal_ready_boundary_splits_a_committed_gt_interval_exactly():
    env = SimpleNamespace(scenario={'stage': 0, 'duration_steps': 40, 'step_minutes': 15.},
        config={'diesel_start_delay_minutes': 15.}, execution_contract={'ats_transfer_minutes': 0.},
        hrsg_thermal_config={'warmup_delay_minutes': 15.})
    core = SimpleNamespace(env=env, time=20., pending_commands=[], gt_start_pending={},
        pending_shed=None, local_down=None, state=SimpleNamespace(diesel_start_minute=None),
        grid_outage_start=None, gt_clocks=SimpleNamespace(running=np.array([True, False, False]),
                                                        last_on=np.array([10., -60., -60.])))
    assert LocalResponseMixin._next_boundary(core, 100.) == 25.
