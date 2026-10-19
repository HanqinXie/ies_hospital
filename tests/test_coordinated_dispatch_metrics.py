"""Saved segment accounting only; no solver, environment or raw-file reads."""
import copy
import importlib
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def api():
    try:return importlib.import_module('tools.coordinated_dispatch.metrics').extract_metrics
    except ModuleNotFoundError:pytest.fail('Saved service/switch metrics are missing')


def segment(start,end,*,gt=5.,grid=0.,diesel=0.,failed=False,loss=None,temp=25.,violation=False):
    u=[[0.,0.,0.] for _ in range(4)] if loss is None else loss
    return dict(start_minute=start,end_minute=end,loads_kW=[[10.,10.,10.] for _ in range(4)],
        unserved_kW=u,action=dict(gt_electric_kW=[gt,0.,0.],grid_import_kW=grid,diesel_kW=diesel),
        source_start_gt_kW=[gt,0.,0.],source_end_gt_kW=[gt,0.,0.],
        pre_state=dict(diesel_status='CONNECTED'),availability=dict(GRID=float(grid>0.)),command_id=1,
        temperature=dict(P0=21.,P1=24.,P2=temp,P3=None),violation=dict(P0=False,P1=False,P2=violation,P3=False),
        classification=dict(p0_guarantee_failure=failed,material_unserved_by_carrier=dict(e=failed,h=False,c=failed)))


def payload(segments,decisions=None):
    observed=segments[-1]['end_minute'] if segments else 0.
    return dict(summary=dict(observed_until_minute=observed,requested_end_minute=600.,
        policy_id='test',status='completed' if observed==600. else 'partial'),segments=segments,
        decisions=decisions or [],initial_state=dict(state=dict(gt_prev=[5.,0.,0.],last_grid_import_kW=0.,last_diesel_kW=0.)))


def test_fractional_window_clipping_material_thresholds_and_p3_temperature_na():
    u=[[0.,0.,0.] for _ in range(4)];u[2]=[.02,.0005,0.];u[3]=[0.,0.,.005]
    affected=segment(58.25,61.75,loss=u,temp=31.,violation=True)
    affected['loads_kW'][3][2]=1.
    p=payload([segment(0.,58.25),affected,segment(61.75,600.)])
    r=api()(p,{'id':'fractional','analysis_class':'L2'})
    assert r['full']['all_registered']['service']['P2']['e']['total_minutes']==3.5
    a=r['primary']['all_registered']
    assert a['service']['P2']['e']['total_minutes']==1.75
    assert a['service']['P2']['e']['longest_minutes']==1.75
    assert a['service']['P2']['e']['ENS_kWh']==pytest.approx(.02*1.75/60.)
    assert a['service']['P2']['h']['total_minutes']==0.
    assert a['service']['P3']['c']['total_minutes']==1.75
    assert a['temperature']['P2']==dict(maximum_C=31.,violation_minutes=1.75)
    assert a['temperature']['P3'] is None


def test_actual_source_ports_gaps_recovery_and_recurrence_not_connected_status():
    segs=[segment(0.,60.),segment(60.,65.,gt=0.,failed=True),
        segment(65.,95.,gt=0.,diesel=3.),segment(95.,100.,gt=0.,failed=True),
        segment(100.,600.,gt=0.,grid=10.)]
    r=api()(payload(segs),{'id':'switch','analysis_class':'L4b'})
    p=r['primary']['all_registered'];rec=p['recovery']
    assert p['gaps']['zero_electric_supply']['total_minutes']==10.
    assert p['gaps']['P0_material_unserved']['episode_count']==2
    assert rec['first_P0_failure_minute']==60. and rec['first_recovery_minute']==65.
    assert rec['confirmed_30min_start']==65. and rec['confirmed_30min_minute']==95.
    assert rec['recurrence_count']==1 and rec['recurrence_after_30min_count']==1
    assert p['source_activations']['DIESEL']==1 and p['source_exits']['DIESEL']==1
    assert p['source_activations']['GRID']==1
    diesel=[x for x in r['source_transition_rows'] if x['device']=='DIESEL']
    assert [(x['time_minute'],x['kind']) for x in diesel]==[(65.,'activated'),(95.,'deactivated')]
    assert p['terminal_actual_sources_kW']['DIESEL']==0.
    assert p['terminal_P0_failure'] is False


def test_partial_prefix_is_retained_and_full_denominator_metrics_are_null():
    r=api()(payload([segment(0.,60.),segment(60.,65.,gt=0.,failed=True),
        segment(65.,80.,gt=0.,diesel=3.)]),{'id':'partial'})
    for window,minutes in (('primary',20.),('full',80.)):
        w=r[window];assert w['complete'] is False and w['all_registered'] is None
        assert w['observed_minutes']==minutes
        assert w['observed_prefix']['gaps']['P0_guarantee_failure']['total_minutes']==5.
        assert w['observed_prefix']['recovery']['confirmed_30min'] is None
        assert w['observed_prefix']['temperature']['P3'] is None
    bad=payload([segment(0.,60.),segment(65.,80.)])
    with pytest.raises(ValueError):api()(bad,{'id':'missing_interval'})


def test_planned_budget_is_issued_step_kwh_not_integrated_actual_ens():
    budget=dict(enabled=True,allowed_extra_kWh=[[1.,2.,3.],[4.,5.,6.]],
        used_extra_kWh=[[.5,2.,4.],[4.,6.,6.]],violation_kWh=[[0.,0.,1.],[0.,1.,0.]])
    def decision(t):
        return dict(decision_time_min=t,command_generated=dict(id=int(t),generated_minute=t,arrival_minute=t+10.,
            dispatch=dict(gt_electric_kW=[100.,0.,0.],grid_import_kW=850.,diesel_kW=50.),
            joint_dispatch=dict(diagnostic=dict(service_budget=copy.deepcopy(budget),coordination_preference='bridge_first'))))
    r=api()(payload([segment(0.,600.)],[decision(45.),decision(60.),decision(420.),decision(600.)]),{'id':'budget'})
    p=r['primary']['all_registered']['planned_budget'];f=r['full']['all_registered']['planned_budget']
    assert p['decision_count']==1 and p['allowed_extra_kWh']==budget['allowed_extra_kWh']
    assert p['used_extra_kWh']==budget['used_extra_kWh'] and p['violation_kWh']==budget['violation_kWh']
    assert f['decision_count']==3 and f['allowed_extra_kWh'][0][0]==3.
    assert r['primary']['all_registered']['service']['P2']['e']['ENS_kWh']==0.
    assert r['decision_rows'][1]['planned_DIESEL_kW']==50.
    no=api()(payload([segment(0.,600.)]),{'id':'legacy'})
    assert no['full']['all_registered']['planned_budget']['allowed_extra_kWh'] is None
