"""Source-derived BDF3 air histories, never invented first-difference closure."""
import importlib
import numpy as np
import pandas as pd
import pytest


def api():
    return importlib.import_module('tools.model_correction_v26.temperature_air_balance')


def frame(groups):
    rows=[]
    for bucket,values in enumerate(groups):
        for value in values:
            rows.append(dict(bucket_end=pd.Timestamp('2006-07-20')+pd.Timedelta(minutes=10*(bucket+1)),dt_h=1/6/len(values),temperature_C=value))
    return pd.DataFrame(rows)


def test_unknown_initial_air_histories_remain_unknown_not_zero():
    r=api().reconstruct_air_histories(frame([[20.],[21.],[22.],[23.]]))
    assert not r.iloc[:3].history_complete.any()
    assert np.isnan(r.ZTM2_C.iloc[0])
    assert r.history_complete.iloc[3]
    assert r[['ZTM0_C','ZTM1_C','ZTM2_C']].iloc[3].tolist()==[22.,21.,20.]


def test_shorten_resets_from_zone_means_not_last_system_endpoints():
    r=api().reconstruct_air_histories(frame([[0.],[1.],[2.],[3.],[4.,8.],[10.,12.,14.]]))
    row=r.iloc[6]  # first system substep after count 2 -> 3
    assert row.history_source=='zone_history_downinterpolated'
    assert row.ZTM0_C==6.
    assert row.ZTM1_C==5.
    assert row.ZTM2_C==4.


def test_unchanged_system_count_preserves_substep_history():
    r=api().reconstruct_air_histories(frame([[0.],[1.],[2.],[3.],[4.,8.],[10.,12.]]))
    row=r.iloc[6]
    assert row.history_source=='previous_system_history'
    assert row[['ZTM0_C','ZTM1_C','ZTM2_C']].tolist()==[8.,4.,3.]


def test_bdf3_storage_is_exact_for_a_quadratic_and_differs_from_report():
    # Unit capacity, dt=1h, T=t^2, at t=3h: BDF3 gives dT/dt=6 K/h.
    storage=api().air_storage_rate(1.,1.,9.,[4.,1.,0.],algorithm='ThirdOrder')
    assert storage.solver_storage_kW==pytest.approx(6.)
    assert storage.native_report_storage_kW==pytest.approx(5.)
    assert storage.first_difference_is_solver_storage is False


def test_air_storage_retains_unknown_history():
    storage=api().air_storage_rate(1.,1.,9.,[4.,np.nan,0.],algorithm='ThirdOrder')
    assert np.isnan(storage.solver_storage_kW)
    assert storage.native_report_storage_kW==5.


def test_analytical_native_storage_is_coefficient_residual_not_first_difference():
    # Exact source AnalyticalSolution branch: TempIndCoef-TempDepCoef*MAT.
    # With those endpoint coefficients the rate is4kW, not C*(16-10)/dt=6kW.
    storage=api().air_storage_rate(1.,1.,16.,[10.],algorithm='AnalyticalSolution',
                                  temperature_independent_kW=20.,temperature_dependent_kW_K=1.)
    assert storage.solver_storage_kW==4.
    assert storage.native_report_storage_kW==4.
    assert storage.first_difference_is_solver_storage is False


def test_analytical_algorithm_requires_its_actual_coefficients():
    with pytest.raises(ValueError,match='coefficients'):
        api().air_storage_rate(1.,1.,16.,[10.],algorithm='AnalyticalSolution')


def test_exact_substep_durations_are_preserved_and_malformed_bucket_rejected():
    f=frame([[20.,21.,22.]])
    r=api().reconstruct_air_histories(f)
    np.testing.assert_array_equal(r.dt_h.to_numpy(),f.dt_h.to_numpy())
    f.loc[1,'dt_h']+=1e-5
    with pytest.raises(ValueError,match='uniform|complete'):
        api().reconstruct_air_histories(f)
