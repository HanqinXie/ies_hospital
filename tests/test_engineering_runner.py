"""Registration guards, preserving original outcome and baseline identities."""
import importlib
import json
from pathlib import Path
import sys
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]


def module():
    try:return importlib.import_module('tools.engineering_model.runner')
    except ModuleNotFoundError as exc:pytest.fail('Missing engineering runner: '+str(exc),pytrace=False)


def test_named_baseline_and_new_policy_remain_distinct():
    m=module();specs=m.policy_specs()
    assert {'F','H','Z','S','E','C'}<=set(specs)
    assert specs['F']['policy_id']=='fixed_standby_constant_v12b'
    assert specs['H']['policy_id']=='executed_loss_feedback_v13'
    assert len({x['policy_id'] for x in specs.values()})==len(specs)


def test_registration_is_immutable_and_model_explicit(tmp_path):
    m=module();proposal=json.loads((ROOT/'outputs/v14_engineering_model_20261017_run01/design_v2/DEVELOPMENT_PROPOSAL.json').read_text(encoding='utf-8'))
    ids=[next(s['id'] for s in proposal['scenarios'] if s['analysis_class']=='N2')]
    destination=tmp_path/'registered'
    plan=m.register(proposal,destination,[m.policy_specs()['F']],model_id='M0',model_config={},scenario_ids=ids)
    assert plan['registered_pairs']==1 and plan['model_id']=='M0'
    assert plan['source_sha256'] and plan['original_final12']=='not_verified; unopened'
    with pytest.raises(FileExistsError):m.register(proposal,destination,[m.policy_specs()['F']],model_id='M0',model_config={},scenario_ids=ids)
    assert (destination/'manifest.json').exists()


def test_c_drive_and_unknown_model_registration_are_rejected():
    m=module()
    with pytest.raises(ValueError):m.check_output_path(Path('C:/v14_forbidden_data'))
    from tools.engineering_model.environment import model_contract
    with pytest.raises(ValueError):model_contract('M14_hidden_fast_diesel',{})


def test_matching_uses_actual_state_and_applied_events():
    import copy
    m=module();a=dict(initial_state={'gt_prev':[100.,0.,0.]},scenario={'start_hour':10,'faults':{},'shock':{}})
    b=copy.deepcopy(a)
    assert m.matching_evidence([a,b])['same_initial_state']
    b['initial_state']['gt_prev'][0]=101.
    assert not m.matching_evidence([a,b])['same_initial_state']
    b=copy.deepcopy(a);b['scenario']['faults']={'GT_1':0.}
    assert not m.matching_evidence([a,b])['same_exogenous_definition']
    b['initial_state']=None
    assert not m.matching_evidence([a,b])['same_initial_state']


def test_registration_records_resolved_execution_assumptions(tmp_path):
    m=module();p=m.read(m.RUN/'design_v2/DEVELOPMENT_PROPOSAL.json')
    sid=next(s['id'] for s in p['scenarios'] if s['analysis_class']=='N2')
    plan=m.register(p,tmp_path/'resolved',[m.policy_specs()['S']],model_id='M14_engineering',model_config={},scenario_ids=[sid])
    assert plan['model_config']['gt_start_delay_minutes']==10.
    assert plan['model_config']['minimum_up_minutes']==60.
    manifest=m.read(tmp_path/'resolved'/'manifest.json')
    assert manifest['model_config']==plan['model_config']
