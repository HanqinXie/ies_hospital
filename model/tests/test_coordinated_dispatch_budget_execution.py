"""Synthetic saved-payload budget deviation checks; no environment or solver."""
import copy
import importlib
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def api():
    try:return importlib.import_module('tools.coordinated_dispatch.budget_execution').extract_executed_budget
    except ModuleNotFoundError:pytest.fail('Executed budget deviation helper is missing')


def command(t=60.,reference=None,allowed=None):
    reference=reference if reference is not None else [[4.,0.,2.],[0.,2.,0.]]
    allowed=allowed if allowed is not None else [[.2,0.,.1],[0.,.1,0.]]
    return {'command_generated':{'id':1,'generated_minute':t,'joint_dispatch':{'diagnostic':{
        'service_reference':{'unserved_kW':[[0.,0.,0.],[0.,0.,0.]]+reference},
        'service_budget':{'enabled':True,'allowed_extra_kWh':allowed,
            'violation_kWh':[[.03,0.,0.],[0.,0.,0.]]}}}}}


def segment(a,b,loss):
    return dict(start_minute=a,end_minute=b,command_id=999,
                unserved_kW=[[0.,0.,0.],[0.,0.,0.]]+loss)


def test_fractional_step_actual_integral_reference_units_and_generation_window():
    c=command(58.25);loss=[[8.,0.,4.],[0.,4.,0.]]
    p=dict(summary={'observed_until_minute':80.},decisions=[c],segments=[
        segment(58.25,60.,loss),segment(60.,65.,loss),segment(65.,73.25,loss)])
    before=copy.deepcopy(p);r=api()(p,{'step_minutes':15.})
    q=r['decision_rows'][0]
    assert q['status']=='complete' and q['complete'] is True
    assert q['interval_minutes']==[58.25,73.25]
    assert q['reference_kWh']==[[1.,0.,.5],[0.,.5,0.]]
    assert q['target_kWh']==[[1.2,0.,.6],[0.,.6,0.]]
    assert q['actual_all_ENS_kWh']==[[2.,0.,1.],[0.,1.,0.]]
    assert q['actual_all_excess_kWh'][0]==pytest.approx([.8,0.,.4])
    assert q['actual_all_excess_kWh'][1]==pytest.approx([0.,.4,0.])
    assert q['planned_slack_kWh'][0][0]==.03
    assert r['primary']['decision_count']==0 and r['full']['decision_count']==1
    assert r['full']['attribution']=='generation_cohort_full_step_intervals_not_score_window_ENS'
    assert p==before


def test_partial_interval_preserves_prefix_and_whole_target_without_tail_fill():
    p=dict(summary={'observed_until_minute':65.},decisions=[command(60.,[[4.,0.,0.],[0.,0.,0.]],[[0.,0.,0.],[0.,0.,0.]])],
           segments=[segment(60.,65.,[[12.,0.,0.],[0.,0.,0.]])])
    r=api()(p,{'step_minutes':15.});q=r['decision_rows'][0]
    assert q['status']=='partial' and q['observed_minutes']==5.
    assert q['target_kWh'][0][0]==1.
    assert q['actual_ENS_observed_prefix_kWh'][0][0]==1.
    assert q['observed_prefix_target_kWh'][0][0]==pytest.approx(1/3)
    assert q['observed_prefix_excess_kWh'][0][0]==pytest.approx(2/3)
    assert q['actual_all_ENS_kWh'] is None and q['actual_all_excess_kWh'] is None
    assert r['full']['all_registered_excess_kWh'] is None
    assert r['full']['unknown_decision_ids']==[1]


def test_legacy_is_not_applicable_and_future_generation_and_invalid_step_rejected():
    p=dict(summary={'observed_until_minute':65.},decisions=[{'command_generated':{'id':3,'generated_minute':60.}}],segments=[])
    r=api()(p,{'step_minutes':15.});q=r['decision_rows'][0]
    assert q['status']=='not_applicable' and q['reference_kWh'] is None
    assert q['actual_all_excess_kWh'] is None
    assert r['full']['applicable_count']==0 and r['full']['all_registered_excess_kWh'] is None
    p['decisions']=[command(65.01)]
    with pytest.raises(ValueError,match='generated.*observed'):api()(p,{'step_minutes':15.})
    with pytest.raises(ValueError,match='step_minutes'):api()(p,{'step_minutes':0.})
