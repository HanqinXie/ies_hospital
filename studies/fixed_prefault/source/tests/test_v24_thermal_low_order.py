"""Mathematical checks for the one preregistered V24 thermal structure test."""
import importlib

import numpy as np
import pytest


def api():
    try:
        spec = importlib.util.find_spec('tools.engineering_completion_v24.thermal_low_order')
    except ModuleNotFoundError:
        spec = None
    assert spec is not None, 'V24 passive air/furniture/construction model is missing'
    return importlib.import_module('tools.engineering_completion_v24.thermal_low_order')


def test_three_state_exact_heat_accounting_and_partition_invariance():
    m = api()
    parameters = [.08, 1., 5., .5, .2, .08]
    initial = np.array([3., 2., 1.])
    result = m.commit(initial, parameters, sensible_delta_kW=2., actual_extra_cooling_kW=.5, dt_h=1.)
    assert abs(result['balance_residual_kWh']) < 1e-11
    assert result['actual_extra_cooling_kWh'] == .5
    divided = initial
    for _ in range(12):
        divided = m.commit(divided, parameters, sensible_delta_kW=2.,
                           actual_extra_cooling_kW=.5, dt_h=1/12)['state']
    assert divided == pytest.approx(result['state'], abs=1e-11)


def test_three_state_has_two_storage_paths_and_never_clips_negative_state():
    m = api()
    parameters = [.08, 1., 5., .5, .2, .08]
    heat = m.predict([3.]*12, parameters, dt_h=1/6)
    assert 0 < heat[-1, 2] < heat[-1, 1] < heat[-1, 0]
    cooled = m.commit([0., 0., 0.], parameters, sensible_delta_kW=0.,
                      actual_extra_cooling_kW=5., dt_h=.5)
    assert np.all(cooled['state'] < 0)
    assert abs(cooled['balance_residual_kWh']) < 1e-11


def test_nonphysical_parameters_or_future_state_inputs_are_rejected():
    m = api()
    with pytest.raises(ValueError):
        m.predict([1.], [.08, 1., 5., -.5, .2, .08])
    with pytest.raises(TypeError):
        m.predict([1.], [.08, 1., 5., .5, .2, .08], future_true_wall_temperature=[24.])
