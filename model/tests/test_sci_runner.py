"""Failure-preserving and source-bound V21 runner tests; no physical solve."""
from pathlib import Path
import importlib
import json
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]


def runner():
    return importlib.import_module('tools.sci_engineering.runner')

def test_sidecar_close_failure_preserves_already_returned_physical_prefix(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from tools.sci_engineering.solver_log import SolverRowSidecar
    r,root,directory,_=fixture(tmp_path)
    def fail_flush(self,data):raise OSError('synthetic sidecar disk failure')
    monkeypatch.setattr(SolverRowSidecar,'_flush_block',fail_flush)
    def make_env(*args,**kwargs):return SimpleNamespace(writer=kwargs['solver_row_sidecar'])
    def episode(env,scene,controller,seed,manifest):
        env.writer.store([{'index':0,'violation':0.}],location=[0])
        value=payload(scene,controller)
        value['segments'][0]['end_minute']=30.
        value['summary'].update(status='solver_error',observed_until_minute=30.,requested_end_minute=60.)
        value['error']={'type':'SyntheticPhysicalFailure','message':'physical prefix retained'}
        return value
    r.run_registered(root,directory,env_factory=make_env,policy_factory=lambda s:s['policy_id'],
        episode_fn=episode,max_pairs=1)
    record=r.read(next(directory.glob('pairs/*/attempt_*/RESULT.json')))
    assert record['status']=='failed_unknown'
    assert record['metrics']['observed_until_minute']==30.
    assert record['unknown_tail_minutes']==30.
    assert record['error']['physical_error']['type']=='SyntheticPhysicalFailure'
    assert Path(record['raw_path']).is_file()
    import gzip
    raw=json.loads(gzip.decompress(Path(record['raw_path']).read_bytes()))
    assert raw['segments'][0]['end_minute']==30.
    assert raw['error']['type']=='SyntheticPhysicalFailure'
    assert record['solver_row_evidence']['status']=='failed'

@pytest.mark.parametrize('artifact',['result','raw','sidecar'])
def test_retry_refuses_tampered_failed_terminal_evidence(tmp_path,artifact):
    r,root,directory,_=fixture(tmp_path)
    def fail(env,scene,controller,seed,manifest):
        value=payload(scene,controller)
        value['error']={'type':'SyntheticFailure','message':'retained failure fixture'}
        value['summary']['status']='solver_error'
        return value
    r.run_registered(root,directory,env_factory=env_factory,policy_factory=lambda s:s['policy_id'],
        episode_fn=fail,max_pairs=1)
    path=next(directory.glob('pairs/*/attempt_*/RESULT.json'))
    record=r.read(path)
    if artifact=='result':
        record['unknown_tail_minutes']=42.
        path.write_text(json.dumps(record),encoding='utf8')
    else:
        target=Path(record['raw_path'] if artifact=='raw' else record['solver_row_evidence']['path'])
        with target.open('ab') as stream:stream.write(b'SYNTHETIC_DRIFT')
    with pytest.raises(ValueError,match='evidence drift'):
        r.run_registered(root,directory,env_factory=env_factory,policy_factory=lambda s:s['policy_id'],
            episode_fn=fail,retry_failed=True,max_pairs=1)

def test_real_registered_episode_has_complete_legacy_export_identity_and_actual_m21_ledger(tmp_path):
    from scripts.sci_campaign import pilot_proposal
    from tools.sci_engineering.policy import policy_specs
    from tools.sci_engineering.environment import model_contract
    r=runner();panel=pilot_proposal(duration_minutes=45.,warmup_minutes=15.,fault_duration_minutes=15.)
    panel['scenarios']=panel['scenarios'][:1]
    specs=[policy_specs()['M']]
    configs={'H0':model_contract('M21_sci_engineering',{})[1]}
    freeze=tmp_path/'freeze/FREEZE.json'
    r.create_freeze(ROOT,freeze,specs,configs,dict(max_actual_attempts=2,max_experiment_wall_hours=1))
    directory=tmp_path/'real_episode'
    r.register_campaign(ROOT,directory,panel,specs,configs,freeze,phase='pilot',raw_root=tmp_path/'raw')
    result=r.run_registered(ROOT,directory)
    records=[json.loads(p.read_text(encoding='utf8')) for p in directory.glob('pairs/*/attempt_*/RESULT.json')]
    assert len(records)==1
    assert records[0]['error'] is None, records[0]['error']
    assert result['completed']==1 and result['failed']==0
    assert records[0]['metrics']['complete'] is True
    assert records[0]['metrics']['observed_until_minute']==45.


def fixture(tmp_path):
    r=runner()
    root=tmp_path/'source';(root/'src').mkdir(parents=True)
    (root/'src/core.py').write_text('frozen=1\n',encoding='utf8')
    policies=[dict(policy_id='C',config={}),dict(policy_id='A',config={})]
    configurations=dict(H0={})
    freeze_path=tmp_path/'run/FREEZE.json'
    r.create_freeze(root,freeze_path,policies,configurations,
        budget=dict(max_actual_attempts=10,max_experiment_wall_hours=2))
    scene=dict(id='s',source_block_id='block',stage=1,start_hour=1,duration_steps=4,step_minutes=15.,
        canonical_class='L1',initialization_seed=7,first_event_minute=15.,fault_duration_minutes=15.,
        recovery_start_minute=30.,role='development',full_source_support=dict(raw_start_minute=0.,raw_end_minute=120.))
    panel=dict(scenarios=[scene],source_blocks=[dict(source_block_id='block',role='development',
        raw_start_minute=0.,raw_end_minute=120.)])
    directory=tmp_path/'run/pilot'
    r.register_campaign(root,directory,panel,policies,configurations,freeze_path,phase='pilot',raw_root=tmp_path/'raw')
    return r,root,directory,freeze_path


def payload(scene,policy):
    demand=[[10.,0.,4.],[2.,0.,0.],[0.,0.,0.],[5.,0.,0.]]
    cut=[[0.,0.,0.],[0.,0.,0.],[0.,0.,0.],[1.,0.,0.]]
    lost=[[1.,0.,2.],[0.,0.,0.],[0.,0.,0.],[0.,0.,0.]]
    delivered=[[9.,0.,2.],[2.,0.,0.],[0.,0.,0.],[4.,0.,0.]]
    segment=dict(start_minute=0.,end_minute=60.,service_request_ledger=dict(original_demand_kW=demand,
        active_cut_kW=cut,connected_unserved_kW=lost,delivered_kW=delivered),
        action=dict(grid_import_kW=15.,diesel_kW=0.,gt_electric_kW=[0.,0.,0.],
                    electric_chiller_cooling_kW=[2.,0.,0.],absorption_cooling_kW=0.),
        m21_storage=dict(UPS=dict(start_kWh=3.,end_kWh=1.,discharge_output_kWh=1.8,charge_input_kWh=0.)))
    return dict(execution_model_id='M21_sci_engineering',scenario=scene,segments=[segment],
        summary=dict(status='completed',observed_until_minute=60.,requested_end_minute=60.),error=None,
        events=[],solver_calls=[],decisions=[],model_config={})


def env_factory(*args,**kwargs):
    return object()


def test_new_budget_does_not_inherit_historical_campaign_clock_and_raw_is_separate(tmp_path):
    r,root,directory,freeze=fixture(tmp_path)
    result=r.run_registered(root,directory,env_factory=env_factory,policy_factory=lambda x:x,
        episode_fn=lambda env,scene,policy,seed,manifest:payload(scene,policy))
    assert result['completed']==2 and result['failed']==0 and result['pending']==0
    records=[json.loads(p.read_text(encoding='utf8')) for p in directory.glob('pairs/*/attempt_*/RESULT.json')]
    assert len(records)==2
    assert all(Path(x['raw_path']).is_relative_to(tmp_path/'raw') for x in records)
    assert all(x['model_id']=='M21_sci_engineering' and x['source_block_id']=='block' for x in records)
    assert all(x['metrics']['carrier_service']['P0']['c']['ENS_kWh']==2. for x in records)
    assert all(x['metrics']['priority_union']['P0']['material_minutes']==60. for x in records)


def test_failure_retains_attempt_and_unknown_tail_and_later_pairs_continue(tmp_path):
    r,root,directory,freeze=fixture(tmp_path)
    def episode(env,scene,policy,seed,manifest):
        if policy['policy_id']=='C':raise RuntimeError('scientific failure retained')
        return payload(scene,policy)
    result=r.run_registered(root,directory,env_factory=env_factory,policy_factory=lambda x:x,episode_fn=episode)
    assert result['failed']==1 and result['completed']==1
    rows=[json.loads(x) for x in (directory/'ATTEMPTS.jsonl').read_text(encoding='utf8').splitlines()]
    assert [x['event'] for x in rows]==['started','finished','started','finished']
    failed=json.loads(next(directory.glob('pairs/s__C__H0/attempt_*/RESULT.json')).read_text(encoding='utf8'))
    assert failed['status']=='failed_unknown'
    assert failed['metrics'] is None and failed['unknown_tail_minutes']==60.
    assert 'scientific failure retained' in failed['error']['message']


def test_source_drift_refuses_before_any_attempt(tmp_path):
    r,root,directory,freeze=fixture(tmp_path)
    (root/'src/core.py').write_text('frozen=2\n',encoding='utf8')
    with pytest.raises(ValueError,match='drift'):
        r.run_registered(root,directory,env_factory=env_factory,policy_factory=lambda x:x,
            episode_fn=lambda *args:pytest.fail('must not execute drifted physics'))
    assert not (directory/'ATTEMPTS.jsonl').exists()


def test_formal_registration_requires_frozen_selection_and_actual_pilot(tmp_path):
    r,root,directory,freeze=fixture(tmp_path)
    plan=json.loads((directory/'PLAN.json').read_text(encoding='utf8'))
    with pytest.raises(ValueError,match='pilot'):
        r.register_campaign(root,tmp_path/'run/formal',plan['panel'],plan['policy_specs'],
            plan['configurations'],freeze,phase='formal',raw_root=tmp_path/'raw')


def test_resuming_skips_success_but_does_not_overwrite_failed_attempts(tmp_path):
    r,root,directory,freeze=fixture(tmp_path)
    def bad(env,scene,policy,seed,manifest):
        if policy['policy_id']=='C':raise ValueError('first retained')
        return payload(scene,policy)
    r.run_registered(root,directory,env_factory=env_factory,policy_factory=lambda x:x,episode_fn=bad)
    failed=next(directory.glob('pairs/s__C__H0/attempt_*/RESULT.json'));before=failed.read_bytes()
    result=r.run_registered(root,directory,env_factory=env_factory,policy_factory=lambda x:x,
        episode_fn=lambda env,scene,policy,seed,manifest:payload(scene,policy),retry_failed=True)
    assert result['completed']==2
    assert failed.read_bytes()==before
    assert len(list(directory.glob('pairs/s__C__H0/attempt_*/RESULT.json')))==2


def test_compact_service_refuses_relabeling_disconnection_as_satisfaction():
    r=runner();value=payload({},{});metrics=r.compact_metrics(value)
    p3=metrics['carrier_service']['P3']['e']
    assert p3['requested_kWh']==5. and p3['delivered_kWh']==4.
    assert p3['ENS_kWh']==p3['disconnected_ENS_kWh']==1.
    assert p3['connected_unserved_ENS_kWh']==0.
    bad=payload({},{});bad['segments'][0]['service_request_ledger']['delivered_kW'][0][0]=20.
    with pytest.raises(ValueError,match='service ledger'):
        r.compact_metrics(bad)


def test_default_policy_factory_receives_actual_common_plant(tmp_path,monkeypatch):
    from types import SimpleNamespace
    r,root,directory,freeze=fixture(tmp_path)
    plant=object();seen=[]
    def factory(spec,plant=None):
        assert plant is not None
        seen.append(plant)
        return spec
    monkeypatch.setattr(r,'_default_policy',factory)
    result=r.run_registered(root,directory,env_factory=lambda *a,**k:SimpleNamespace(plant=plant),
        episode_fn=lambda env,scene,policy,seed,manifest:payload(scene,policy))
    assert result['completed']==2 and seen==[plant,plant]


def test_cost_and_conditional_inventory_charge_are_counted_once():
    from types import SimpleNamespace
    r=runner();value=payload({},{});segment=value['segments'][0]
    segment.update(cost_USD_step=5.,grid_tariff_USD_kWh=.1,
        diesel_local_response=dict(mechanical_work_kWh=2.,electrical_work_kWh=1.,loss_work_kWh=1.,
            rotor_delta_kWh=0.,fuel_budget_debit_kWh_equivalent=2.,
            original_electric_fee_USD=.3,shaft_equivalent_fee_USD=.6))
    segment['action'].update(gt_fuel_MMBtu_h=0.,boiler_steam_lbph=0.)
    value['m21_initial_storage']=dict(ups=2.,cold_store=3.)
    plant=SimpleNamespace(steam_kWh_per_lb=.3,boiler_eff=.8,gas_price_usd_per_mmbtu=4.84,
                          ec_electric_kW_per_rt=.77)
    env=SimpleNamespace(plant=plant,sci_config=dict(cold_pump_kW_per_kW=.05))
    metrics=r.compact_metrics(value,env=env)
    initial_input=2.+3.*(.77/3.51685284+.05)
    assert metrics['economics']['initial_inventory_grid_input_kWh_conditional']==pytest.approx(initial_input)
    assert metrics['economics']['energy_cost_USD']==pytest.approx(5.+.3+initial_input*.1)
    assert metrics['economics']['DG_fee_correction_USD']==pytest.approx(.3)
    assert metrics['economics']['inventory_recharge_fee_added_twice'] is False


def test_resume_refuses_tampered_completed_result_even_if_raw_unchanged(tmp_path):
    r,root,directory,freeze=fixture(tmp_path)
    episode=lambda env,scene,policy,seed,manifest:payload(scene,policy)
    r.run_registered(root,directory,env_factory=env_factory,policy_factory=lambda x:x,episode_fn=episode)
    path=next(directory.glob('pairs/s__C__H0/attempt_*/RESULT.json'))
    record=json.loads(path.read_text(encoding='utf8'));record['metrics']['carrier_service']['P0']['e']['ENS_kWh']=0.
    path.write_text(json.dumps(record),encoding='utf8')
    with pytest.raises(ValueError,match='result evidence drift'):
        r.run_registered(root,directory,env_factory=env_factory,policy_factory=lambda x:x,episode_fn=episode)


def test_frozen_factor_registry_bytes_cannot_change_after_registration(tmp_path):
    r,root,directory,freeze_path=fixture(tmp_path)
    factor=root/'outputs/v20_load_accounting_20261019_run01/references/EMISSIONS_FACTOR_REGISTRY.json'
    factor.parent.mkdir(parents=True);factor.write_text('{"cases": {}}',encoding='utf8')
    frozen=r.create_freeze(root,tmp_path/'run/FACTORS_FREEZE.json',[dict(policy_id='C',config={})],dict(H0={}),
                          dict(max_actual_attempts=2,max_experiment_wall_hours=1))
    factor.write_text('{"changed": true}',encoding='utf8')
    with pytest.raises(ValueError,match='factor.*drift'):
        r.verify_freeze(root,frozen)
