"""Causal finite-shaft return: same current state, no future event facts."""
import copy
import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from tools.engineering_model.environment import isolated
from test_execution_recovery_core import small_core as original_core, arrive


def implementation():
    from tools.model_closeout.execution import ExecutionRecoveryCore as Historical
    try:
        from tools.model_correction_v25.transfer import FiniteSourceReturnMixin
    except ModuleNotFoundError:
        return SimpleNamespace(ExecutionRecoveryCore=Historical)  # RED old mechanism
    return SimpleNamespace(ExecutionRecoveryCore=type('TestCore', (FiniteSourceReturnMixin, Historical), {}))


def current_case(*, remaining=5.085431771, rate=109.606087285, enabled=True):
    loads=np.zeros((4,3));loads[0,0]=50.;loads[1,0]=60.
    factory=isolated(original_core, dict(original_core.__globals__,implementation=implementation))
    c,loads,f,row=factory(gt=0.,loads=loads)
    c.env.model_correction_config={'finite_source_return':enabled}
    f['GRID']=0.;c.boundary();c.time=15.;c.boundary()
    c.segments.append(dict(start_minute=210.,end_minute=225.,loads_kW=loads.tolist(),
        unserved_kW=np.zeros((4,3)).tolist(),diesel_local_response=dict(mechanical_work_kWh=rate/4.)))
    c.time=225.;c.state.diesel_energy_remaining_kWh=remaining
    c.diesel_rotor.mechanical_kW=rate
    f['GRID']=1.;c.bridge_wait_started_minute=210.
    return c,loads,f


def test_original_300_current_state_has_pre_exhaustion_boundary():
    c,_,_=current_case();c.boundary()
    expected=225.+5.085431771/109.606087285*60.-.25-.25
    assert c.grid_bus_state=='ISLAND'
    assert c._next_boundary(240.)==pytest.approx(expected,abs=1e-8)
    assert expected<227.783840878


def test_preparation_is_powered_and_completion_does_not_reenter_old_wait():
    c,_,_=current_case(remaining=.8);c.boundary()
    assert c.grid_bus_state=='RETURN_PREP'
    assert c.executed['diesel_kW']>0. and c.executed['grid_import_kW']==0.
    c.time+=.25;c.boundary()
    assert c.grid_bus_state=='TRANSFER_OPEN'
    assert c.executed['diesel_kW']==c.executed['grid_import_kW']==0.
    c.time+=.249;c.boundary();assert c.grid_bus_state=='TRANSFER_OPEN'
    c.time+=.001;c.boundary();assert c.grid_bus_state=='GRID'


@pytest.mark.parametrize('loss',['grid','permission','destination'])
def test_preparation_completion_rechecks_current_qualification(loss):
    c,_,f=current_case(remaining=.8);c.boundary();assert c.grid_bus_state=='RETURN_PREP'
    c.time+=.25
    if loss=='grid':f['GRID']=0.
    else:arrive(c,grid=0. if loss=='permission' else 1.,diesel=500.)
    c.boundary()
    assert c.grid_bus_state=='ISLAND'
    assert c.executed['grid_import_kW']==0.


@pytest.mark.parametrize('healthy,permit',[(False,True),(True,False)])
def test_no_return_without_current_healthy_grid_and_arrived_permission(healthy,permit):
    c,_,f=current_case(remaining=.01);f['GRID']=float(healthy)
    if not permit:arrive(c,grid=0.,diesel=500.)
    c.boundary();assert c.grid_bus_state=='ISLAND'


def test_nearly_empty_starts_immediately_without_inventing_stock():
    c,_,_=current_case(remaining=1e-5);before=c.state.diesel_energy_remaining_kWh
    c.boundary();assert c.grid_bus_state=='RETURN_PREP'
    assert c.state.diesel_energy_remaining_kWh==before


def test_zero_measured_shaft_rate_keeps_finite_original_wait_without_division():
    c,_,_=current_case(rate=0.);c.boundary();assert c.grid_bus_state=='ISLAND'
    assert c._next_boundary(250.)==pytest.approx(240.)


def test_legacy_switch_preserves_original_wait():
    c,_,_=current_case(remaining=.8,enabled=False);c.boundary()
    assert c.grid_bus_state=='ISLAND'
    assert c._next_boundary(240.)==240.


def test_deadline_uses_observed_shaft_not_electric_and_no_future_scene():
    c,_,_=current_case(rate=200.,remaining=2.)
    c.state.last_diesel_kW=50.
    c.env.scenario['fault_duration_minutes']=999999.
    c.boundary()
    assert c._next_boundary(240.)==pytest.approx(225.1)
