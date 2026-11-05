"""Tests for the V24 diagnostic gate and fixed-zone identification boundary."""
import importlib

import numpy as np
import pytest


def api():
    try:
        spec = importlib.util.find_spec('tools.engineering_completion_v24.thermal_diagnostics')
    except ModuleNotFoundError:
        spec = None
    assert spec is not None, 'V24 bounded thermal diagnostics are missing'
    return importlib.import_module('tools.engineering_completion_v24.thermal_diagnostics')


def test_weak_winter_success_cannot_open_summer_validation_gate():
    m = api()
    good = dict(event_rmse_C=.1, recovery_first6h_rmse_C=.1,
                maximum_absolute_error_C=.2, first_crossing_error_minutes=0.,
                recovery_time_error_minutes=10., complete_input_interval_minutes=10.)
    winter = dict(good, season='winter', case_id='weak')
    assert not m.calibration_gate([winter])['qualified_for_new_validation']
    summer = dict(good, season='summer', case_id='strong', maximum_actual_delta_C=4.)
    assert m.calibration_gate([winter, summer])['qualified_for_new_validation']
    summer['recovery_time_error_minutes'] = 390.
    assert not m.calibration_gate([winter, summer])['qualified_for_new_validation']


def test_censored_recovery_remains_failed_or_unassessed():
    m = api()
    row = dict(season='summer', case_id='censored', maximum_actual_delta_C=5.,
               event_rmse_C=.1, recovery_first6h_rmse_C=.1,
               maximum_absolute_error_C=.2, first_crossing_error_minutes=0.,
               recovery_time_error_minutes=None, complete_input_interval_minutes=10.)
    assert m.calibration_gate([row])['failed_or_unassessed_cases'] == ['censored']


def test_fixed_zone_fit_never_reads_development_validation_truth():
    m = api()
    from tools.model_correctness_v23.thermal_model import predict
    parameters = [.2, 3., .6, .12]
    q = np.r_[np.zeros(6), np.ones(12)*3., np.ones(120)*-.05]
    cases = []
    for hours in [2, 6]:
        cases.append(dict(case_id=f'cal_{hours}h', split='calibration', season='summer',
                          zone='zone', dt_h=1/6, forcing_kW=q,
                          actual_delta_C=predict(q, parameters, dt_h=1/6)[:, 0]))
    cases.append(dict(case_id='seen_old_test', split='development_validation',
                      zone='zone', forcing_kW=object(), actual_delta_C=object()))
    bounds = dict(zone=dict(lower=[.08, .7, .12, .02], upper=[.8, 16., 2.4, .3],
                            starts=[[.18, 4., .5, .08]]))
    result = m.fit_zone_models(cases, bounds)
    assert result['zone']['fit_case_ids'] == ['cal_2h', 'cal_6h']
    assert result['zone']['validation_used'] is False
    assert result['zone']['parameters'] == pytest.approx(parameters, rel=1e-4)


def test_multipliers_apply_only_to_heat_balance_system_term():
    m = api()
    import pandas as pd
    frame = pd.DataFrame(dict(internal_convective_kW=[1.], surface_convection_kW=[2.],
                              interzone_kW=[0.], outdoor_transfer_kW=[-.1],
                              system_air_kW=[-14.5], system_convective_kW=[0.],
                              air_storage_kW=[0.], heating_kW=[0.], cooling_kW=[2.9]))
    residual = m.heat_balance_residual(frame, multiplier=5.)
    assert residual.iloc[0] == pytest.approx(0., abs=1e-12)
    assert frame.cooling_kW.iloc[0] == 2.9


def test_node_instrumentation_appends_outputs_without_rewriting_physics():
    try:
        spec = importlib.util.find_spec('tools.engineering_completion_v24.thermal_nodes')
    except ModuleNotFoundError:
        spec = None
    assert spec is not None, 'Targeted node instrumentation is missing'
    module = importlib.import_module('tools.engineering_completion_v24.thermal_nodes')
    source = 'Coil:Cooling:Water,unchanged,Always_On,0.001;\n'
    derived = module.diagnostic_idf(source)
    assert derived.startswith(source)
    assert 'System Node Mass Flow Rate,Timestep;' in derived
    assert 'OR1_Flr_2 VAV Box Reheat Coil,Heating Coil Heating Rate,Timestep;' in derived
    assert all(line.startswith('Output:Variable,') for line in derived[len(source):].splitlines() if line)


def test_archived_eso_retains_fractional_hvac_minutes(tmp_path):
    m = api()
    source = tmp_path/'fractional.eso'
    source.write_text('10,1,OR1_FLR_2,Zone Air System Sensible Heating Rate [W] !Each Call\n'
                      '11,1,OR1_FLR_2,Zone Air System Sensible Cooling Rate [W] !Each Call\n'
                      'End of Data Dictionary\n'
                      '2,1,7,20,1,12,0.00,3.33,Thursday\n10,0\n11,3000\n'
                      '2,1,7,20,1,12,3.33,6.67,Thursday\n10,0\n11,6000\n'
                      'End of Data\n', encoding='utf-8')
    frame = m.read_detailed_eso(source)
    assert frame.dt_h.sum() == pytest.approx(6.67/60)
    assert frame.net_sensible_kW.tolist() == [-3., -6.]
    assert frame.index[0].second == 19
