"""Versioned C campaigns must preserve fixed baselines and immutable bindings."""
import copy
import importlib
import json
from pathlib import Path
import uuid
import pytest

def api():
    try:return importlib.import_module('tools.state_coordinated.runner')
    except ModuleNotFoundError:pytest.fail('Missing V17 bounded runner')

def test_v17_has_independent_root_and_retains_frozen_E_K1():
    from tools.essential_fixes.runner import policy_specs as previous
    m=api()
    assert m.RUN.name=='v17_state_coordinated_20261018_run01'
    assert m.policy_specs()['E']==previous()['E']
    assert m.policy_specs()['K1']==previous()['K1']
    assert m.policy_specs()['C']['policy_id'].startswith('C_state_coordinated_')

def test_baseline_identity_does_not_accept_config_mutation():
    m=api();spec=copy.deepcopy(m.policy_specs()['E']);spec['config']['invented_future_permission']=True
    with pytest.raises(ValueError):m.factory(spec)

def test_v17_output_boundary_rejects_old_and_C_drive():
    m=api()
    for p in ('C:/hospital_new_run',str(m.ROOT/'outputs/v16_essential_engineering_20261017_run01/experiments/new')):
        with pytest.raises(ValueError):m._experiment_path(Path(p))

def test_C_configuration_identity_is_not_silently_reused():
    m=api();spec=copy.deepcopy(m.policy_specs()['C']);spec['config']['support_minutes']=999.
    with pytest.raises(ValueError):m.factory(spec)

@pytest.fixture
def fresh_run(monkeypatch):
    m=api();root=m.ROOT/'outputs/v17_state_coordinated_20261018_run01/test_tmp'/('registration_'+uuid.uuid4().hex)
    monkeypatch.setattr(m,'RUN',root)
    return m,root

@pytest.mark.parametrize('field',('model_config','policy_specs','scenario_ids','registered_pairs'))
def test_changed_registration_cannot_reach_physical_environment(fresh_run,monkeypatch,field):
    m,root=fresh_run
    from tools.operating_response.data_design import normal_proposal
    proposal=normal_proposal();directory=root/'experiments/one'
    m.register(proposal,directory,[m.policy_specs()['E']],model_id='M16_essential',model_config={},
               scenario_ids=[proposal['scenarios'][0]['id']])
    plan=m.read(directory/'plan.json')
    if field=='model_config':plan[field]['gt_floor_correction']['enabled']=False
    elif field=='policy_specs':plan[field][0]['policy_id']='unregistered_C'
    elif field=='scenario_ids':plan[field]=['invented_scene']
    else:plan[field]+=1
    (directory/'plan.json').write_text(json.dumps(plan),encoding='utf8')
    monkeypatch.setattr(m,'OperatingHospitalIESEnv',lambda *a,**kw:pytest.fail('Unbound plan reached physical simulation'))
    with pytest.raises(ValueError,match='binding|manifest|Plan|registration'):m.run_batch(directory)
    assert not (directory/'ATTEMPTS.jsonl').exists()
    assert not (directory/'pairs').exists()

def test_registration_retains_actual_M16_and_cannot_overwrite(fresh_run):
    m,root=fresh_run
    from tools.operating_response.data_design import normal_proposal
    p=normal_proposal();before=copy.deepcopy(p);directory=root/'experiments/one'
    plan=m.register(p,directory,[m.policy_specs()['E']],model_id='M16_essential',model_config={},
                    scenario_ids=[p['scenarios'][0]['id']])
    assert plan['model_config']['gt_floor_correction']['enabled'] is True
    assert plan['registered_pairs']==1
    assert plan['canonical_run']==str(root.resolve())
    assert p==before
    with pytest.raises(FileExistsError):
        m.register(p,directory,[m.policy_specs()['E']],model_id='M16_essential',model_config={})

def test_compact_preserves_unknown_and_metrics_without_duplicate_recovery_timelines():
    m=api();original=dict(windows={'registered_full':{'complete':False}},
        v17=dict(windows={'registered_full':dict(complete=False,unknown_tail_minutes=[100.,600.],observed_prefix={'P0_cold_ENS':6.})},
            recovery_stages=dict(actual_source_intervals=[{'actual':123.}],ordinary_command_lifecycle=[{'command':1}],hardware_effective_state={'preserve':True})))
    before=copy.deepcopy(original);result=m.compact_analysis(original)
    assert original==before
    assert result['v17']['windows']==original['v17']['windows']
    stages=result['v17']['recovery_stages']
    assert 'actual_source_intervals' not in stages
    assert 'ordinary_command_lifecycle' not in stages
    assert stages['exported_row_counts']=={'actual_source_intervals':1,'ordinary_command_lifecycle':1}
    assert stages['hardware_effective_state']=={'preserve':True}

def test_C_cannot_be_registered_against_unimplemented_old_model_route(fresh_run):
    m,root=fresh_run
    from tools.operating_response.data_design import normal_proposal
    p=normal_proposal();directory=root/'experiments/unsupported_C'
    with pytest.raises(ValueError,match='M16|model|route'):
        m.register(p,directory,[m.policy_specs()['C']],model_id='M15_local_response',model_config={},
            scenario_ids=[p['scenarios'][0]['id']])
    assert not directory.exists()
