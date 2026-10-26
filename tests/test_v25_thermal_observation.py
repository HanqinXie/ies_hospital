"""V25 regression: preserve actual endpoint reporting and distinct clocks."""
import importlib
import importlib.util

import numpy as np
import pandas as pd
import pytest


def module():
    name = 'tools.model_correction_v25.thermal_observation'
    assert importlib.util.find_spec(name) is not None, 'V25 observation contract is missing'
    return importlib.import_module(name)


def detail(temperatures, durations, bucket='2006-07-20T12:10:00'):
    return pd.DataFrame(dict(bucket_end=pd.to_datetime([bucket]*len(durations)),
                             dt_h=np.asarray(durations)/60,
                             temperature_C=temperatures,
                             net_sensible_kW=np.arange(len(durations), dtype=float)))


def test_single_substep_report_is_endpoint_not_continuous_mean():
    result = module().aggregate_completed(detail([24.], [10.]))
    assert result.temperature_C.iloc[0] == 24.
    assert result.endpoint_C.iloc[0] == 24.


def test_nonuniform_fast_rise_and_fall_duration_weighted_report():
    result = module().aggregate_completed(detail([20., 30., 10.], [1., 2., 7.]))
    assert result.temperature_C.iloc[0] == pytest.approx(15.)
    assert result.endpoint_C.iloc[0] == 10.
    assert result.substeps.iloc[0] == 3


@pytest.mark.parametrize('durations', [[1., 2., 6.], [1., 2., 8.], [0., 10.], [-1., 11.]])
def test_incomplete_overfull_or_nonpositive_bucket_rejected(durations):
    with pytest.raises(ValueError):
        module().aggregate_completed(detail([20.]*len(durations), durations))


def test_normal_and_fault_aggregate_on_their_own_substeps_before_subtraction():
    normal = detail([20., 25.], [8., 2.])
    fault = detail([20., 30., 24.], [1., 2., 7.])
    difference = module().paired_report(normal, fault)
    assert difference.temperature_C.iloc[0] == pytest.approx(3.8)
    assert difference.endpoint_C.iloc[0] == pytest.approx(-1.)


def test_adding_outputs_changes_no_original_text_and_covers_all_four_zones():
    original = 'Version,26.2;\nTimestep,6;\nCoil:Cooling:Water,unchanged;'
    changed = module().observation_idf(original)
    assert changed.startswith(original)
    for zone in ('OR1_FLR_2','OR2_MULT5_FLR_2','OR3_FLR_2','OR4_FLR_2'):
        assert f'Output:Variable,{zone},Zone Air Temperature,Detailed;' in changed
        assert f'Output:Variable,{zone},Zone Mean Air Temperature,Timestep;' in changed


def test_eso_original_fractional_clock_is_preserved_without_sql_rounding(tmp_path):
    p = tmp_path/'eplusout.eso'
    p.write_text('1,5,Environment Title[],Latitude[],Longitude[],Time Zone[],Elevation[]\n'
                 '2,8,Day[],Month[],Day[],DST[],Hour[],Start[],End[],DayType\n'
                 '10,1,OR1_FLR_2,Zone Air Temperature [C] !Each Call\n'
                 '11,1,OR1_FLR_2,Zone Air System Sensible Heating Rate [W] !Each Call\n'
                 '12,1,OR1_FLR_2,Zone Air System Sensible Cooling Rate [W] !Each Call\n'
                 'End of Data Dictionary\n1,RUN,1,1,-8,1\n2,1,7,20,0,13,0.00,3.33,Thursday\n'
                 '10,25\n11,1000\n12,2000\n2,1,7,20,0,13,3.33,10.00,Thursday\n'
                 '10,26\n11,500\n12,2000\nEnd of Data\n')
    result = module().read_detailed_eso(p)
    assert result.eso_start_minute.tolist() == ['0.00','3.33']
    assert result.eso_end_minute.tolist() == ['3.33','10.00']
    assert result.eso_dt_h.iloc[0] == pytest.approx(3.33/60)
    assert result.net_sensible_kW.tolist() == [-1.,-1.5]


def test_align_completed_zone_clock_preserves_unrounded_actual_system_durations():
    # end-zone API current_time can include the last shortened system dt.
    # The SQL clock is used only for the already complete ten-minute bucket.
    api = detail([23.,25.],[10/3,20/3],bucket='2006-07-20T12:16:40')
    api['substep_index'] = [0,1]
    clock = pd.DatetimeIndex(['2006-07-20T12:10:00'])
    aligned = module().align_completed_api(api,clock)
    assert aligned.bucket_end.tolist() == [clock[0],clock[0]]
    np.testing.assert_array_equal(aligned.dt_h,api.dt_h)
    assert aligned.source_api_bucket_end.iloc[0] == pd.Timestamp('2006-07-20T12:16:40')
    assert aligned.timestamp.iloc[0] == pd.Timestamp('2006-07-20T12:03:20')
