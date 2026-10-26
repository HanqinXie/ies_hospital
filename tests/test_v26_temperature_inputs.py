"""Fixed original material inventory and causally completed weather tests."""
import importlib
import numpy as np
import pandas as pd
import pytest


def api():
    return importlib.import_module('tools.model_correction_v26.temperature_inputs')


def test_material_capacity_excludes_no_mass_and_sums_original_resistances():
    text='Material,Wood,Smooth,0.15,0.12,540,1210; Material:NoMass,Insulation,Smooth,0.8; Construction,Wall,Wood,Insulation;'
    c=api().construction_inventory(text)['WALL']
    assert c['resistance_m2K_W']==pytest.approx(2.05)
    assert c['capacity_J_m2K']==pytest.approx(.15*540*1210)
    assert c['outside_solar_absorptance']==.7
    assert c['inside_thermal_absorptance']==.9


def test_unused_non_or_glazing_does_not_block_known_opaque_inventory():
    text='Material,Wood,Smooth,0.15,0.12,540,1210; Construction,Wall,Wood; WindowMaterial:SimpleGlazingSystem,Glass,2.0,0.5; Construction,UnusedWindow,Glass;'
    inventory=api().construction_inventory(text)
    assert inventory['WALL']['capacity_J_m2K']==pytest.approx(.15*540*1210)
    assert inventory['UNUSEDWINDOW']['unsupported_materials']==['Glass']


def test_global_sql_azimuth_is_not_guessed_from_north_name():
    n=api().global_normal(270.,90.)
    np.testing.assert_allclose(n,[-1.,0.,0.],atol=1e-12)


def test_current_weather_never_interpolates_or_reads_future_hour():
    weather=pd.DataFrame(dict(record_end=pd.to_datetime(['2006-07-20 09:00','2006-07-20 10:00']),outdoor_C=[20.,50.],horizontal_ir_W_m2=[350.,500.],global_horizontal_W_m2=[100.,900.],direct_normal_W_m2=[50.,1000.],diffuse_horizontal_W_m2=[50.,100.],wind_m_s=[1.,9.]))
    r=api().completed_weather(weather,pd.Timestamp('2006-07-20 09:30'))
    assert r['outdoor_C']==20.
    assert r['record_end']==pd.Timestamp('2006-07-20 09:00')
    assert r['record_end']<=pd.Timestamp('2006-07-20 09:30')


def test_missing_ir_fails_instead_of_inventing_sky_heat():
    with pytest.raises(ValueError,match='infrared|IR'):
        api().sky_temperature_C(9999.)


def test_solar_vector_noon_equator_equinox_and_projection_sign():
    n=api().solar_vector(pd.Timestamp('2006-03-21 12:00'),latitude_deg=0.,longitude_deg=0.,timezone_h=0.)
    assert n[2]>.99
    assert np.linalg.norm(n)==pytest.approx(1.)
    w=dict(direct_normal_W_m2=800.,diffuse_horizontal_W_m2=0.,global_horizontal_W_m2=800.)
    assert api().incident_solar(w,[0.,0.,1.],n)>.99*800
    assert api().incident_solar(w,[0.,0.,-1.],n)==pytest.approx(.2*800)
