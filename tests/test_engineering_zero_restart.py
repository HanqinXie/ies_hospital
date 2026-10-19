"""Zero-startup min-off restart targets use arrived stable requests, not probes.

Reuse the existing small actuator environment; no annual input or trajectory is
loaded. This covers a sensitivity branch separately from the primary 10 min one.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tests"), str(ROOT), str(ROOT / "src")]

from test_engineering_dynamics import core, target
from hospital_ies.model import gt_level_properties


@pytest.mark.parametrize("arrived_positive_kw", [180.0, 210.0, 150.0])
def test_zero_start_min_off_wait_retains_arrived_stable_target(arrived_positive_kw):
    x = core()
    x.env.engineering_config["gt_start_delay_minutes"] = 0.0
    x.env.execution_contract["gt_start_delay_minutes"] = 0.0
    x.gt_clocks.commit([180.0, 0.0, 0.0], 0.0)
    x.gt_clocks.commit([0.0, 0.0, 0.0], 1.0)
    availability = x.data_at(0.0)[3]
    row = x.data_at(0.0)[1]
    rated = float(gt_level_properties(x.env.plant, row.outdoor_temperature_C)[0][-1])
    stable_floor = 0.4 * rated * availability["GT_1"]

    x.time = 5.0
    first = x._clamp(target(x, 180.0), availability, normal_ramp=True)
    assert first["gt_electric_kW"][0] == 0.0
    assert x.gt_start_pending[0]["started"] == pytest.approx(31.0)

    # The parent ordinary 900 kW/h clamp has only 75 kW at this five-minute
    # interval. That temporary candidate cannot replace a legal online target
    # while the actual machine is still OFF waiting for its min-down clock.
    x.time = 10.0
    repeated = x._clamp(target(x, arrived_positive_kw), availability, normal_ramp=True)
    assert repeated["gt_electric_kW"][0] == 0.0
    pending = x.gt_start_pending[0]
    assert pending["started"] == pytest.approx(31.0)
    assert pending["requested"] == pytest.approx(arrived_positive_kw)
    assert pending["requested"] >= stable_floor
    expected_ready = 31.0 + arrived_positive_kw / 900.0 * 60.0
    assert pending["ready"] == pytest.approx(expected_ready)

    # At its real ready boundary the stored electrical target is still stable;
    # no minimum-output interlock should be manufactured from the 75 kW probe.
    x.time = expected_ready
    ready = x._clamp(target(x, pending["requested"]), availability, normal_ramp=False)
    assert ready["gt_electric_kW"][0] == pytest.approx(arrived_positive_kw)
    assert ready["gt_electric_kW"][0] >= stable_floor
