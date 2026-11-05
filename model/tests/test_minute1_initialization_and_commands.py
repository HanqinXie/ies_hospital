from types import SimpleNamespace
import pandas as pd
import numpy as np
import pytest
from hospital_ies.control_env import HospitalIESEnv


def test_minute_initialization_uses_current_native_row_and_group_without_hourly_lookup():
    class Source:
        def at_elapsed(self, minute, start_hour):
            assert (minute,start_hour)==(0.,33)
            return 33,SimpleNamespace(minute_of_year=1921,hour_of_year=33,Q_e_non_hvac_kW=10.),{'P0_e_kW':7.}
    env=HospitalIESEnv.__new__(HospitalIESEnv);env.minute_source=Source()
    env.hourly=object();env.groups=object()
    assert hasattr(env,'_initialization_inputs'),'Explicit minute initialization is missing'
    base,warmup,groups=env._initialization_inputs(33)
    assert base.Q_e_non_hvac_kW==10. and warmup.minute_of_year==1921
    assert groups=={'P0_e_kW':7.}
    assert env.minute1_initialization_reference['reference_minute_of_year']==1921
    assert env.minute1_initialization_reference['static_solve_dt_hours']==.25


def test_old_initialization_rows_and_groups_keep_original_types_and_values():
    env=HospitalIESEnv.__new__(HospitalIESEnv)
    env.hourly=pd.DataFrame([{'hour_of_year':1,'Q_e_non_hvac_kW':10.}])
    env.groups=pd.DataFrame([{'P0_e_kW':7.}],index=[1])
    assert hasattr(env,'_initialization_inputs'),'Explicit minute initialization is missing'
    base,warmup,groups=env._initialization_inputs(1)
    assert hasattr(base,'_asdict') and hasattr(warmup,'_asdict')
    assert base==next(env.hourly.itertuples(index=False)) and warmup==next(env.hourly.itertuples(index=False))
    assert groups=={'P0_e_kW':7.}


def test_default_upper_command_hook_delegates_exactly_once_without_changing_action():
    calls=[];command={'id':'original'}
    def generate(action):calls.append(action);return command
    env=HospitalIESEnv.__new__(HospitalIESEnv);env.core=SimpleNamespace(generate_command=generate)
    assert hasattr(env,'_issue_upper_command'),'Explicit command issue hook is missing'
    request={'targets':{'gt_1':10.}}
    assert env._issue_upper_command(request) is command
    assert calls==[request] and calls[0] is request


@pytest.mark.parametrize('minute,expected',[(.25,3.),(1.25,1.8)])
def test_controller_snapshot_thermal_margin_uses_current_native_minute(monkeypatch,minute,expected):
    from hospital_ies import data_contract as api
    calls=[]
    class Source:
        def thermal_at_elapsed(self,t,start_hour):
            calls.append((t,start_hour))
            return {'zone':SimpleNamespace(thermostat_cooling_setpoint_C=25.,
                zone_mean_air_temperature_C=23.6 if t<1. else 24.8)}
    old={'zone':SimpleNamespace(thermostat_cooling_setpoint_C=24.,zone_mean_air_temperature_C=22.)}
    state=SimpleNamespace(last_diesel_kW=0.,diesel_energy_remaining_kWh=10.,diesel_status='OFF',
        topology_mode='normal',gt_prev=np.zeros(3),overload_minutes=np.zeros(3),
        disconnected_loads=np.zeros((4,3)),thermal_delta={'zone':.4})
    obs=dict(time_minute=minute,grid_outage_elapsed_minutes=0.,hour_of_year=33,outdoor_temperature_C=20.,
        grid_import_limit_kW=1.,arrived_grid_import_limit_kW=1.,grid_import_prev_kW=0.,
        control_available=True,loads_kW=np.zeros((4,3)))
    env=SimpleNamespace(minute_source=Source(),thermal_cache=({}, {33:old}),
        state=state,config={'thermal_setpoint_margin_C':2.},scenario={'start_hour':33},
        core=SimpleNamespace(observe=lambda:obs,pending_commands=[],active_command=None))
    monkeypatch.setattr(api,'graph_view',lambda *_:{'nodes':[]})
    snapshot=api.decision_snapshot(env)
    assert snapshot['features']['zone_margin_zone']==pytest.approx(expected)
    assert snapshot['features']['zone_delta_zone']==.4
    assert snapshot['feature_metadata']['zone_margin_zone']['available_time_min']==minute
    assert snapshot['feature_metadata']['outdoor_temperature_C']['source']=='native minute boundary'
    assert calls==[(minute,33)]
