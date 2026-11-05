from types import SimpleNamespace
import pytest
from tools.model_review_v28.demand import thermal_target_context, DEMAND_CONTRACT_ID


def row(**changes):
    trace=dict(contract_id='hospital_ies.minute1.ordinary_wateruse_trace.v1',
        path='E:/native_minute/thermal_components.csv',sha256='a'*64,minute_of_year=1921,
        hour_of_year=33,boundary='ordinary_wateruse_central_target_minute1',
        request_column='ordinary_DHW_target_kW',legacy_delivery_column='ordinary_DHW_original_delivered_kW')
    trace.update(changes)
    return SimpleNamespace(sci_demand_boundary_contract=DEMAND_CONTRACT_ID,
        minute1_thermal_service_trace=trace,Q_h_DHW_water_use_kW=4.,v28_ordinary_DHW_source_delivered_kW=3.)


def test_minute_target_is_distinct_from_source_delivery_without_fake_hourly_trace():
    result=thermal_target_context(row())
    assert result is not None,'Minute DHW trace is not yet supported'
    assert result['target_kW']==4. and result['legacy_delivered_kW']==3.
    assert result['source_trace']['minute_of_year']==1921


@pytest.mark.parametrize('changes',[{'hour_of_year':34},{'minute_of_year':True},
    {'path':'inputs/model_review_v28/pretend.csv'},{'sha256':'unknown'},
    {'boundary':'ordinary_wateruse_central_target_v28'}])
def test_minute_trace_rejects_unbound_clock_path_or_legacy_alias(changes):
    with pytest.raises(ValueError,match='minute'):
        thermal_target_context(row(**changes))
