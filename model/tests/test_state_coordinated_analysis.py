"""V17 metadata and saved-analysis regression; no physical episodes."""
import copy
import importlib
import json
from pathlib import Path
from types import SimpleNamespace
import uuid

import pytest

from test_engineering_analysis import payload, segment

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def tmp_path():
    # Research test artifacts stay on D; no global pytest/TEMP changes.
    path = ROOT/'outputs/v17_state_coordinated_20261018_run01/test_tmp'/uuid.uuid4().hex
    path.mkdir(parents=True)
    return path


def api(name):
    try:
        return importlib.import_module('tools.state_coordinated.'+name)
    except ModuleNotFoundError:
        pytest.fail('V17 '+name+' API is not implemented')


def carrier_loss(a, b, priority=0, carrier=2, loss=2., *, hot=False):
    s = segment(a, b)
    s['unserved_kW'][priority][carrier] = loss
    s['temperature']['P0'] = 28. if hot else 21.
    s['violation']['P0'] = hot
    return s


def test_carrier_loss_recovery_and_temperature_recovery_are_distinct():
    a = api('analysis')
    p = payload([carrier_loss(0,15,hot=True), carrier_loss(15,30,loss=0,hot=True),
                 carrier_loss(30,60,loss=0), carrier_loss(60,75), carrier_loss(75,120,loss=0)],120)
    p['scenario'].update(canonical_class='L4',legacy_class='L4b')
    before = copy.deepcopy(p)
    r = a.analyze_payload(p,None)
    w = r['v17']['windows']['registered_full']
    cold = w['observed_prefix']['carrier_service']['P0']['c']
    assert cold['ENS_kWh'] == 1.
    assert cold['material_minutes'] == 30.
    assert cold['longest_material_minutes'] == 15.
    assert cold['first_loss_minute'] == 0.
    assert cold['first_clean_minute'] == 15.
    assert cold['confirmed_30min_interval'] == [15.,45.]
    assert cold['recurrence_after_30min_count'] == 1
    assert w['observed_prefix']['temperature']['P0']['first_clean_minute'] == 30.
    assert w['observed_prefix']['temperature']['P0']['confirmed_30min_interval'] == [30.,60.]
    assert r['windows']['registered_full']['original_19']['P0_failure_in_window'] == 0.
    assert p == before
    json.dumps(r,allow_nan=False)


def test_unknown_tail_and_insufficient_recovery_confirmation_are_not_filled():
    a = api('analysis')
    p = payload([carrier_loss(0,15,priority=1,carrier=1), carrier_loss(15,30,loss=0)],120)
    r = a.analyze_payload(p,None)['v17']['windows']['registered_full']
    assert r['all_registered'] is None
    heat = r['observed_prefix']['carrier_service']['P1']['h']
    assert heat['ENS_kWh'] == .5 and heat['first_clean_minute'] == 15.
    assert heat['confirmed_30min_interval'] is None
    assert heat['recovery_status'] == 'unconfirmed_observed_clean'
    assert r['unknown_tail_minutes'] == [30.,120.]


def test_tiny_loss_not_material_and_window_boundary_loss_is_left_censored():
    a = api('analysis')
    p = payload([carrier_loss(0,80),carrier_loss(80,600,loss=.0005)],600)
    r = a.analyze_payload(p,None)['v17']['windows']
    cold = r['primary']['observed_prefix']['carrier_service']['P0']['c']
    assert cold['first_loss_minute'] == 60.
    assert cold['loss_runs'][0]['left_censored']
    assert cold['material_minutes'] == 20.
    assert cold['ENS_kWh'] > 2/3


def test_economic_decomposition_uses_declared_fuel_and_separates_dg_shaft():
    a = api('analysis')
    s = segment(0,60)
    s['action'].update(gt_fuel_MMBtu_h=2.,boiler_steam_lbph=293.0710702,grid_import_kW=10.,diesel_kW=3.)
    s['diesel_local_response'] = dict(electrical_work_kWh=3.,mechanical_work_kWh=3.2,
        loss_work_kWh=.2,rotor_delta_kWh=0.,fuel_budget_debit_kWh_equivalent=3.2,
        original_electric_fee_USD=.9,shaft_equivalent_fee_USD=.96)
    # Explicit plant parameters, not an invented gas/diesel conversion.
    env = SimpleNamespace(config={},plant=SimpleNamespace(steam_kWh_per_lb=1.,boiler_eff=.5,
        gas_price_usd_per_mmbtu=4.),data=None)
    e = a.economic_window(payload([s],60),env,0.,60.)
    assert e['GT_gas_MMBtu'] == 2. and e['boiler_gas_MMBtu'] == 2.
    assert e['natural_gas_total_MMBtu'] == 4.
    assert e['GT_gas_fee_USD'] == 8. and e['boiler_gas_fee_USD'] == 8.
    assert e['grid_purchase_kWh'] == 10. and e['grid_fee_USD'] is None
    assert e['DG_shaft_work_kWh'] == 3.2 and e['DG_electric_work_kWh'] == 3.
    assert e['DG_fuel_budget_debit_kWh_equivalent'] == 3.2
    assert e['DG_original_electric_fee_USD'] == .9
    assert e['diesel_chemical_fuel_or_carbon'] is None


def test_export_compatible_batch_api_preserves_unknown_and_immutable_files(tmp_path):
    a = api('analysis')
    r = a.analyze_payload(payload([carrier_loss(0,30)],120),None)
    out=tmp_path/'analysis'
    receipt = a.export_rows([dict(policy_id='E',analysis=r)],out)
    assert receipt['physical_episodes_started'] == 0 and receipt['additional_raw_opens'] == 0
    assert (out/'V17_CARRIER_SERVICE.csv').exists()
    assert (out/'V17_ECONOMICS.csv').exists()
    assert (out/'V17_RECOVERY_STAGES.csv').exists()
    saved=json.loads((out/'RESULTS.json').read_text(encoding='utf8'))
    assert 'v17' not in saved['records'][0]['analysis']
    assert 'actual_intervals' not in saved['records'][0]['analysis'].get('timelines',{})
    assert receipt['unknown_registered_full_trajectory_count'] == 1
    with pytest.raises(FileExistsError): a.export_rows([r],out)


def test_design_main_eighty_specs_and_native_canonical_mapping_remain_separate():
    d = api('data_design'); p = d.build_main_design()
    assert p['canonical_class_counts'] == dict(L1=20,L2=20,L3=20,L4=20)
    assert len(p['roots']) == 20
    mapping = dict(L1='L1',L2='L3',L3='L2',L4='L4b')
    assert all(s['legacy_class'] == mapping[s['canonical_class']] == s['event_class'] for s in p['scenarios'])
    for rid in {s['root_family_id'] for s in p['scenarios']}:
        scenes = {s['canonical_class']:s for s in p['scenarios'] if s['root_family_id']==rid}
        off,on = scenes['L1'],scenes['L2']
        assert off['exogenous_events'] == on['exogenous_events']
        assert off['faults'] == on['faults'] and off['first_event_minute'] == on['first_event_minute']
        assert off['base_state_id'] == on['base_state_id']
        assert not off['enable_propagation'] and on['enable_propagation']
        assert not scenes['L3']['faults'] and not scenes['L3']['enable_propagation']
    assert p['original_final12_evaluated'] is False
    assert p['source_load_values_read'] is False
    assert p['raw_or_result_files_read'] is False
    assert p['signature'] == d.digest({k:v for k,v in p.items() if k!='signature'})
    assert d.allowed_hours(p)


def test_development_known_in_service_targets_and_sealed_support_rejected():
    d = api('data_design'); p = d.build_development_design()
    assert {10,190,250,310}.issubset({s['source_day_index'] for s in p['scenarios']})
    assert any(s['faults'].get('HRSG_1')==0 and s.get('target_operating_role')=='in_service_candidate' for s in p['scenarios'])
    assert any(s['faults'].get('EC_2')==0 and s.get('target_operating_role')=='in_service_candidate' for s in p['scenarios'])
    assert any(s.get('target_operating_role')=='standby_candidate' for s in p['scenarios'])
    assert all(s['experiment_role']=='development' for s in p['scenarios'])
    with pytest.raises(ValueError,match='[Ss]ealed'):
        d.build_main_design(days=[21]+list(d.MAIN_CALENDAR_DAYS[1:]))


def test_long_and_normal_designs_are_separate_and_have_full_tail_support():
    d = api('data_design'); normal=d.build_normal_design(); long=d.build_long_outage_design()
    assert {24.,48.,72.}.issubset({s['duration_steps']*s['step_minutes']/60 for s in normal['scenarios']})
    assert len(long['scenarios'])==8
    assert {s['diesel_design'] for s in long['scenarios']} == {'all_GT_lost','one_GT_retained'}
    assert all(not s['core_four_class_denominator'] for s in normal['scenarios']+long['scenarios'])
    for s in long['scenarios']:
        assert s['duration_steps']*s['step_minutes'] >= s['fault_start_minute']+s['fault_duration_minutes']+240.
    assert len({s['id'] for s in long['scenarios']})==8


def test_long_preserves_native_no_demand_case_and_actual_gt_loss_count():
    d=api('data_design');p=d.build_long_outage_design()
    for s in p['scenarios']:
        expected='all_GT_lost' if all(s['faults'].get('GT_'+str(i))==0 for i in (1,2,3)) else 'one_GT_retained'
        assert s['diesel_design']==expected
        if not s['shock'] and not s['shock_by_priority']:
            assert s['canonical_class']=='L1' and s['legacy_class']==s['event_class']=='L1'
            assert s['long_outage_mechanism']=='multiple_exogenous_faults_no_demand_shock'


def test_cross_panel_support_isolation_checks_full_support_and_embargo():
    d=api('data_design')
    assert d.validate_panel_isolation(d.build_main_design(),d.build_normal_design(),d.build_development_design())['isolated']
    with pytest.raises(ValueError,match='support|[Ii]solation'):
        d.validate_panel_isolation(d.build_main_design(),d.build_normal_design(days=(32,)))


def test_l4_propagation_off_manifest_keeps_exact_original_scene(monkeypatch):
    d=api('data_design');p=d.build_development_design()
    s=copy.deepcopy(next(s for s in p['scenarios'] if s['legacy_class']=='L4b'))
    s.update(enable_propagation=False,v17_matched_propagation_ablation=True,design_variant='OFF')
    p=d.panel('prop_off',[s]);s=p['scenarios'][0]
    assert not s['enable_propagation'] and s['stage']==4 and s['event_class']=='L4b'
    def inventory_only(proposal,*args,**kwargs):
        assert proposal['scenarios'][0]['enable_propagation'] is True
        return dict(scenarios=proposal['scenarios'],splits={},engineering_model_contract_id='physical-M16',
            legacy_execution_contract_id='exec',legacy_assumption_set_id='assume')
    monkeypatch.setattr(d.previous,'build_manifest',inventory_only)
    manifest=d.build_manifest(p,{'source.py':'0'*64},'M16_essential',{})
    assert manifest['scenarios']==[s]
    assert not manifest['scenarios'][0]['enable_propagation']
    assert manifest['scenarios'][0]['physical_scenario_signature']==s['physical_scenario_signature']
    assert manifest['study_contract_id']==d.STUDY_CONTRACT_ID
    assert manifest['engineering_model_contract_id']=='physical-M16'
    assert manifest['signature']==d.digest({k:v for k,v in manifest.items() if k!='signature'})


def test_target_in_service_output_and_source_recovery_do_not_use_hardware_return():
    a=api('analysis'); parts=[segment(0,15),segment(15,30),segment(30,45),segment(45,90)]
    for i,s in enumerate(parts):
        s['action'].update(hrsg_steam_lbph=100.,electric_chiller_cooling_kW=[0.,10. if i in (0,3) else 0.,0.])
        s['availability']={'EC_2':0. if i==1 else 1.,'HRSG_1':1.,'HRSG_2':1.,'HRSG_3':1.}
    p=payload(parts,90);p['scenario'].update(faults={'EC_2':0.},fault_start_minute=15.,fault_duration_minutes=15.)
    r=a.analyze_payload(p,None)['v17']['recovery_stages']['target_device_operation'][0]
    assert r['pre_fault_actual_output']==10. and r['pre_fault_operating_role']=='in_service'
    assert r['first_effective_full_return_observed_minute']==30.
    assert r['first_positive_output_after_effective_return_minute']==45.
    assert r['positive_source_is_service_recovery'] is False


def test_individual_hrsg_derived_from_common_coupling_not_aggregate_total():
    a=api('analysis');s=segment(0,60,gt=(5.,0.,0.));s['action']['hrsg_steam_lbph']=100.
    s['availability']={'HRSG_1':1.,'HRSG_2':1.,'HRSG_3':1.}
    after=copy.deepcopy(s);after.update(start_minute=60.,end_minute=90.);after['availability']['HRSG_2']=0.
    p=payload([s,after],90);p['scenario'].update(faults={'HRSG_2':0.},fault_start_minute=60.,fault_duration_minutes=15.)
    r=a.analyze_payload(p,None)['v17']['recovery_stages']['target_device_operation'][0]
    assert r['pre_fault_actual_output']==0.
    assert r['pre_fault_operating_role']=='standby_or_not_producing'
    assert r['output_unit']=='lb/h steam' and 'coupling' in r['output_basis']


def test_zero_diesel_electric_output_without_receipt_does_not_identify_shaft_work():
    a=api('analysis');s=segment(0,60)
    e=a.economic_window(payload([s],60),None,0.,60.)
    assert e['DG_electric_work_kWh']==0.
    assert e['DG_original_electric_fee_USD']==0.
    for field in ('DG_shaft_work_kWh','DG_loss_work_kWh','DG_rotor_delta_kWh',
                  'DG_fuel_budget_debit_kWh_equivalent','DG_shaft_equivalent_fee_USD'):
        assert e[field] is None


def test_manifest_binds_actual_mixed_policy_action_routes(monkeypatch):
    d=api('data_design');p=d.build_development_design()
    from tools.state_coordinated.runner import policy_specs
    specs=policy_specs();ids=[specs[key]['policy_id'] for key in ('E','K1','C')]
    def inventory_only(proposal,*args,**kwargs):
        return dict(scenarios=proposal['scenarios'],splits={},policies=ids,
            schemas={'action':'operating_response.operating_joint.v15'},
            planning_interface_id='operating_response.ordinary_joint_command.v15',
            engineering_model_contract_id='physical-M16',legacy_execution_contract_id='exec',legacy_assumption_set_id='assume')
    monkeypatch.setattr(d.previous,'build_manifest',inventory_only)
    m=d.build_manifest(p,{'source.py':'0'*64},'M16_essential',{},policies=ids)
    assert m['schemas']['action']=='state_coordinated.mixed_actions.v17'
    assert m['legacy_action_schema']=='operating_response.operating_joint.v15'
    assert m['legacy_planning_interface_id']=='operating_response.ordinary_joint_command.v15'
    assert m['policy_action_routes']==dict(zip(ids,('engineering_joint','operating_joint','state_coordinated_joint')))
    assert m['action_routes']['state_coordinated_joint']['action_schema']=='state_coordinated.joint.v17'
    assert m['action_routes']['engineering_joint']['action_schema']=='engineering_model.engineering_joint.v14'
    assert m['action_routes']['operating_joint']['action_schema']=='operating_response.operating_joint.v15'
    assert m['signature']==d.digest({k:v for k,v in m.items() if k!='signature'})
