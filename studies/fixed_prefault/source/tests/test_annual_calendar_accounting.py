"""Calendar exposure is distinct from a realized service interruption."""
import copy
from pathlib import Path
import sys
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))


def implementation():
    from tools.annual_program import calendar_accounting
    return calendar_accounting


def event(t, **ports):
    return dict(time_minute=t, availability=ports)


def test_zero_event_random_year_is_retained_as_normal_physical_route():
    m=implementation();report=m.calendar_exposure([], horizon_minutes=525600.)
    assert report['positive_fault_exposure'] is False
    assert report['any_unavailable_minutes']==0.
    assert report['by_port']=={}
    assert m.literature_physical_route([])['mode']=='N'
    assert m.literature_physical_route([], demand_growth=True)['mode']=='D'
    with pytest.raises(ValueError,match='positive'):
        m.require_specified_fault([], horizon_minutes=525600.)


def test_partial_derating_and_joint_exposure_have_separate_units():
    m=implementation();events=[event(10., GRID=.5),event(20., TOWER=0.),
        event(25., TOWER=1.),event(30., GRID=1.)];before=copy.deepcopy(events)
    report=m.calendar_exposure(events,horizon_minutes=40.)
    assert events==before
    assert report['by_port']['GRID']['unavailable_minutes']==20.
    assert report['by_port']['GRID']['equivalent_full_outage_minutes']==10.
    assert report['by_port']['TOWER']['unavailable_minutes']==5.
    assert report['any_unavailable_minutes']==20.
    assert report['summed_asset_unavailable_minutes']==25.
    assert report['positive_fault_exposure'] is True
    assert report['service_failure_inferred'] is False
    assert m.require_specified_fault(events,horizon_minutes=40.)==report
    assert m.literature_physical_route(events,horizon_minutes=40.)['mode']=='F'
    assert m.literature_physical_route(events,horizon_minutes=40.,demand_growth=True)['mode']=='FD'


@pytest.mark.parametrize('events',[
    [event(10.,GRID=1.)],
    [event(10.,GRID=0.),event(10.,GRID=1.)],
    [event(30.,GRID=0.)],
    [event(31.,GRID=0.)],
])
def test_normal_zero_duration_and_out_of_horizon_calendar_are_not_positive_faults(events):
    m=implementation();report=m.calendar_exposure(events,horizon_minutes=30.)
    assert report['positive_fault_exposure'] is False
    assert report['any_unavailable_minutes']==0.
    assert report['availability_at_observation_end_left']['GRID']==1.
    with pytest.raises(ValueError,match='positive'):
        m.require_specified_fault(events,horizon_minutes=30.)
    # Review corrected the old assumption that zero exposure implied malformed
    # draws. Native event response clocks can still differ from N/D, so retain
    # every nonempty calendar on F/FD even when no fault is observed in-year.
    route=m.literature_physical_route(events,horizon_minutes=30.)
    assert route['mode']=='F'
    assert route['calendar']==events
    assert route['zero_observed_fault_exposure'] is True
    assert route['zero_event_random_year'] is False
    assert route['physical_prefix_reuse_automatically_authorized'] is False


def test_start_at_zero_and_unrepaired_year_end_are_observed_without_free_restore():
    report=implementation().calendar_exposure([event(0.,ABS=0.),event(40.,ABS=1.)],horizon_minutes=30.)
    assert report['by_port']['ABS']['unavailable_minutes']==30.
    assert report['availability_at_observation_end_left']=={'ABS':0.}
    assert report['unobserved_event_count']==1
    assert report['post_horizon_recovery_observed'] is False


@pytest.mark.parametrize('horizon',[0.,-1.,True,float('nan'),float('inf'),'30'])
def test_invalid_observation_horizon_is_rejected(horizon):
    with pytest.raises(ValueError):implementation().calendar_exposure([],horizon_minutes=horizon)


@pytest.mark.parametrize('events',[
    [event(-1.,GRID=0.)], [event(1.,FAKE_PORT=0.)],
    [event(10.,GRID=0.),event(9.,GRID=1.)], [event(1.,GRID=float('nan'))],
])
def test_native_port_clock_and_availability_validation_is_preserved(events):
    with pytest.raises(ValueError):implementation().calendar_exposure(events,horizon_minutes=30.)


def test_demand_flag_must_be_explicit_boolean():
    with pytest.raises(ValueError):implementation().literature_physical_route([],demand_growth=1)
