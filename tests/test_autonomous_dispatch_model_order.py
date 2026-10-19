"""Bounded M0/M1 boundary probes; no episode, dispatch MILP or blind data."""
from __future__ import annotations

import copy
import importlib
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from hospital_ies.execution import execution_contract
from hospital_ies.model import load_plant
from hospital_ies.resilience import CARRIERS, GROUPS, RuntimeState, empty_faults
from hospital_ies.state_transition import TransitionCore


def implementation():
    path = ROOT / "tools/autonomous_dispatch/model_order.py"
    assert path.is_file(), "The independent M1 ordered-boundary branch is not implemented"
    return importlib.import_module("tools.autonomous_dispatch.model_order")


def make_core(core_type=TransitionCore, *, gt_kW=100.0, load_kW=1000.0,
              disconnected_kW=0.0, interlock="isolate_largest_gt"):
    """Construct only the current boundary state using the real plant/core."""
    plant = load_plant(ROOT / "configs/plant_configs.json", "doe_capacity_matched_exploratory")
    loads = np.zeros((4, 3))
    loads[0, 0] = load_kW
    base = {f"{g}_{c}_kW": loads[gi, ci]
            for gi, g in enumerate(GROUPS) for ci, c in enumerate(CARRIERS)}
    state = RuntimeState(np.array([gt_kW, 0.0, 0.0]), 2400.0, set(), np.zeros(3), {})
    env = SimpleNamespace(
        plant=plant, state=state, groups=pd.DataFrame([base], index=[1]),
        scenario={"id": "synthetic_boundary_probe", "stage": 1, "start_hour": 1,
                  "duration_steps": 2, "step_minutes": 15, "response_delay_minutes": 0},
        steps_done=0, dt_minutes=15.0, dt_h=0.25, trip_events=[],
        execution_contract=execution_contract({"surplus_interlock": interlock}),
        config={"diesel_start_delay_minutes": 15.0, "gt_ramp_kW_per_hour": 900.0,
                "grid_import_limit_kW": 850.0},
    )
    action = {"gt_electric_kW": [gt_kW, 0.0, 0.0], "grid_import_kW": 0.0,
              "diesel_kW": 0.0, "boiler_steam_lbph": 0.0,
              "absorption_cooling_kW": 0.0, "electric_chiller_cooling_kW": [0.0] * 3}
    core = core_type(env, action)
    state.disconnected_loads[0, 0] = disconnected_kW
    row = SimpleNamespace(outdoor_temperature_C=25.0)

    def current_data(_minute):
        availability = empty_faults()
        for device in state.extra_faulted:
            availability[device] = 0.0
        return 1, row, loads.copy(), availability

    core.data_at = current_data
    return core


def command(core, *, identifier=1, gt_kW=150.0):
    action = copy.deepcopy(core.executed)
    action["gt_electric_kW"] = [gt_kW, 0.0, 0.0]
    return {"id": identifier, "generated_minute": core.time,
            "arrival_minute": core.time, "dispatch": action, "disconnected": np.zeros((4, 3)).tolist(),
            "reference_cooling_kW": 0.0, "weight": 0.0, "request": {}}


def interlock_events(core):
    return [e for e in core.events if e["kind"] in {"minimum_output_interlock", "no_export_interlock"}]


def test_m0_observation_precedes_advance_interlock():
    core = make_core()
    core.boundary()
    assert core.observe()["gt_prev_kW"][0] == 100.0
    assert not core.state.extra_faulted and not interlock_events(core)
    _, row, loads, availability = core.data_at(core.time)
    core._surplus_interlock(loads - core.state.disconnected_loads, availability, row)
    assert core.state.extra_faulted == {"GT_1"}
    assert interlock_events(core)[0]["kind"] == "minimum_output_interlock"


def test_m1_observation_sees_existing_interlock_and_repetition_does_not_retrip():
    branch = implementation()
    core = make_core(branch.OrderedTransitionCore)
    core.boundary()
    assert core.observe()["gt_prev_kW"].tolist() == [0.0, 0.0, 0.0]
    assert core.observe()["availability"]["GT_1"] == 0.0
    assert core.state.extra_faulted == {"GT_1"}
    assert len(interlock_events(core)) == len(core.env.trip_events) == 1
    before = copy.deepcopy(core.executed)
    core.boundary()
    core.boundary()
    assert core.executed == before
    assert len(interlock_events(core)) == len(core.env.trip_events) == 1
    assert not core.segments and not core.solver_calls


def test_same_time_preexisting_due_command_precedes_m1_interlock():
    branch = implementation()
    core = make_core(branch.OrderedTransitionCore)
    core.pending_commands.append(command(core))
    core.boundary()
    assert core.executed["gt_electric_kW"][0] == 150.0
    assert core.active_command["id"] == 1
    assert not core.state.extra_faulted and not interlock_events(core)
    assert [e["kind"] for e in core.events if e["kind"].startswith("command_")] == [
        "command_arrived", "command_executed"]


def test_later_queued_same_time_command_cannot_undo_m1_interlock():
    branch = implementation()
    core = make_core(branch.OrderedTransitionCore)
    core.boundary()
    core.pending_commands.append(command(core))
    core.boundary()
    assert core.active_command["id"] == 1
    assert core.executed["gt_electric_kW"] == [0.0, 0.0, 0.0]
    assert core.state.extra_faulted == {"GT_1"}
    assert len(core.env.trip_events) == 1
    kinds = [e["kind"] for e in core.events]
    assert kinds.index("minimum_output_interlock") < kinds.index("command_executed")


def test_m1_uses_connected_demand_for_existing_surplus_check():
    branch = implementation()
    core = make_core(branch.OrderedTransitionCore, gt_kW=200.0, load_kW=300.0,
                     disconnected_kW=250.0)
    core.boundary()
    events = interlock_events(core)
    assert len(events) == 1 and events[0]["kind"] == "no_export_interlock"
    assert events[0]["connected_electric_kW"] == 50.0
    assert events[0]["power_surplus_kW"] == pytest.approx(150.0, abs=1e-9)


def test_m1_preserves_disabled_interlock_contract():
    branch = implementation()
    m0 = make_core(interlock="none")
    m1 = make_core(branch.OrderedTransitionCore, interlock="none")
    m0.boundary()
    m1.boundary()
    assert m0.executed == m1.executed
    assert m0.events == m1.events
    assert not m1.state.extra_faulted


def test_m1_does_not_add_a_diesel_only_surplus_response():
    branch = implementation()
    core = make_core(branch.OrderedTransitionCore, gt_kW=0.0, load_kW=350.0)
    _, row, loads, _ = core.data_at(0.0)
    availability = empty_faults()
    availability["GRID"] = 0.0
    core.data_at = lambda _minute: (1, row, loads.copy(), availability.copy())
    core.time = 15.0
    core.state.diesel_start_minute = 0.0
    core.executed["diesel_kW"] = 750.0
    core.boundary()
    assert core.state.diesel_status == "CONNECTED"
    assert core.executed["diesel_kW"] == core.state.last_diesel_kW == 750.0
    assert not core.state.extra_faulted and not interlock_events(core)
    assert not core.segments and not core.solver_calls


def test_mixin_composes_with_another_transition_subclass():
    branch = implementation()
    class JointCore(TransitionCore):
        def boundary(self):
            self.log("joint_boundary_entered")
            return super().boundary()

    class OrderedJointCore(branch.OrderedBoundaryMixin, JointCore):
        pass

    core = make_core(OrderedJointCore)
    core.boundary()
    assert core.events[0]["kind"] == "joint_boundary_entered"
    assert core.state.extra_faulted == {"GT_1"}


def test_finite_down_diagnostic_is_positive_even_with_zero_response_delay():
    branch = implementation()
    # Pure mathematical values; 900 is not assigned as a diesel device rate.
    bound = branch.finite_down_excess_energy_lower_bound_kWh
    assert bound(300.0, 900.0) == pytest.approx(50.0)
    assert bound(300.0, 900.0, 0.25) == pytest.approx(51.25)
    assert bound(0.0, 900.0, 15.0) == 0.0


@pytest.mark.parametrize("args", [(-1.0, 900.0, 0.0), (1.0, 0.0, 0.0),
    (1.0, -1.0, 0.0), (1.0, 900.0, -1.0), (float("nan"), 900.0, 0.0),
    (1.0, float("inf"), 0.0), (1.0, 900.0, float("nan"))])
def test_finite_down_diagnostic_rejects_invalid_inputs(args):
    branch = implementation()
    with pytest.raises(ValueError):
        branch.finite_down_excess_energy_lower_bound_kWh(*args)
