"""New runner contract; synthetic callback tests do not approve hospital truth."""
import copy,importlib,json
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1]
PROPOSAL=ROOT/'outputs/v43_control_improvement_20261014_run01/design/INITIAL_300_PROPOSAL.json'
GATE=ROOT/'outputs/v43_integrated_20261014_run01/core_gate_v2.json'
def runner():return importlib.import_module('tools.control_improvement.data_runner')
def minimal(n):
    s=[dict(id=f'x{i}',experiment_role='pilot_train') for i in range(n)]
    return dict(scenarios=s,policies=['priority_milp','secure_milp'],p2b_shards=[dict(id='all',scenario_ids=[x['id'] for x in s])],
        splits={x['id']:dict(group_id='same',is_final_blind=False,experiment_role='pilot_train') for x in s})

def test_runner_budget_and_no_sealed_roles():
    r=runner();r.validate_partition(minimal(1200))
    with pytest.raises(ValueError,match='budget'):r.validate_partition(minimal(1201))
    m=minimal(1);m['splits']['x0']['is_final_blind']=True
    with pytest.raises(ValueError,match='sealed|blind'):r.validate_partition(m)

def test_family_group_role_crossing_and_shard_duplicates_rejected():
    r=runner();m=minimal(2);m['scenarios'][1]['experiment_role']='pilot_validation';m['splits']['x1']['experiment_role']='pilot_validation'
    with pytest.raises(ValueError,match='group|role'):r.validate_partition(m)
    m=minimal(2);m['p2b_shards'][0]['scenario_ids'].append('x0')
    with pytest.raises(ValueError,match='partition'):r.validate_partition(m)

def test_real_current_core_freeze_all300_preserves_H120_and600(tmp_path):
    r=runner();m=r.freeze_new(tmp_path/'fresh',GATE,PROPOSAL)
    r.verify_manifest(m)
    assert len(m['scenarios'])==300 and m['options']['label_horizons']==[15,30,60,120]
    assert m['options']['observed_end_minute']==600 and m['options']['security_weight']==.35
    assert [x['scenario_id'] for x in m['p2b_support']]==[x['id'] for x in m['scenarios']]
    assert m['execution_contract']==json.loads((ROOT/'configs/p1b_execution_research.json').read_text(encoding='utf-8'))
    assert any(x['path']=='tools/control_improvement/data_runner.py' for x in m['p2b_dependencies'])
    assert not any(x['path']=='tools/control_improvement/data_audit.py' for x in m['p2b_dependencies'])
    with pytest.raises(FileExistsError):r.freeze_new(tmp_path/'fresh',GATE,PROPOSAL)

def test_registered_full300_design_and_runtime_changes_rejected(tmp_path):
    r=runner();m=r.freeze_new(tmp_path/'fresh',GATE,PROPOSAL)
    q=copy.deepcopy(m);q['options']['label_horizons']=[15,30,60];q['signature']=r.digest({k:v for k,v in q.items() if k!='signature'})
    with pytest.raises(ValueError,match='design|Registered|registered'):r.verify_manifest(q)
    q=copy.deepcopy(m);q['runtime']['python']='invented';q['signature']=r.digest({k:v for k,v in q.items() if k!='signature'})
    with pytest.raises(ValueError,match='Runtime|runtime'):r.verify_manifest(q)

def test_initial_subset_then_whole_shard_never_repeats_started_attempt(tmp_path,monkeypatch):
    r=runner();m=minimal(3);m.update(signature='synthetic',execution_contract={},options=dict(seed=9,security_weight=.35,label_horizons=[15,30,60,120]))
    path=tmp_path/'global_manifest.json';path.write_text(json.dumps(m),encoding='utf-8')
    monkeypatch.setattr(r,'verify_manifest',lambda m:True)
    monkeypatch.setattr(r,'HospitalIESEnv',lambda *a,**k:object())
    calls=[]
    def simulate(env,s,policy,seed,weight,manifest,horizons):
        calls.append((s['id'],policy,tuple(horizons)))
        return dict(scenario=s,summary=dict(status='solver_error',completed=False,full_trajectory_outcome_valid=False,p0_failed_any=None),decisions=[],segments=[],events=[],error={'type':'synthetic_retained_error'})
    monkeypatch.setattr(r,'run_trajectory',simulate)
    a=r.run_shard(path,0,scenario_ids=['x0']);b=r.run_shard(path,0)
    assert len(calls)==6 and all(x[2]==(15,30,60,120) for x in calls)
    assert a['attempted']==2 and b['attempted']==6 and b['status_counts']==dict(solver_error=6)
    assert len({x[:2] for x in calls})==6

def test_source_producer_import_drift_rejected(tmp_path,monkeypatch):
    r=runner();m=r.freeze_new(tmp_path/'fresh',GATE,PROPOSAL)
    monkeypatch.setitem(r._PRODUCER_SOURCE_AT_IMPORT,'tools/control_improvement/data_runner.py','changed')
    with pytest.raises(ValueError,match='import|restart|producer'):r.verify_manifest(m)

def test_foreign_initial_subset_id_rejected_before_attempt(tmp_path,monkeypatch):
    r=runner();m=minimal(1);m.update(signature='synthetic')
    p=tmp_path/'global_manifest.json';p.write_text(json.dumps(m),encoding='utf-8');monkeypatch.setattr(r,'verify_manifest',lambda m:True)
    with pytest.raises(ValueError,match='subset|registered'):r.run_shard(p,0,scenario_ids=['foreign'])
    assert not (tmp_path/'shards').exists()
