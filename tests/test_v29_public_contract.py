"""Adopted-only registration and inherited hardware, before V29 implementation."""
from pathlib import Path
import copy,json,sys
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
def original():
    registry=json.loads((ROOT/'configs/model_review_v28/cases.json').read_text(encoding='utf8'))
    case=next(c for c in registry['cases'] if c['id']=='B_combined_N')
    registry['adopted_thermal_profile']=copy.deepcopy(case['v28_thermal_profile'])
    return copy.deepcopy(case),registry

def test_new_entry_reuses_adopted_V28_request_and_original_policy_rules():
    from tools.model_review_v29.validation import resolve_case
    from tools.model_review_v29 import STUDY_ID
    from tools.model_review_v28.validation import resolve_case as inherited
    case,registry=original();before=copy.deepcopy(case)
    actual=resolve_case(case,registry);old=inherited(case,registry)
    assert actual['study_id']==STUDY_ID
    assert actual['v28_demand_version']=='V28_adopted'
    assert actual['v28_demand_contract']=='hospital_ies.fixed_direct_aux.v28.v1'
    assert actual['policy']==old['policy']
    assert actual['v29_core_reuse']['scientific_producer_commit']=='09f922840b7cfaef209d693ebfdda3492e16e24a'
    assert case==before

@pytest.mark.parametrize('version',[None,'A_original_heat','unregistered'])
def test_every_new_case_requires_explicit_adopted_version(version):
    from tools.model_review_v29.validation import resolve_case
    case,registry=original()
    if version is None:case.pop('v28_demand_version')
    else:case['v28_demand_version']=version
    with pytest.raises(ValueError,match='V28_adopted'):resolve_case(case,registry)

def test_thermal_profile_must_be_exact_frozen_adopted_files():
    from tools.model_review_v29.validation import resolve_case
    case,registry=original();case['v28_thermal_profile']['files']['thermal_service_components']['sha256']='0'*64
    with pytest.raises(ValueError,match='thermal|bound'):resolve_case(case,registry)

def test_V29_keeps_unqualified_interfaces_and_old_stress_proxy_closed():
    from tools.model_review_v29.validation import resolve_case
    case,registry=original();registry['model_closure_v22']['research_stress_proxy']=True
    with pytest.raises(ValueError,match='stress'):resolve_case(case,registry)
    case,registry=original();case['model_correctness_v23']={'thermal_coupling':{'unqualified':True}}
    with pytest.raises(ValueError,match='not qualified'):resolve_case(case,registry)

def test_registered_hardware_is_exact_parent_not_silent_inventory_change():
    from tools.model_review_v29.validation import resolve_case
    registry=json.loads((ROOT/'configs/model_review_v29/cases.json').read_text(encoding='utf8'))
    parent=json.loads((ROOT/'configs/model_correction_v25/cases.json').read_text(encoding='utf8'))
    assert set(registry['hardware_profiles'])=={'H0','H1','H2'}
    for name in ['H0','H1','H2']:assert registry['hardware_profiles'][name]==parent['hardware_profiles'][name]
    assert registry['hardware_profiles']['H0']['sci_engineering']['ups']=={}
    assert registry['hardware_profiles']['H0']['sci_engineering']['cold_store']=={}
    assert registry['hardware_profiles']['H1']['sci_engineering']['ups']['capacity_kWh']==40
    assert registry['hardware_profiles']['H1']['sci_engineering']['ups']['power_kW']==160
    assert registry['hardware_profiles']['H2']['sci_engineering']['cold_store']['capacity_kWh']==60
    for case in registry['cases']:
        actual=resolve_case(case,registry)
        assert actual['v28_demand_version']=='V28_adopted'
        assert actual['policy']['config']['online_GT_count_order']=='after_cost'

def test_new_fault_scenarios_preserve_exact_parent_events_background_and_severity():
    registry=json.loads((ROOT/'configs/model_review_v29/cases.json').read_text(encoding='utf8'))
    mapping={'grid_winter_fault':('model_correction_v25','four_L1_grid_winter'),
             'tower_summer_fault':('engineering_completion_v24','cold_partial_tower'),
             'heat_pump_winter_fault':('model_review_v28','A_aux_pump_fault'),
             'transfer_300_fault':('model_correction_v25','transfer_l2_long_300')}
    keys=['start_hour','duration_steps','step_minutes','pre_event_warmup_steps','initialization_seed',
          'faults','auxiliary_faults','shock','shock_by_priority','shock_by_zone','exogenous_events','model_closure_events',
          'fault_start_minute','fault_duration_minutes','shock_start_minute','shock_duration_minutes','grid_limit_kW','pre_event_grid_limit_kW','enable_propagation']
    for name,(folder,parent_id) in mapping.items():
        old_registry=json.loads((ROOT/'configs'/folder/'cases.json').read_text(encoding='utf8'))
        parent=next(c for c in old_registry['cases'] if c['id']==parent_id)
        case=next(c for c in registry['cases'] if c['id']==name)
        for key in keys:assert case['scenario'].get(key)==parent['scenario'].get(key),(name,key)
        assert case['configuration']==parent['configuration']
        assert case['v29_mechanism']['parent_case_id']==parent_id

def test_registered_native_parser_encodings_still_validate():
    from tools.model_review_v28.environment import validate_scenario,validate_events
    registry=json.loads((ROOT/'configs/model_review_v29/cases.json').read_text(encoding='utf8'))
    for case in registry['cases']:
        scene=copy.deepcopy(case['scenario']);events=scene.pop('model_closure_events',[])
        validate_scenario(scene);validate_events(events)

def test_scope_is_offline_postrun_annotation_and_keeps_unknown_source_null(tmp_path):
    from tools.model_review_v29.validation import annotate_result
    result={'status':'failed_unknown','observed_until_minute':None,'metrics':None}
    case,registry=original()
    annotated=annotate_result(tmp_path,ROOT,case,result)
    assert result=={'status':'failed_unknown','observed_until_minute':None,'metrics':None}
    assert annotated['status']=='failed_unknown' and annotated['metrics'] is None
    assert annotated['v29_source_scope']['time_support']['observed'] is None
    boundary=annotated['v29_source_scope']['energy_service_boundary']
    assert boundary['unknown_upstream_energy_kWh'] is None
    assert (tmp_path/'SOURCE_SCOPE.json').is_file()
