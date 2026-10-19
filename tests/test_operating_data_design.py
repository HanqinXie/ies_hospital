"""Metadata-only v15 design checks: no env, solver or annual numeric reads."""
import copy
import importlib
import json
from collections import Counter
from pathlib import Path

import pytest


def api():
    try:
        return importlib.import_module('tools.operating_response.data_design')
    except ModuleNotFoundError:
        pytest.fail('v15 data design not implemented')


def test_normal_windows_are_distributed_actual_full_support_not_old_three():
    a=api(); p=a.normal_proposal()
    assert len(p['scenarios'])==12
    assert sum(s['duration_steps']*s['step_minutes']/60 for s in p['scenarios'])==384
    assert Counter(s['season_quarter'] for s in p['scenarios'])=={1:3,2:3,3:3,4:3}
    assert all(s['analysis_class']=='N2' and not s['exogenous_events'] for s in p['scenarios'])
    for s in p['scenarios']:
        support=a.validate_sealed_support(s)
        assert support['raw_end_minute']>support['actual_trajectory_interval_minutes'][1]
        assert s['start_hour']+int(s['duration_steps']*.25) in a.allowed_hours(p)
    assert p['strict_final_gate']=='not_verified' and not p['original_final12_evaluated']


def test_four_classes_match_faults_but_retain_early_and_continuous_prefix_roles():
    a=api(); p=a.build_development()
    assert Counter(s['analysis_class'] for s in p['scenarios'])=={k:12 for k in ('L1','L2','L3','L4b')}
    assert len(p['roots'])==12
    for root in p['roots']:
        scenes={s['analysis_class']:s for s in p['scenarios'] if s['root_family_id']==root['root_family_id']}
        l1,l3=scenes['L1'],scenes['L3']
        for key in ('start_hour','faults','fault_start_minute','fault_duration_minutes'):
            assert l1[key]==l3[key]
        assert not l1['enable_propagation'] and l3['enable_propagation']
        assert not scenes['L2']['faults'] and not scenes['L2']['enable_propagation']
        assert scenes['L4b']['faults'] and scenes['L4b']['shock']
        assert all(s['prehistory_mode']=='same_continuous_episode_no_reset' for s in scenes.values())
    starts={s['first_event_minute'] for s in p['scenarios']}
    assert {0.,15.,45.,180.,360.}<=starts
    assert all(s['duration_steps']*15>=s['first_event_minute']+600 for s in p['scenarios'])
    assert {'e','h','c','mixed'}=={s['demand_carrier_design'] for s in p['scenarios'] if s['analysis_class']=='L2'}


def test_known_counterexamples_keep_physical_definition_and_exposed_role():
    a=api(); p=a.known_counterexamples()
    assert {'early45','late85','day51_original','day342_recovery','day47_recovery'}<=set(s['known_case_role'] for s in p['scenarios'])
    assert all(s['previously_exposed_v14'] and s['selection_allowed'] and not s['requires_policy_freeze'] for s in p['scenarios'])
    early=next(s for s in p['scenarios'] if s['known_case_role']=='early45')
    assert early['fault_start_minute']==45 and early['shock']=={'e':.75,'h':.35,'c':.5}
    assert early['shock_by_priority']['P0']['e']==.1


def test_complete_endpoint_and_embargo_reject_sealed_not_only_event_day():
    a=api(); s=copy.deepcopy(a.normal_proposal()['scenarios'][0])
    s['start_hour']=20*24+1;s['duration_steps']=96
    s['full_source_support']=a.validate_observation_support(s)
    with pytest.raises(ValueError,match='[Ss]ealed'):
        a.validate_sealed_support(s)
    # A 47h trajectory ends before day21, but its reserved endpoint plus
    # 60min embargo reaches that sealed day. A 24h day19 window does not.
    s['start_hour']=19*24+1;s['duration_steps']=188
    s['full_source_support']=a.validate_observation_support(s)
    with pytest.raises(ValueError,match='[Ss]ealed'):
        a.validate_sealed_support(s)


def freeze():
    return dict(frozen=True,source_sha256={'tools/operating_response/planner.py':'a'*64},
                model_configs={'M14_engineering':{},'M15_local_response':{}},
                policy_specs=[{'policy_id':'E6'},{'policy_id':'new'}])


def test_final_requires_freeze_and_has_new_roots_support_and_no_selection():
    a=api(); dev=a.build_development(); normals=a.normal_proposal()
    with pytest.raises(ValueError,match='[Ff]reez'):
        a.final_registration(dict(frozen=False),normals)
    p=a.final_registration(freeze(),normals)
    assert len(p['normal']['roots'])==8 and len(p['incidents']['scenarios'])==32
    assert not ({r['root_family_id'] for r in dev['roots']} & {r['root_family_id'] for r in p['incidents']['roots']})
    for scene in p['incidents']['scenarios']:
        a.validate_sealed_support(scene)
        assert scene['requires_policy_freeze'] and not scene['selection_allowed']
        assert scene['experiment_role']=='confirmation' and not scene['is_final_blind']
    assert a.allowed_hours(normals).isdisjoint(a.allowed_hours(p['normal']))
    assert p['incidents']['freeze_signature']==a.digest(freeze())
    assert p==a.final_registration(freeze(),normals)


def test_manifest_records_new_model_not_v14_alias_or_336_ceiling():
    a=api(); proposal=a.normal_proposal(); before=copy.deepcopy(proposal)
    m=a.build_manifest(proposal,{'source.py':'a'*64},'M15_local_response',{'example':1},policies=['E6','new'])
    assert m['execution_model_id']=='M15_local_response'
    assert m['engineering_model_contract_id']=='hospital_ies.operating_response.v15.v1'
    assert 'v15' in m['manifest_version'] and m['source_proposal_signature']==proposal['signature']
    assert m['model_config_signature']==a.digest({'example':1})
    assert m['execution_contract_id']!=m['legacy_execution_contract_id']
    assert m['resource_limits']['max_actual_attempts']!=336
    assert proposal==before


def test_v2_coordinates_cover_each_quarter_growth_and_declared_hold_inside_outside():
    a=api();p=a.build_development()
    assert p['coordinate_design_version']=='operating_response.v15.event_coordinates.v2'
    for quarter in range(1,5):
        pure=[s for s in p['scenarios'] if s['analysis_class']=='L2' and s['season_quarter']==quarter]
        assert {max(s['shock'].values()) for s in pure}=={.25,.75,1.2}
    faults=[s for s in p['scenarios'] if s['analysis_class']=='L1']
    shocks=[s for s in p['scenarios'] if s['analysis_class']=='L2']
    assert {s['fault_duration_minutes'] for s in faults}=={30.,60.,180.,300.}
    assert {s['shock_duration_minutes'] for s in shocks}=={30.,60.,90.,270.}
    middle=[s for s in shocks if max(s['shock'].values())==.75]
    assert all(s['preparation_demand_relation_by_domain']['K1']=='outside' for s in middle)
    assert all(s['preparation_demand_relation_by_domain']['K2']=='within_demand_component' for s in middle)
    for profile in ('K1','K2'):
        assert {'within_declared_hold','outside_declared_hold'}<=set(s['duration_relation_by_domain'][profile]['combined_active_duration_relation'] for s in p['scenarios'])
    assert all(s['preparation_hold_declaration_only'] and s['implemented_static_copy_support_hours']==2.
               and not s['all_interval_service_guarantee'] for s in p['scenarios'])
    assert all('not_guaranteed' in s['preparation_full_domain_status'] for s in p['scenarios'])


def test_v2_preserves_normal_and_old_unexecuted_development_coordinates_source():
    a=api();run=Path(__file__).resolve().parents[1]/'outputs/v15_engineering_operation_20261017_run01'
    normal=json.loads((run/'design/NORMAL_PROPOSAL.json').read_text(encoding='utf8'))
    old=json.loads((run/'design/DEVELOPMENT_PROPOSAL.json').read_text(encoding='utf8'))
    assert a.normal_proposal()==normal
    old_by={(s['root_family_id'],s['analysis_class']):s for s in old['scenarios']}
    new=a.build_development()
    assert len(new['scenarios'])==len(old['scenarios'])==48
    assert {s['id'] for s in new['scenarios']}.isdisjoint(s['id'] for s in old['scenarios'])
    fields=('root_family_id','source_day_index','start_hour','duration_steps','step_minutes','faults',
        'fault_start_minute','shock_start_minute','response_delay_minutes','first_event_minute','full_source_support')
    for scene in new['scenarios']:
        previous=old_by[(scene['root_family_id'],scene['analysis_class'])]
        assert {k:scene[k] for k in fields}=={k:previous[k] for k in fields}


def test_final_uses_same_v2_recipe_after_freeze_and_new_root_phase():
    a=api();p=a.final_registration(freeze(),a.normal_proposal())['incidents']
    assert p['coordinate_design_version']=='operating_response.v15.event_coordinates.v2'
    pure=[s for s in p['scenarios'] if s['analysis_class']=='L2']
    assert [max(s['shock'].values()) for s in pure]==[.25,.75,1.2,.25,.75,1.2,.25,.75]
    assert {s['shock_duration_minutes'] for s in pure}=={30.,60.,90.,270.}
    assert all(s['fault_start_minute']%15==3.75 for s in pure)
