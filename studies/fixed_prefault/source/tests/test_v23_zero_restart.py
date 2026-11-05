"""Zero-minute restart is an ideal lower bound, without phantom event clocks."""
import sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'src'),str(ROOT/'tests')]
from test_v23_recovery import make
from test_sci_environment import scenario

def setup_zero(*,no_source=False,grid_restore=False):
    from tools.model_correctness_v23.environment import decision_snapshot
    from tools.model_correctness_v23.policy import SciPolicy
    e=make(dict(model_closure_v22=dict(station=dict(boiler_restart_minutes=0.))))
    s=scenario();s.update(fault_start_minute=0.,fault_duration_minutes=45.,faults=dict(GT_1=0.,GT_2=0.,GT_3=0.),
        model_closure_events=[dict(time_minute=0.,availability={'STATION_AC':0.}),dict(time_minute=.5,availability={'STATION_AC':1.})])
    if no_source:s['faults'].update(GRID=0.,DIESEL=0.)
    if grid_restore:
        s['model_closure_events']=[dict(time_minute=0.,availability={'GRID':0.,'DIESEL':0.}),dict(time_minute=1.,availability={'GRID':1.})]
    e.reset(scenario=s,seed=9);request,_=SciPolicy('R',plant=e.plant).decide([decision_snapshot(e)])
    if grid_restore:
        # Explicit lawful queued grid permission, independent of its unknown
        # return time. Otherwise the rule's zero cap during hardware outage
        # correctly prevents reconnection and tests a different condition.
        request['ordinary_dispatch']['dispatch']['grid_import_kW']=e.config['grid_import_limit_kW']
    e.core.generate_command(request);e.core.boundary()
    return e

def test_zero_restart_has_only_real_source_boundary_and_paid_run_auxiliary():
    e=setup_zero();c=e.core
    assert c._next_boundary(1.)==pytest.approx(.5)
    c.advance(1.)
    assert len(e.segment_history)<=4
    before=[x for x in e.segment_history if x['start_minute']<.5-1e-8]
    after=[x for x in e.segment_history if x['start_minute']>=.5-1e-8]
    assert all(x['action']['boiler_steam_lbph']==0. and x['action']['boiler_auxiliary_kW']==0. for x in before)
    assert any(x['action']['boiler_steam_lbph']>1. and x['action']['boiler_auxiliary_kW']>.5 for x in after)
    assert all(x['action']['boiler_restart_electric_kW']==0. for x in e.segment_history)

def test_zero_restart_without_source_never_creates_heat_or_auxiliary_sink():
    e=setup_zero(no_source=True);c=e.core
    assert c._next_boundary(.5)>.001
    c.advance(.5)
    assert len(e.segment_history)<=3
    assert all(x['action']['boiler_steam_lbph']==0. and x['action']['boiler_restart_electric_kW']==0. for x in e.segment_history)
    assert not c.station.boiler_ready

def test_zero_restart_waits_for_actual_grid_connection_after_hardware_return():
    e=setup_zero(grid_restore=True);c=e.core
    assert c._next_boundary(1.)>.001
    c.advance(2.)
    supplied=[x for x in e.segment_history if x['action']['boiler_steam_lbph']>1.]
    assert supplied and supplied[0]['start_minute']==pytest.approx(1.5)
    assert all(x['action']['grid_import_kW']==0. and x['action']['boiler_steam_lbph']==0. for x in e.segment_history if x['start_minute']<1.5-1e-8)

@pytest.mark.parametrize('minutes',[1.,5.])
def test_positive_restart_durations_keep_their_paid_continuous_clock(minutes):
    from tools.model_correctness_v23.environment import decision_snapshot
    from tools.model_correctness_v23.policy import SciPolicy
    e=make(dict(model_closure_v22=dict(station=dict(boiler_restart_minutes=minutes))))
    s=scenario();s.update(fault_start_minute=0.,fault_duration_minutes=45.,faults=dict(GT_1=0.,GT_2=0.,GT_3=0.),
        model_closure_events=[dict(time_minute=0.,availability={'STATION_AC':0.}),dict(time_minute=.5,availability={'STATION_AC':1.})])
    e.reset(scenario=s,seed=9);request,_=SciPolicy('R',plant=e.plant).decide([decision_snapshot(e)])
    e.core.generate_command(request);e.core.advance(minutes+1.)
    actual=[x for x in e.segment_history if x['action']['boiler_steam_lbph']>1.]
    assert actual and actual[0]['start_minute']==pytest.approx(.5+minutes)
    paid=sum(x['action']['boiler_restart_electric_kW']*(x['end_minute']-x['start_minute'])/60. for x in e.segment_history)
    assert paid==pytest.approx(.5*minutes/60.,abs=1e-7)
