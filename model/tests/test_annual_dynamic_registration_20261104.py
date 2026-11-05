"""Pure registration/checkpoint integration; never construct an IES environment."""
from functools import partial
from pathlib import Path
import sys
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]

from tools.annual_program.registration import make_case,validate_case
from tools.annual_program.checkpoint import _fingerprint

REVISION='annual_responsive_dynamic_bridge_20261104.v1'

def test_new_annual_registration_keeps_declared_pump_and_lowers_only_functional_scene():
    declared=[dict(time_minute=60.,availability={'EC_1_PRIMARY_PUMP':0.}),dict(time_minute=120.,availability={'EC_1_PRIMARY_PUMP':1.})]
    case=make_case('M_ready',mode='F',duration_minutes=180.,calendar=declared,execution_revision=REVISION)
    assert case['execution_revision']['declared_calendar']==declared
    assert case['scenario']['model_closure_events']==[
        dict(time_minute=60.,availability={'EC_1':0.}),dict(time_minute=120.,availability={'EC_1':1.})]
    assert case['scenario']['step_minutes']==15.
    assert case['execution_revision']['physical_max_segment_minutes']==1.
    assert validate_case(case)==case

def test_new_normal_and_faulted_years_share_new_profile_without_implicit_U():
    normal=make_case('M_available',execution_revision=REVISION)
    assert normal['execution_revision']['variant']=='responsive'
    assert normal['execution_revision']['declared_calendar']==[]
    assert normal['annual_program']['parameters']['execution_revision']==REVISION
    assert normal['scenario']['duration_steps']==35040
    assert validate_case(normal)==normal

def test_legacy_annual_registration_stays_legacy_unless_opted_in():
    case=make_case('M_ready')
    assert 'execution_revision' not in case
    assert 'execution_revision' not in case['annual_program']['parameters']
    assert validate_case(case)==case

def test_registered_initialization_partial_has_stable_strict_fingerprint():
    from hospital_ies.model import solve_hour
    from tools.single_fault_fuel_20261102.fuel import FuelCurve
    import json,hashlib
    cfg=json.loads((ROOT/'configs/plant_configs.json').read_text('utf8'))
    performance=cfg['historical_pnnl_performance']
    nominal=cfg['source_equipment']['gas_turbines']['rated_electric_kW_each']
    pins=tuple((name,hashlib.sha256((ROOT/name).read_bytes()).hexdigest())
        for name in ('configs/plant_configs.json','src/hospital_ies/model.py'))
    first=FuelCurve(float(nominal),float(nominal),performance['gt_hhv_heat_rate_Btu_per_kWh_constant'],
        performance['gt_hhv_heat_rate_Btu_per_kWh_ln_coeff'],performance['gt_partload_correction_multiplier'],
        performance['gt_partload_correction_exponent'],pins).as_dict()
    second=dict(first,iso_scaled_kW=first['iso_scaled_kW']+1.)
    value=partial(solve_hour,sf2_fuel_config=first)
    assert _fingerprint(value)==_fingerprint(partial(solve_hour,sf2_fuel_config=first))
    assert _fingerprint(value)!=_fingerprint(partial(solve_hour,sf2_fuel_config=second))
    with pytest.raises((TypeError,ValueError)):_fingerprint(partial(sum,[1,2]))
