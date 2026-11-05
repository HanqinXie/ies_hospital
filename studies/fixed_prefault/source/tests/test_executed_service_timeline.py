"""Saved-payload lifecycle and interval checks; no environment or solver."""
import copy
import importlib
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def api():
    try:
        return importlib.import_module('tools.executed_service.timeline').extract_command_timeline
    except ModuleNotFoundError:
        pytest.fail('Actual command timeline helper is missing')


def command(cid, generated, eta):
    return dict(id=cid, generated_minute=generated, arrival_minute=eta,
                dispatch=dict(gt_electric_kW=[100., 0., 0.], grid_import_kW=0.,
                              diesel_kW=20., absorption_cooling_kW=0.))


def segment(a, b, cid, loss=12.):
    return dict(start_minute=a, end_minute=b, command_id=cid,
                action=dict(gt_electric_kW=[100., 0., 0.], grid_import_kW=0.,
                            diesel_kW=20., absorption_cooling_kW=0.),
                unserved_kW=[[0., 0., 0.], [0., 0., 0.], [loss, 0., 0.], [0., 0., 0.]])


def execute(cid, t, *, dg=20.):
    requested = command(cid, t, t)['dispatch']
    actual = copy.deepcopy(requested); actual['diesel_kW'] = dg
    return dict(kind='command_executed', time_minute=t, command_id=cid,
                requested_dispatch=requested, executed_dispatch=actual,
                reason='revalidated_against_current_capacity_and_ramp')


def payload(commands, segments, events, observed=30., requested=30., status='completed'):
    return dict(summary=dict(observed_until_minute=observed, requested_end_minute=requested,
                             status=status),
                decisions=[dict(command_generated=c) for c in commands],
                segments=segments, events=events)


def test_actual_ids_supersession_and_initial_state_are_separate_from_generation():
    cs=[command(1, 0., 10.), command(2, 5., 10.), command(3, 20., 20.)]
    events=[dict(kind='command_arrived', time_minute=10., command_id=i) for i in (1, 2)]
    events += [dict(kind='command_superseded', time_minute=10., command_id=1, by=2), execute(2, 10.),
               dict(kind='command_arrived', time_minute=20., command_id=3), execute(3, 20.)]
    p=payload(cs,[segment(0.,10.,None),segment(10.,20.,2),segment(20.,30.,3)],events)
    original=copy.deepcopy(p); r=api()(p)
    rows={q['command_id']:q for q in r['command_rows']}
    assert rows[1]['lifecycle_status']=='superseded_without_execution'
    assert rows[1]['superseded_by_command_id']==2 and rows[1]['actual_execution_minute'] is None
    assert rows[2]['generated_minute']==5. and rows[2]['actual_execution_minute']==10.
    assert rows[2]['observed_execution_minutes']==10.
    assert r['initial_unattributed_segments'][0]['interval_minutes']==[0.,10.]
    assert [(q['command_id'],q['start_minute'],q['observed_end_minute']) for q in r['execution_intervals']]==[(2,10.,20.),(3,20.,30.)]
    assert r['execution_intervals'][0]['observed_P2_P3_ENS_kWh'][0][0]==2.
    assert r['execution_intervals'][1]['end_reason']=='requested_observation_horizon'
    assert r['hard_execution_budget_claim'] is False and p==original


def test_unknown_tail_and_queued_eta_do_not_become_future_execution():
    cs=[command(1,0.,0.),command(2,15.,35.)]
    p=payload(cs,[segment(0.,20.,1)],[execute(1,0.)],20.,45.,'model_constraint_infeasible')
    r=api()(p); rows={q['command_id']:q for q in r['command_rows']}
    assert rows[2]['lifecycle_status']=='queued_not_arrived_in_observed_prefix'
    assert rows[2]['eventual_execution_status']=='unknown'
    assert rows[2]['actual_execution_minute'] is None
    last=r['execution_intervals'][0]
    assert last['end_minute'] is None and last['observed_end_minute']==20.
    assert last['right_censored'] is True and last['complete_interval_P2_P3_ENS_kWh'] is None
    assert r['unknown_tail_minutes']==[20.,45.]


def test_arrival_revalidation_and_later_clamp_keep_affected_command_and_order():
    cs=[command(1,0.,0.),command(2,15.,15.)]
    events=[execute(1,0.,dg=0.),
            dict(kind='forced_capacity_or_permission_clamp',time_minute=15.,before={'diesel_kW':20.},after={'diesel_kW':0.}),
            execute(2,15.)]
    p=payload(cs,[segment(0.,15.,1),segment(15.,30.,2)],events)
    r=api()(p); rows={q['command_id']:q for q in r['command_rows']}
    assert rows[1]['arrival_revalidation_changed'] is True
    assert rows[1]['arrival_source_delta_kW']['DIESEL']==-20.
    assert rows[1]['clamp_events'][0]['time_minute']==15.
    assert rows[1]['clamp_events'][0]['command_id_basis']=='preceding_execution_event_in_saved_event_order'
    assert rows[2]['clamp_events']==[]


def test_hole_preserved_and_interval_integral_unknown_instead_of_zero_filled():
    p=payload([command(1,0.,0.)],[segment(0.,5.,1),segment(10.,30.,1)],[execute(1,0.)])
    r=api()(p); q=r['execution_intervals'][0]
    assert q['coverage_holes_minutes']==[[5.,10.]] and q['coverage_complete'] is False
    assert q['observed_execution_minutes']==25.
    assert q['complete_interval_P2_P3_ENS_kWh'] is None


@pytest.mark.parametrize('bad', ['overlap','unknown_id','future_generation','duplicate_execution'])
def test_inconsistent_saved_evidence_is_rejected(bad):
    p=payload([command(1,0.,0.)],[segment(0.,30.,1)],[execute(1,0.)])
    if bad=='overlap':p['segments'].append(segment(20.,30.,1))
    if bad=='unknown_id':p['segments'][0]['command_id']=9
    if bad=='future_generation':p['decisions'][0]['command_generated']['generated_minute']=31.
    if bad=='duplicate_execution':p['events'].append(execute(1,1.))
    with pytest.raises(ValueError):api()(p)
