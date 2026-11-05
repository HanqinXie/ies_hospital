"""V28 public demand identity and input-only activation, before implementation."""
from pathlib import Path
import copy,json
import pandas as pd
import pytest

ROOT=Path(__file__).resolve().parents[1]
def source_case():
    registry=json.loads((ROOT/'configs/model_review_v27/cases.json').read_text(encoding='utf8'))
    return copy.deepcopy(registry['cases'][0]),registry

def test_v28_default_is_fixed_direct_and_retains_after_cost_without_other_goal_changes():
    from tools.model_review_v28.validation import resolve_case,policy_identity
    from tools.model_review_v28 import STUDY_ID
    case,registry=source_case();old=copy.deepcopy(case)
    new=resolve_case(case,registry)
    assert new['study_id']==STUDY_ID
    assert new['v28_demand_version']=='A_original_heat'
    assert new['v28_demand_contract']=='hospital_ies.fixed_direct_aux.v28.v1'
    assert new['policy']['config']==old['policy']['config']
    assert case==old
    assert policy_identity(new)=='C_fixed_direct_aux_v28_A_original_heat'

def test_v28_rejects_unknown_demand_version_and_unqualified_thermal_interfaces():
    from tools.model_review_v28.validation import resolve_case
    case,registry=source_case();case['v28_demand_version']='unknown_unregistered'
    with pytest.raises(ValueError,match='demand version'):resolve_case(case,registry)
    case,registry=source_case();case['model_correctness_v23']=dict(registry['model_correctness_v23'],thermal_coupling={'unqualified':True})
    with pytest.raises(ValueError,match='not qualified'):resolve_case(case,registry)

def test_v28_normal_route_always_selects_new_demand_adapter():
    from tools.model_review_v28.environment import with_boundary,OperatingHospitalIESEnv
    from tools.model_correction_v25 import environment as legacy
    adapter=with_boundary(None,demand_version='A_original_heat')
    assert adapter is not legacy
    assert issubclass(OperatingHospitalIESEnv,legacy.OperatingHospitalIESEnv)

def test_boundary_activation_is_in_memory_and_limited_to_original_hour_grant():
    from tools.model_review_v28.environment import activate_boundary
    from types import SimpleNamespace
    original=pd.DataFrame({'hour_of_year':[1,2,3],'Q_h_DHW_water_use_kW':[1.,2.,3.]})
    env=SimpleNamespace(hourly=original,allowed_hours={2})
    activate_boundary(env)
    assert original.columns.tolist()==['hour_of_year','Q_h_DHW_water_use_kW']
    assert env.hourly.loc[1,'sci_demand_boundary_contract']=='hospital_ies.fixed_direct_aux.v28.v1'
    assert pd.isna(env.hourly.loc[0,'sci_demand_boundary_contract'])
    assert pd.isna(env.hourly.loc[2,'sci_demand_boundary_contract'])
    assert env.hourly.Q_h_DHW_water_use_kW.tolist()==original.Q_h_DHW_water_use_kW.tolist()

def test_A_original_heat_cannot_silently_receive_the_adopted_thermal_profile():
    from tools.model_review_v28.validation import resolve_case
    case,registry=source_case()
    case.update(v28_demand_version='A_original_heat',v28_thermal_profile={'files':{}})
    with pytest.raises(ValueError,match='original heat|A_original_heat'):resolve_case(case,registry)

def test_all_registered_native_scenarios_have_legal_event_encodings():
    from tools.model_review_v28.environment import validate_scenario
    registry=json.loads((ROOT/'configs/model_review_v28/cases.json').read_text(encoding='utf8'))
    for case in registry['cases']:
        scene=copy.deepcopy(case['scenario']);scene.pop('model_closure_events',None)
        validate_scenario(scene)
