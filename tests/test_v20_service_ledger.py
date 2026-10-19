from pathlib import Path
import sys
import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]

def payload(observed=60.):
    rows=[]
    for a,b,load,loss,cut in [(0.,30.,10.,3.,2.),(30.,60.,20.,5.,0.)]:
        x=np.zeros((4,3));x[0,0]=load
        u=np.zeros((4,3));u[0,0]=loss
        d=np.zeros((4,3));d[0,0]=cut
        rows.append(dict(start_minute=a,end_minute=b,loads_kW=x.tolist(),unserved_kW=u.tolist(),
            disconnected_loads_kW=d.tolist(),connected_loads_kW=(x-d).tolist(),
            action=dict(grid_import_kW=load-loss),
            pre_state=dict(diesel_energy_remaining_kWh=100-a),
            post_state=dict(diesel_energy_remaining_kWh=100-b)))
    return dict(segments=rows,summary=dict(observed_until_minute=observed),events=[])

def api():
    from tools.load_accounting.service_ledger import service_ledger
    return service_ledger

def test_exact_piecewise_requested_delivered_loss_decomposition():
    v=api()(payload(),0.,60.)
    s=v['all_registered']['P0']['e']
    assert s['requested_kWh']==pytest.approx(15.)
    assert s['delivered_kWh']==pytest.approx(11.)
    assert s['ENS_kWh']==pytest.approx(4.)
    assert s['disconnected_ENS_kWh']==pytest.approx(1.)
    assert s['connected_unserved_ENS_kWh']==pytest.approx(3.)
    assert v['DG_budget_start_kWh_equivalent']==100.
    assert v['DG_budget_finish_kWh_equivalent']==40.

def test_partial_window_is_integrated_at_actual_boundaries():
    v=api()(payload(),15.,45.)
    s=v['all_registered']['P0']['e']
    assert s['requested_kWh']==pytest.approx(7.5)
    assert s['delivered_kWh']==pytest.approx(5.5)
    assert s['ENS_kWh']==pytest.approx(2.)
    assert v['DG_budget_start_kWh_equivalent'] is None
    assert v['DG_budget_finish_kWh_equivalent'] is None

def test_unknown_tail_is_not_zero_service_or_full_result():
    v=api()(payload(observed=30.),0.,60.)
    assert not v['complete']
    assert v['all_registered'] is None
    assert v['unknown_tail_minutes']==[30.,60.]
    assert v['observed_prefix']['P0']['e']['requested_kWh']==pytest.approx(5.)

def test_inconsistent_disconnection_is_rejected():
    p=payload();p['segments'][0]['disconnected_loads_kW'][0][0]=50.
    with pytest.raises(ValueError):api()(p,0.,60.)

def test_partial_gt_ramp_uses_saved_linear_endpoints():
    p=payload();s=p['segments'][0]
    s['action']['gt_electric_kW']=[50.,0.,0.]
    s['source_start_gt_kW']=[0.,0.,0.]
    s['source_end_gt_kW']=[100.,0.,0.]
    v=api()(p,0.,15.)
    assert v['actual_source_integrals']['GT_electric_kWh']['value']==pytest.approx(6.25)
    assert v['actual_source_integrals']['GRID_electric_kWh']['value'] is None

@pytest.mark.parametrize('field,value',[('start_minute',float('nan')),('end_minute',float('nan')),
    ('start_minute',float('inf')),('end_minute',-1.)])
def test_nonfinite_or_reversed_recorded_interval_is_rejected(field,value):
    p=payload();p['segments'][0][field]=value
    with pytest.raises(ValueError):api()(p,0.,60.)
