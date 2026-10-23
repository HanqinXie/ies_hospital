from pathlib import Path
from datetime import datetime
import importlib
import sys
import numpy as np
import pandas as pd
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT),str(ROOT/'src')]


def api():
    try:
        return importlib.import_module('tools.sci_engineering.temperature_validation')
    except ModuleNotFoundError:
        pytest.fail('Temperature validation implementation is missing')


def test_targeted_intervention_leaves_other_cooling_coils_and_zone_loads_unchanged():
    source = ('Version,26.2;\nRunPeriod,Original,1,1,,12,31,,Sunday,No,No,No,Yes,Yes;\n'
        'Coil:Cooling:Water,VAV_OR Cooling Coil,Always_On,Autosize;\n'
        'Coil:Cooling:Water,VAV_ICU Cooling Coil,Always_On,Autosize;\n'
        'Branch,ORBranch,,\n    Coil:Cooling:Water,VAV_OR Cooling Coil,Inlet,Outlet;\n'
        'ElectricEquipment,OR1,OR1_Flr_2,Always_On,EquipmentLevel,123;\n')
    result = api().make_intervention_idf(source, month=6, day=15, outage_hours=2.)
    assert 'Coil:Cooling:Water,VAV_ICU Cooling Coil,Always_On,Autosize;' in result
    assert 'ElectricEquipment,OR1,OR1_Flr_2,Always_On,EquipmentLevel,123;' in result
    assert 'Branch,ORBranch,,\n    Coil:Cooling:Water,VAV_OR Cooling Coil,Inlet,Outlet;' in result
    assert 'SCI_OR_COOLING_AVAILABILITY' in result
    assert 'Until: 12:00' in result and 'Until: 14:00' in result


def test_surrogate_recovery_preserves_thermal_state_without_adding_free_cooling():
    value = api().predict_surrogate_delta(np.array([10.,10.,10.,10.]),
        np.array([1.,1.,0.,0.]), ua_kW_K=2., capacity_kWh_K=4., step_hours=.5)
    assert value[0] > 0 and value[1] > value[0]
    assert value[2] == pytest.approx(value[1] * np.exp(-.25))
    assert value[3] == pytest.approx(value[2] * np.exp(-.25))


def test_energyplus_timestep_hour_is_already_clock_hour_and_dst_shifts_schedule():
    assert api().sql_timestamp(6,15,0,10,0) == datetime(2006,6,15,0,10)
    assert api().sql_timestamp(6,15,11,10,1) == datetime(2006,6,15,12,10)
    assert api().sql_timestamp(6,15,12,0,1) == datetime(2006,6,15,13,0)
    assert api().sql_timestamp(1,15,12,0,0) == datetime(2006,1,15,12,0)


def test_validation_rejects_case_whose_target_coil_still_delivers_in_disabled_window():
    stamps = pd.date_range('2006-06-15 11:50', periods=5, freq='10min')
    base = pd.DataFrame({'temperature_C':21.,'cooling_kW':5.,
        'cooling_setpoint_C':21.,'airloop_coil_cooling_kW':5.},index=stamps)
    parameter = dict(assumed_UA_kW_K=1., effective_C_kWh_K=2.,
        mass_participation=.5, effective_U_W_m2K=1.5)
    with pytest.raises(ValueError,match='coil'):
        api().summarize_pair(dict(case_id='bad',split='validation',month=6,day=15,outage_hours=2.),
            base,base.copy(),parameter,label='test')
