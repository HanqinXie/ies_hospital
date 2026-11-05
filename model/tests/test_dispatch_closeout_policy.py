import importlib
import sys
from pathlib import Path
import numpy as np
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))


def api():
    return importlib.import_module('tools.dispatch_closeout.policy')


def test_sustain_respects_energy_and_observed_downward_absorption():
    a=api()
    cap,details=a.diesel_preference(750.,240.,[500.,400.],support_hours=2.,downward_fraction=.85)
    assert cap==120.
    assert details['fuel_bound_kW']==120.
    cap,_=a.diesel_preference(750.,2400.,[500.,400.],support_hours=2.,downward_fraction=.85)
    assert cap==340.


def test_transfer_diesel_retains_electric_budget_without_unlicensed_grid():
    a=api()
    target,detail=a.allocate_targets([200.,200.,200.,0.,300.,0.],[300.]*3,[1.]*3,[0.]*3,
        grid_limit=0.,diesel_target=120.,ready_target=780.,minimum_fraction=.45)
    assert sum(target[:5])==pytest.approx(900.)
    assert target[3]==0.
    assert target[4]==120.
    assert min(target[:3])>=135.
    assert detail['request_is_not_execution']


def test_unavailable_gt_does_not_create_fuel_free_capacity():
    a=api()
    target,detail=a.allocate_targets([0.,0.,0.,0.,750.,0.],[0.]*3,[0.]*3,[15.]*3,
        grid_limit=0.,diesel_target=100.,ready_target=100.,minimum_fraction=.45)
    assert target[4]==750.
    assert detail['untransferable_diesel_kW']==650.


def test_hrsg_readiness_is_coupled_to_gt_power_not_independent_capacity():
    a=api()
    assert a.coupled_heat_kW([150.,0.,0.],[300.]*3,[3000.]*3,.3,.95)==pytest.approx(427.5)


def test_configuration_rejects_hidden_future_and_unknown_route():
    a=api()
    with pytest.raises(ValueError): a.StateDispatchPolicy({'future_outage_end':337.5},'x')
    with pytest.raises(ValueError): a.StateDispatchPolicy({'support_hours':0},'x')


def test_small_island_diesel_cannot_be_transferred_below_gt_stable_minimum():
    a=api()
    target,detail=a.allocate_targets([0.,0.,0.,0.,50.,0.],[300.]*3,[1.]*3,[0.]*3,
        grid_limit=0.,diesel_target=25.,ready_target=25.,minimum_fraction=.45)
    assert target==pytest.approx([0.,0.,0.,0.,50.,0.])
    assert detail['allocation_fallback'] is True
    assert detail['untransferable_diesel_kW']==25.
