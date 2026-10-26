"""V26 observer algebra: no change to EnergyPlus or supply physics."""
import importlib
import math

import numpy as np
import pytest


def contract():
    try:
        return importlib.import_module('tools.model_correction_v26.cold_contract')
    except ModuleNotFoundError:
        pytest.fail('V26 signed cold-water observation contract is not implemented')


def test_halfloop_report_mean_is_not_end_inventory_and_storage_includes_pump_once():
    m=contract()
    result=m.tank_step(7., 20., 2., 100., 1000., 300., 4200.)
    expected=20.+100./8400.+(7.-20.-100./8400.)*math.exp(-.6)
    assert result['endpoint_C']==pytest.approx(expected)
    assert result['mean_C']!=pytest.approx(result['endpoint_C'],abs=.1)
    assert result['storage_W']==pytest.approx(result['transport_W']+100.)
    observed=m.reconstruct_tank(result['mean_C'],20.,2.,100.,1000.,300.,lambda _:4200.)
    assert observed['start_C']==pytest.approx(7.)
    assert observed['endpoint_C']==pytest.approx(expected)
    assert observed['endpoint_reconstructed'] is True


def test_zero_flow_has_only_finite_pump_heating_and_no_transport():
    m=contract()
    result=m.tank_step(10.,80.,0.,200.,1000.,60.,4000.)
    assert result['endpoint_C']==pytest.approx(10.003)
    assert result['mean_C']==pytest.approx(10.0015)
    assert result['transport_W']==0.
    assert result['storage_W']==pytest.approx(200.)
    observed=m.reconstruct_tank(result['mean_C'],80.,0.,200.,1000.,60.,lambda _:4000.)
    assert observed['start_C']==pytest.approx(10.)


def test_hx_native_positive_cools_api_supply_and_two_water_sides_cancel():
    m=contract()
    result=m.hx_water_heat(3.,4200.,20.,10.,2.,4100.,7.,17.24390243902439)
    assert result['supply_water_gain_W']==pytest.approx(-126000.)
    assert result['demand_water_gain_W']==pytest.approx(84000.)
    assert result['two_side_residual_W']==pytest.approx(-42000.)
    # A conserved Ideal HX is constrained by the smaller capacity rate.
    q=8200.*13.
    result=m.hx_water_heat(3.,4200.,20.,20.-q/12600.,2.,4100.,7.,7.+q/8200.)
    assert result['supply_water_gain_W']==pytest.approx(-q)
    assert result['demand_water_gain_W']==pytest.approx(q)
    assert abs(result['two_side_residual_W'])<1e-9


def test_hx_zero_flow_cannot_make_heat_and_nan_never_becomes_zero():
    m=contract()
    result=m.hx_water_heat(0.,4200.,20.,20.,0.,4200.,7.,7.)
    assert result['two_side_residual_W']==0.
    # Distinct stale node temperatures at no flow do not imply heat transfer.
    # The earlier implementation rejected ΔT itself; test native Q instead.
    stale=m.hx_water_heat(0.,4200.,20.,10.,2.,4200.,7.,7.,native_heat_W=100.)
    assert stale['supply_water_gain_W']==0.
    assert stale['zero_flow_native_nonzero'] is True
    with pytest.raises(ValueError):
        m.tank_step(np.nan,20.,1.,0.,1000.,60.,4200.)


def test_residual_keeps_signed_absolute_and_peak_without_net_cooling_denominator():
    m=contract()
    result=m.residual_summary([1000.,-1000.],[.25,.25],[[2000.,3000.],[-2000.,-3000.]],absolute_floor_kWh=.01)
    assert result['signed_residual_kWh']==0.
    assert result['absolute_residual_kWh']==pytest.approx(.5)
    assert result['maximum_substep_residual_W']==1000.
    assert result['exchange_scale_kWh']==pytest.approx(2.5)
    assert result['relative_absolute_residual']==pytest.approx(.2)


def test_variable_cp_endpoints_are_reconstructed_from_mean_without_using_storage_output():
    m=contract()
    cp=lambda t:4180.+.2*t
    original=m.tank_step(8.,25.,4.,350.,2500.,137.123456789,cp(8.))
    result=m.reconstruct_tank(original['mean_C'],25.,4.,350.,2500.,137.123456789,cp)
    assert result['start_C']==pytest.approx(8.,abs=1e-10)
    assert result['storage_W']==pytest.approx(original['storage_W'],abs=1e-7)


@pytest.mark.parametrize('floor',[np.nan,np.inf,0.,-1.])
def test_nonfinite_or_nonpositive_residual_scale_floor_is_rejected(floor):
    with pytest.raises(ValueError):
        contract().residual_summary([0.],[1/6],[[0.]],absolute_floor_kWh=floor)
