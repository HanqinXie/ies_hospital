"""No extra thermal body: source-based algebraic face and three-store tests."""
import importlib
import numpy as np
import pandas as pd
import pytest


def api():
    return importlib.import_module('tools.model_correction_v26.temperature_surface')


def test_massless_linear_face_has_analytic_conduction_convection_balance():
    r=api().inside_face(air_C=20.,bulk_C=26.,half_conductance_kW_K=2.,radiant_kW=0.,
                        area_m2=100.,law='constant',constant_conductance_kW_K=1.)
    assert r['face_C']==pytest.approx(24.)
    assert r['air_heat_kW']==pytest.approx(4.)
    assert r['body_heat_kW']==pytest.approx(-4.)
    assert r['algebraic_residual_kW']==pytest.approx(0.,abs=1e-12)


def test_absorbed_radiation_is_partitioned_not_turned_into_new_capacity():
    r=api().inside_face(air_C=20.,bulk_C=20.,half_conductance_kW_K=2.,radiant_kW=6.,
                        area_m2=100.,law='constant',constant_conductance_kW_K=1.)
    assert r['face_C']==pytest.approx(22.)
    assert r['air_heat_kW']==pytest.approx(2.)
    assert r['body_heat_kW']==pytest.approx(4.)
    assert r['air_heat_kW']+r['body_heat_kW']==pytest.approx(6.)
    assert 'capacity' not in r


@pytest.mark.parametrize('bulk,sign',[(30.,1.),(10.,-1.)])
def test_tarp_port_is_passive_and_constant_offset_invariant(bulk,sign):
    args=dict(air_C=20.,bulk_C=bulk,half_conductance_kW_K=2.,radiant_kW=0.,area_m2=100.,law='tarp',cosine_inward=0.,convective_scale=1.)
    r=api().inside_face(**args)
    s=api().inside_face(**dict(args,air_C=40.,bulk_C=bulk+20.))
    assert r['air_heat_kW']*sign>0
    assert r['air_heat_kW']==pytest.approx(s['air_heat_kW'],abs=1e-11)
    assert r['air_heat_kW']+r['body_heat_kW']==pytest.approx(0.,abs=1e-11)


def test_sky_and_outdoor_equal_temperature_have_no_free_boundary_heat():
    r=api().outside_face(bulk_C=20.,outdoor_C=20.,sky_C=20.,ground_C=20.,
                         half_conductance_kW_K=1.,area_m2=10.,emissivity=.9,
                         solar_absorptance=.7,solar_W_m2=0.,wind_m_s=0.,cosine_outward=0.)
    assert r['face_C']==pytest.approx(20.)
    assert r['body_heat_kW']==pytest.approx(0.,abs=1e-12)


def test_solar_input_closes_algebraic_exterior_balance():
    r=api().outside_face(bulk_C=20.,outdoor_C=20.,sky_C=20.,ground_C=20.,
                         half_conductance_kW_K=1.,area_m2=10.,emissivity=.9,
                         solar_absorptance=.7,solar_W_m2=100.,wind_m_s=0.,cosine_outward=0.)
    assert r['solar_heat_kW']==pytest.approx(.7)
    assert r['body_heat_kW']>0
    assert r['algebraic_residual_kW']==pytest.approx(0.,abs=1e-10)
    assert r['body_heat_kW']==pytest.approx(r['solar_heat_kW']+r['convection_into_face_kW']+r['longwave_into_face_kW'],abs=1e-10)


def geometry():
    return dict(air_capacity_kWh_K=.08,infiltration_design_m3_s=0.,
                surfaces=[dict(name='furniture',body_index=1,area_m2=1.,half_conductance_kW_K=2.,cosine_inward=0.,exterior=False,inside_thermal_absorptance=.9,inside_visible_absorptance=.7),
                          dict(name='construction',body_index=2,area_m2=2.,half_conductance_kW_K=3.,cosine_inward=0.,exterior=False,inside_thermal_absorptance=.9,inside_visible_absorptance=.7)])


def forcing(**kw):
    return dict(system_sensible_kW=0.,internal_convective_kW=0.,internal_radiant_kW=0.,internal_visible_kW=0.,outdoor_C=20.,sky_C=20.,ground_C=20.,wind_m_s=0.,solar_by_surface_W_m2={},**kw)


@pytest.mark.parametrize('structure',['baseline','surface'])
def test_three_states_unchanged_at_uniform_zero_source_equilibrium(structure):
    r=api().advance_three_stores([20.,20.,20.],[20.,20.,20.],parameters=[1.,4.,.6,.5],
        geometry=geometry(),forcing=forcing(),dt_h=1/6,structure=structure)
    np.testing.assert_allclose(r['state'],[20.,20.,20.],atol=1e-11)
    assert len(r['state'])==3


@pytest.mark.parametrize('structure',['baseline','surface'])
def test_air_bdf3_and_solid_ode_balance_keeps_numerical_history_explicit(structure):
    f=forcing();f['system_sensible_kW']=1.;f['internal_radiant_kW']=.2
    r=api().advance_three_stores([20.,20.,20.],[20.,20.,20.],parameters=[1.,4.,.6,.5],
        geometry=geometry(),forcing=f,dt_h=1/6,structure=structure)
    assert r['balance_residual_kWh']==pytest.approx(0.,abs=1e-9)
    assert r['actual_input_kWh']==pytest.approx(.2)
    assert r['numerical_air_history_correction_kWh']!=0
    assert r['actual_input_kWh']==pytest.approx(r['physical_store_energy_change_kWh']+r['numerical_air_history_correction_kWh'],abs=1e-9)


def test_future_weather_packet_is_rejected_by_current_step_interface():
    f=forcing();f.update(weather_record_end='2006-07-20 15:00',completed_standard_time='2006-07-20 14:30')
    with pytest.raises(ValueError,match='future|Future'):
        api().advance_three_stores([20.,20.,20.],[20.,20.,20.],parameters=[1.,4.,.6,.5],
            geometry=geometry(),forcing=f,dt_h=1/6,structure='surface')

