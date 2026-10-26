"""Budget validation never launches an optimizer in tests."""
import copy,importlib
import pytest


def valid():
    from tools.model_correction_v26.temperature_inputs import ZONES
    return dict(maximum_actual_optimizer_fits=16,maximum_starts_per_zone_structure=2,maximum_nfev=200,
        structures=['baseline','surface'],parameter_order=['furniture_capacity_kWh_K','construction_capacity_kWh_K','total_air_solid_conductance_kW_K','furniture_fraction'],fit_outages_h=[2,6],fit_dates=['07-20'],objective_name='absolute_N_F2_F6_and_paired_delta2_delta6',
        bounds={z:dict(lower=[.7,3.,.1,.1],upper=[3.,12.,2.4,.9],starts=[[1.5,5.,.7,.5],[1.,6.,.6,.4]]) for z in ZONES})


def api():
    return importlib.import_module('tools.model_correction_v26.temperature_study')


def test_declared_plan_has_exactly_sixteen_calls_and_no_automatic_extra_start():
    assert api().validate_fit_registration(valid())==16
    p=valid();next(iter(p['bounds'].values()))['starts'].append([1.,5.,.5,.5])
    with pytest.raises(ValueError,match='two|2'):
        api().validate_fit_registration(p)


def test_fit_budget_does_not_allow_a_third_structure_or_larger_nfev():
    p=valid();p['structures'].append('another_surface')
    with pytest.raises(ValueError,match='structure'):
        api().validate_fit_registration(p)
    p=valid();p['maximum_nfev']=201
    with pytest.raises(ValueError,match='200|nfev'):
        api().validate_fit_registration(p)


def test_one_zone_vector_covers_the_same_two_registered_summer_events():
    p=valid();p['fit_outages_h']=[2]
    with pytest.raises(ValueError,match='2h|6h|events'):
        api().validate_fit_registration(p)


def test_objective_keeps_absolute_bias_that_would_cancel_from_paired_delta():
    r=api().fit_residuals([15.,15.],[15.,15.],[15.,15.],[20.,20.],[20.,20.],[20.,20.])
    assert r[:6].tolist()==[-5.]*6
    assert r[6:].tolist()==[0.]*4
