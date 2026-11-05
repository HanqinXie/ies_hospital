"""Audit bookkeeping fixtures; mock release only, never authorize simulation."""
from pathlib import Path
import copy,json,importlib,sys
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from tools.p2b_experiment.runner import save_payload,payload_path
from hospital_ies.data_contract import first_failure_label

def make_fixture(tmp,monkeypatch,status='completed',first=75.,second=False):
    runner=importlib.import_module('tools.integrated_experiment.runner')
    monkeypatch.setattr(runner,'verify_manifest',lambda m:None) # bookkeeping unit only
    specs=[dict(id='caseA',experiment_role='pilot_train',distribution_id='fixture',root_family_id='rootA',time_block_id='bgA',event_class='L4a',condition_id='fixed_condition')]
    if second:specs.append(dict(specs[0],id='caseB',root_family_id='rootB',time_block_id='bgB'))
    m=dict(signature='test-only-not-a-release',scenarios=specs,policies=['priority_milp','secure_milp'],
        options=dict(seed=20261014,security_weight=.35,label_horizons=[60],score_start_minute=60,score_end_minute=120,history_minutes=60),
        execution_contract_id='fixture-contract',simulator_hash='fixture-core',assumption_set_id='fixture-assumptions',schemas={'fixture':'v1'},
        splits={s['id']:dict(group_id=s['time_block_id'],split='pilot_train',experiment_role='pilot_train',fit_allowed=True) for s in specs},
        p2b_shards=[dict(id='shard_00',scenario_ids=[s['id'] for s in specs])],integrated_cohort='new_development')
    folder=tmp/'shards/shard_00';folder.mkdir(parents=True);records=[]
    for policy in m['policies']:
        end=120 if status=='completed' else 75;segments=[]
        for t in range(0,end,15):
            segments.append(dict(start_minute=float(t),end_minute=float(t+15),unserved_kW=[[1e-4,0,0],[0,0,0],[0,0,0],[0,0,0]],
                classification={'p0_guarantee_failure':first==t+15},violation={f'P{i}':False for i in range(4)},
                temperature=dict(P0=22.,P1=22.,P2=22.,P3=None),energy_balance_residuals=[0,0,0],steam_balance_residual_lbph=0.,cost_USD_step=1.,
                hour_of_year=1,action=dict(gt_fuel_MMBtu_h=0.,boiler_steam_lbph=0.,grid_import_kW=0.,diesel_kW=0.)))
        identity=dict(scenario_id='caseA',policy_id=policy,group_id='bgA',experiment_role='pilot_train',manifest_signature=m['signature'],
            execution_contract_id=m['execution_contract_id'],simulator_hash=m['simulator_hash'],assumption_set_id=m['assumption_set_id'],schemas=m['schemas'])
        decisions=[]
        for t in range(0,end,15):
            decisions.append(dict(decision_time_min=float(t),features={'time_minute':float(t)},
                feature_metadata={'time_minute':dict(unit='min',source='fixture',available_time_min=float(t),valid_time_min=float(t),measurement='ideal_observation_assumption')},
                known_command_queue=[],labels={'60':first_failure_label(t,60,first,end,status)},
                graph={'nodes':[dict(id='source',available_time_min=float(t))],'edges':[],'statistical_edges':[]}))
        p=dict(identity=identity,scenario=specs[0],seed=20261014,initial_state={'fixture_state':1},decisions=decisions,segments=segments,
            summary=dict(status=status,observed_until_minute=end,physical_service_failure=first is not None,
                full_trajectory_outcome_valid=status=='completed'),error=None if status=='completed' else {'type':'SolverError','message':'fixture, not a clinical label'})
        save_payload(folder,'caseA',policy,m['signature'],p)
        records.extend([dict(event=x,scenario_id='caseA',policy_id=policy,manifest_signature=m['signature']) for x in ('started','finished')])
    (folder/'attempts.jsonl').write_text('\n'.join(json.dumps(r) for r in records)+'\n',encoding='utf-8')
    path=tmp/'global_manifest.json';path.write_text(json.dumps(m),encoding='utf-8');return path,m,folder

def module():return importlib.import_module('tools.integrated_experiment.data_audit')

def test_new_seed_and_complete_budget_mean_attempts_not_success(tmp_path,monkeypatch):
    path,m,folder=make_fixture(tmp_path,monkeypatch,status='solver_error',first=None)
    r=module().audit(path,'check')
    assert r['passed'] and r['complete_budget'] and r['attempted_trajectories']==2
    assert r['score_unknown']==2 and r['score_observed_P0_failures']==0 and r['solver_error_trajectories']==2
    assert len(r['checkpoint_digests'])==2

def test_unknown_prefix_positive_not_made_complete(tmp_path,monkeypatch):
    path,m,folder=make_fixture(tmp_path,monkeypatch,status='solver_error',first=75.)
    r=module().audit(path,'check')
    assert r['score_unknown']==2 and r['score_observed_P0_failures']==2
    rows=json.loads((tmp_path/'audits/check/trajectory_scores.json').read_text())
    assert all(x['score_P0_failure']==1 and x['cost_USD'] is None for x in rows)

def test_partial_manifest_retains_unattempted_ledger(tmp_path,monkeypatch):
    path,m,folder=make_fixture(tmp_path,monkeypatch,second=True)
    r=module().audit(path,'check');assert not r['complete_budget'] and r['planned_trajectories']==4 and r['unattempted_trajectories']==2
    ledger=json.loads((tmp_path/'audits/check/attempt_ledger.json').read_text())
    assert len(ledger)==4 and sum(x['status']=='not_attempted' for x in ledger)==2

def test_seed_mismatch_rejected(tmp_path,monkeypatch):
    path,m,folder=make_fixture(tmp_path,monkeypatch)
    from tools.p2b_experiment.runner import load_payload
    p=load_payload(folder,'caseA','secure_milp',m['signature']);p['seed']=20261012;save_payload(folder,'caseA','secure_milp',m['signature'],p)
    with pytest.raises(AssertionError,match='seed'):module().audit(path,'check')

def test_future_graph_rejected(tmp_path,monkeypatch):
    path,m,folder=make_fixture(tmp_path,monkeypatch)
    from tools.p2b_experiment.runner import load_payload
    p=load_payload(folder,'caseA','secure_milp',m['signature']);p['decisions'][0]['graph']['nodes'][0]['available_time_min']=1.;save_payload(folder,'caseA','secure_milp',m['signature'],p)
    with pytest.raises(AssertionError,match='graph'):module().audit(path,'check')

def test_checkpoint_without_attempt_rejected(tmp_path,monkeypatch):
    path,m,folder=make_fixture(tmp_path,monkeypatch)
    (folder/'attempts.jsonl').write_text('',encoding='utf-8')
    with pytest.raises(AssertionError,match='Orphan|orphan'):module().audit(path,'check')

def test_pair_actual_initial_mismatch_rejected(tmp_path,monkeypatch):
    path,m,folder=make_fixture(tmp_path,monkeypatch)
    from tools.p2b_experiment.runner import load_payload
    p=load_payload(folder,'caseA','secure_milp',m['signature']);p['initial_state']['fixture_state']=2;save_payload(folder,'caseA','secure_milp',m['signature'],p)
    with pytest.raises(AssertionError,match='initial'):module().audit(path,'check')

def test_formal_and_tiny_abnormal_parallel_counts_preserved(tmp_path,monkeypatch):
    path,m,folder=make_fixture(tmp_path,monkeypatch)
    r=module().audit(path,'check')
    assert r['formal_warning_counts']['60']['legacy_any_tiny_abnormal_windows']>0
    assert r['formal_warning_counts']['60']['formal_positive_windows']>0
    assert (tmp_path/'audits/check/raw_labels.jsonl').is_file()
    assert len(json.loads((tmp_path/'audits/check/training_outcomes.json').read_text()))==1

def test_unregistered_shard_attempts_are_not_hidden(tmp_path,monkeypatch):
    path,m,folder=make_fixture(tmp_path,monkeypatch)
    hidden=tmp_path/'shards/old_unregistered_shard';hidden.mkdir()
    (hidden/'attempts.jsonl').write_text('{"event":"started","scenario_id":"old"}\n',encoding='utf-8')
    with pytest.raises(AssertionError,match='Unregistered shard'):
        module().audit(path,'check')
