"""Source-backed algebra and typed path contracts; no hospital trajectory."""
from dataclasses import FrozenInstanceError, replace
import importlib
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ORACLE = ROOT/'tests/fixtures/pnnl_sf2_fuel_oracle.py'


def oracle():
    spec = importlib.util.spec_from_file_location('independent_pnnl_sf2_oracle', ORACLE)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def api():
    assert (ROOT/'tools/single_fault_fuel_20261102/fuel.py').is_file(), 'SF2 running fuel module missing'
    return importlib.import_module('tools.single_fault_fuel_20261102.fuel')


def curve():
    from hospital_ies.model import load_plant
    return api().FuelCurve.from_source(ROOT, load_plant(ROOT/'configs/plant_configs.json', 'doe_capacity_matched_exploratory'))


@pytest.mark.parametrize('temperature', [-10., 15., 30., 45.])
@pytest.mark.parametrize('power', [0., 1., 128., 192., 256., 320.])
def test_curve_uses_original_iso800_log_scaled_iso320_denominator_and_9851(power, temperature):
    assert curve().rate(power, temperature) == pytest.approx(oracle().running_fuel_rate(power, temperature), rel=2e-15, abs=1e-15)


@pytest.mark.parametrize('p0,p1', [(0.,128.), (128.,0.), (128.,320.), (320.,128.), (200.,200.+1e-8), (0.,0.)])
def test_exact_linear_ramp_integral_matches_oracle_and_subdivision(p0, p1):
    c=curve(); hours=.25; mid=(p0+p1)/2.
    value=c.integral(p0,p1,hours,30.)
    assert value == pytest.approx(oracle().ramp_fuel_integral(p0,p1,hours,30.),rel=2e-14,abs=1e-15)
    assert value == pytest.approx(c.integral(p0,mid,hours/2.,30.)+c.integral(mid,p1,hours/2.,30.),rel=2e-14,abs=1e-15)


def test_per_unit_sum_is_not_one_curve_of_total_and_mean_rate_is_not_ramp_integral():
    c=curve()
    assert c.rate(128.,15.)+c.rate(128.,15.) != pytest.approx(c.rate(256.,15.))
    assert c.integral(128.,320.,.25,15.) != pytest.approx(c.rate(224.,15.)*.25, rel=1e-5)


def test_typed_path_is_frozen_bound_to_curve_and_exact_start_end_mean_duration():
    c=curve(); p=api().FuelPath.build(c, role='actual_interval', start_minute=15.,end_minute=16.,
        temperature_C=30.,start_kW=(128.,0.,256.),end_kW=(130.,0.,250.))
    assert p.mean_kW == (129.,0.,253.) and p.hours == 1./60.
    assert p.curve_identity == c.identity and len(p.identity)==64
    with pytest.raises(FrozenInstanceError): p.role='point_probe'
    with pytest.raises(ValueError): replace(p,mean_kW=(130.,0.,253.))
    assert p.integrals(c)[0] == c.integral(128.,130.,1./60.,30.)


@pytest.mark.parametrize('bad', [-1.,float('nan'),float('inf'),True,'128'])
def test_curve_rejects_invalid_physical_numbers(bad):
    with pytest.raises(ValueError): curve().rate(bad,15.)


def test_unknown_startup_idle_and_transient_fuel_are_not_zero_filled():
    scope=curve().qualification()
    assert scope['startup_additional_fuel_MMBtu'] is None
    assert scope['OFF_pilot_fuel_MMBtu_h'] is None
    assert scope['OEM_transient_qualified'] is False


@pytest.mark.parametrize('field,value',[('start_minute',True),('end_minute','16'),('temperature_C',True),('start_kW',(True,0.,0.))])
def test_path_builder_does_not_coerce_untyped_physical_values(field,value):
    args=dict(role='actual_interval',start_minute=15.,end_minute=16.,temperature_C=15.,
        start_kW=(128.,0.,0.),end_kW=(128.,0.,0.))
    args[field]=value
    with pytest.raises(ValueError):api().FuelPath.build(curve(),**args)


def test_independent_oracle_is_portable_repository_fixture_with_original_review_pin():
    import hashlib,json
    assert ORACLE==ROOT/'tests/fixtures/pnnl_sf2_fuel_oracle.py'
    assert hashlib.sha256(ORACLE.read_bytes()).hexdigest()=='0cab16e27d61bdebc0f1be63a87ef738543a35a72dfec5b5ce575fed63ba0073'
    provenance=json.loads(ORACLE.with_suffix('.provenance.json').read_text('utf8'))
    assert provenance['reviewed_oracle_sha256']=='0cab16e27d61bdebc0f1be63a87ef738543a35a72dfec5b5ce575fed63ba0073'
