import copy
import json
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[1]

def registry():
    return json.loads((ROOT/'configs/model_correction_v26/cases.json').read_text())

def api():
    from tools.model_review_v27 import validation
    return validation

def test_unqualified_temperature_cannot_enter_a_normal_supply_run():
    reg=registry();case=copy.deepcopy(reg['cases'][0])
    case['model_correctness_v23']=copy.deepcopy(reg['model_correctness_v23'])
    case['model_correctness_v23']['thermal_coupling']={'reference_csv':'future_or_frozen.csv'}
    with pytest.raises(ValueError,match='unqualified|not qualified'):
        api().preflight_case(case,reg)

def test_dynamic_cold_request_is_rejected_instead_of_being_reported_as_passed():
    reg=registry();case=copy.deepcopy(reg['cases'][0]);case['dynamic_cold_interface']='validate'
    with pytest.raises(ValueError,match='unqualified|not qualified'):
        api().preflight_case(case,reg)

def test_v27_current_information_policies_share_explicit_order_but_keep_their_registered_mechanisms():
    reg=registry();original=copy.deepcopy(reg);case=copy.deepcopy(reg['cases'][0])
    c=api().resolve_case(case,reg,'C','after_cost');m=api().resolve_case(case,reg,'M','after_cost')
    assert c['policy']['config']['online_GT_count_order']=='after_cost'
    assert m['policy']['config']['online_GT_count_order']=='after_cost'
    assert c['policy']['config']['policy_mode']!=m['policy']['config']['policy_mode']
    assert reg==original and case==original['cases'][0]

def test_explicit_legacy_remains_separately_named_and_unknown_order_fails():
    reg=registry();case=copy.deepcopy(reg['cases'][0])
    new=api().resolve_case(case,reg,'C','after_cost');old=api().resolve_case(case,reg,'C','legacy_before_cost')
    assert api().policy_identity(new)!=api().policy_identity(old)
    with pytest.raises(ValueError,match='order'):
        api().resolve_case(case,reg,'C','economic_mpc')

def test_stage_integral_unit_mapping_preserves_historical_values_and_rejects_incomplete_contract():
    import pandas as pd
    from tools.model_review_v27.evidence import corrected_cold_stage_units
    frame=pd.DataFrame({'native_storage_total_W':[192.],'coil_total_W':[239.],
        'pump_total_W':[38.],'evaporator_total_W':[0.],'signed_residual_kWh':[-85.]})
    original=frame.copy(deep=True);fixed,mapping=corrected_cold_stage_units(frame)
    assert frame.equals(original)
    assert fixed.native_storage_total_kWh.iloc[0]==192.
    assert fixed.signed_residual_kWh.iloc[0]==-85.
    assert all(r['unit']=='kWh' for r in mapping)
    with pytest.raises(ValueError,match='missing|contract'):
        corrected_cold_stage_units(frame.drop(columns=['coil_total_W']))
