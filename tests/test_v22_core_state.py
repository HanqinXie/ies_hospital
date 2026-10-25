"""Current-only inventory and station state; trial queries have no side effects."""
import sys
from pathlib import Path
import pytest
sys.path[:0]=[str(Path(__file__).resolve().parents[1]),str(Path(__file__).resolve().parents[1]/'src')]

def test_boiler_requires_continuous_actual_restart_power():
    from tools.model_closure_v22.station import StationState
    s=StationState({'boiler_restart_minutes':1.})
    s.commit(.5,boiler_requested=True,boiler_restart_supplied=True,station_live=True)
    assert not s.boiler_ready
    s.commit(.25,boiler_requested=True,boiler_restart_supplied=False,station_live=False)
    s.commit(.75,boiler_requested=True,boiler_restart_supplied=True,station_live=True)
    assert not s.boiler_ready
    s.commit(.25,boiler_requested=True,boiler_restart_supplied=True,station_live=True)
    assert s.boiler_ready

def test_dedicated_dc_is_finite_and_not_created_by_building_ups():
    from tools.model_closure_v22.station import StationState
    s=StationState({'control_autonomy_minutes':5.,'dg_qualified_starts':2})
    assert s.control_available(4.9,True)
    assert not s.control_available(5.,True)
    assert not s.control_available(0.,False)
    assert s.claim_diesel_start(0.,True)
    assert s.claim_diesel_start(1.,True)
    assert not s.claim_diesel_start(2.,True)

def test_bridge_target_uses_current_critical_request_and_known_clocks():
    from tools.model_closure_v22.storage import bridge_target_kWh
    assert bridge_target_kWh(100.,40.,.95,observation_minutes=15.,start_minutes=1.,transfer_minutes=.25)==pytest.approx(100.*15.25/60./.95)
    assert bridge_target_kWh(1000.,40.,.95,observation_minutes=15.,start_minutes=15.,transfer_minutes=.25)==40.

def test_cold_charge_hysteresis_is_changed_only_by_actual_commit():
    from tools.model_closure_v22.storage import ColdRechargeState
    s=ColdRechargeState()
    assert not s.allowed(99.5,100.)
    assert s.allowed(97.,100.)
    assert not s.active
    s.commit(97.,98.,100.,charged_input_kWh=1.)
    assert s.active and s.allowed(99.9,100.)
    s.commit(99.9,100.,100.,charged_input_kWh=.1)
    assert not s.active
