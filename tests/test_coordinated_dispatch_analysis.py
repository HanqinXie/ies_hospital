"""Existing saved analysis is isolated and uses the actual small panel."""
import importlib
import copy
import gzip
import json
import pytest

def api():
    try:return importlib.import_module('tools.coordinated_dispatch.analysis')
    except ModuleNotFoundError:pytest.fail('Small-panel saved-analysis adapter is missing')

def test_reference_selection_uses_fair_fixed_comparisons_without_historical_quota():
    a=api()
    specs=[dict(policy_id='execution_reserve',kind='state_dispatch',config={}),
        dict(policy_id='joint_standby_1p25_h2',kind='joint',config={}),
        dict(policy_id='fixed',kind='coordinated',config={'coordination_mode':'fixed'}),
        dict(policy_id='without_A',kind='coordinated',config={'coordination_mode':'fixed','service_budget_enabled':False}),
        dict(policy_id='state',kind='coordinated',config={'coordination_mode':'state'})]
    assert a.references_for(specs)==('execution_reserve','joint_standby_1p25_h2','fixed')

def test_saved_parallel_comparison_keeps_old_module_immutable():
    a=api();old=importlib.import_module('tools.state_dispatch.analysis');before=old.REFERENCES
    namespace=a.analysis_namespace(('execution_reserve','fixed'))
    rows=[]
    for pid,value in (('execution_reserve',1.),('fixed',2.),('state',4.)):
        rows.append(dict(scenario_id='s',policy_id=pid,decision_time_min=15.,submitted_request_json={},
            requested_targets_kW_json=[value]*6,projected_targets_kW_json=[value]*6,
            actual_targets_kW_json=[value]*6))
    namespace['_decision_comparisons'](rows)
    assert rows[-1]['actual_targets_minus_parallel_fixed_kW_json']==[2.]*6
    assert rows[-1]['actual_execution_differs_from_parallel_fixed'] is True
    assert 'actual_targets_minus_parallel_secure_milp_kW_json' not in rows[-1]
    assert old.REFERENCES==before and namespace['_deltas'].__globals__ is namespace


def audited_small_panel(tmp_path):
    """Small synthetic saved files; real original pair SHA/identity checks."""
    previous=importlib.import_module('tools.four_scenarios.analysis')
    pids=('execution_reserve','joint_standby_1p25_h2','fixed','state')
    scene=dict(id='s',experiment_role='development',is_final_blind=False)
    manifest_path=tmp_path/'manifest.json';plan_path=tmp_path/'plan.json';audit_path=tmp_path/'AUDIT.json'
    def write(path,value):path.write_text(json.dumps(value),encoding='utf-8')
    write(manifest_path,dict(signature='manifest-signature',scenarios=[scene]))
    plan=dict(scenario_ids=['s'],policy_specs=[dict(policy_id=p) for p in pids],phase='development',
        registered_pair_count=len(pids),manifest_signature='manifest-signature',manifest_sha256=previous._sha(manifest_path))
    write(plan_path,plan)
    results=tmp_path/'results';payloads={};raws={};ledgers={}
    def save(pid):
        folder=results/'pairs'/previous.digest(['s',pid])/'attempt_0001';folder.mkdir(parents=True,exist_ok=True)
        raw=folder/'trajectory.json.gz'
        with gzip.open(raw,'wt',encoding='utf-8') as stream:json.dump(payloads[pid],stream)
        result=folder/'result.json'
        write(result,dict(scenario_id='s',policy_id=pid,terminal=True,status='completed',
            trajectory_path=raw.name,trajectory_sha256=previous._sha(raw),binding={'passed':True}))
        raws[pid]=raw;ledgers[pid]=dict(scenario_id='s',policy_id=pid,status='completed',latest_result_sha256=previous._sha(result))
    for pid in pids:
        payloads[pid]=dict(scenario=scene,identity=dict(scenario_id='s',policy_id=pid,exogenous_stream_id='same-stream'),
            initial_state={'gt_prev':[0.,0.,0.]},segments=[],events=[],decisions=[])
        save(pid)
    audit=dict(passed=True,matched_initial_state_and_stream=True,plan_sha256=previous._sha(plan_path),
        manifest_sha256=previous._sha(manifest_path),registered_pair_count=len(pids),terminal_pair_count=len(pids),
        recomputed_pair_count=len(pids),pair_ledger=list(ledgers.values()))
    write(audit_path,audit)
    return dict(paths=(manifest_path,plan_path,results,audit_path),pids=pids,payloads=payloads,raws=raws,
                audit=audit,ledgers=ledgers,save=save,write=write)


def test_no_secure_panel_loads_actual_references_and_preserves_raw_audit_checks(tmp_path,monkeypatch):
    a=api();fixture=audited_small_panel(tmp_path)
    old=importlib.import_module('tools.four_scenarios.deepening_analysis');old_loader=old._load_audited_batch
    namespace=a.analysis_namespace(('execution_reserve','joint_standby_1p25_h2','fixed'))
    local=namespace['deepening']
    monkeypatch.setattr(local,'trajectory_row',lambda s,p,*rest:dict(scenario_id=s['id'],policy_id=p))
    loaded=local._load_audited_batch(*fixture['paths'])
    assert loaded['policy_ids']==list(fixture['pids']) and 'secure_milp' not in loaded['policy_ids']
    assert len(loaded['rows'])==4 and local is not old and old._load_audited_batch is old_loader
    raw=fixture['raws']['fixed'];original=raw.read_bytes();raw.write_bytes(original+b'changed')
    with pytest.raises(ValueError,match='raw SHA'):local._load_audited_batch(*fixture['paths'])
    raw.write_bytes(original)
    fixture['audit']['passed']=False;fixture['write'](fixture['paths'][3],fixture['audit'])
    with pytest.raises(ValueError,match='Passed raw audit'):local._load_audited_batch(*fixture['paths'])


@pytest.mark.parametrize('mismatch',('initial_state','stream'))
def test_no_secure_panel_still_rejects_unmatched_physical_initialization(tmp_path,monkeypatch,mismatch):
    a=api();fixture=audited_small_panel(tmp_path);namespace=a.analysis_namespace(('execution_reserve','fixed'))
    local=namespace['deepening']
    monkeypatch.setattr(local,'trajectory_row',lambda s,p,*rest:dict(scenario_id=s['id'],policy_id=p))
    if mismatch=='initial_state':fixture['payloads']['state']['initial_state']['gt_prev'][0]=1.
    else:fixture['payloads']['state']['identity']['exogenous_stream_id']='wrong-stream'
    fixture['save']('state');fixture['audit']['pair_ledger']=list(fixture['ledgers'].values())
    fixture['write'](fixture['paths'][3],fixture['audit'])
    with pytest.raises(ValueError,match='Matched policies differ'):
        local._load_audited_batch(*fixture['paths'])
