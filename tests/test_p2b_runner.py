"""Orchestration tests use synthetic callbacks, not new hospital experiments."""
import json
from pathlib import Path
import sys
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def test_runner_retains_error_and_attempts_other_policy(tmp_path):
    from tools.p2b_experiment.runner import execute_pairs, load_payload
    def fake(s, policy):
        if policy == 'priority_milp':
            raise ValueError('orchestration test error, not hospital failure')
        return {'summary': {'status': 'completed', 'p0_failed_any': 0}}
    result = execute_pairs([{'id': 'one'}], tmp_path, 'sig', fake)
    assert result['attempted'] == 2
    rows = [json.loads(x) for x in (tmp_path/'attempts.jsonl').read_text().splitlines()]
    assert len([x for x in rows if x['event'] == 'started']) == 2
    p = load_payload(tmp_path, 'one', 'priority_milp', 'sig')
    assert p['summary']['status'] == 'orchestration_error'
    assert p['summary']['p0_failed_any'] is None
    assert 'ValueError' == p['error']['type']
    assert load_payload(tmp_path, 'one', 'secure_milp', 'sig')['summary']['status'] == 'completed'


def test_rerun_uses_checkpoint_without_new_attempt(tmp_path):
    from tools.p2b_experiment.runner import execute_pairs
    calls = []
    def fake(s, p):
        calls.append(p)
        return {'summary': {'status': 'completed'}}
    execute_pairs([{'id': 'one'}], tmp_path, 'sig', fake)
    execute_pairs([{'id': 'one'}], tmp_path, 'sig', fake)
    assert len(calls) == 2


def test_interrupted_attempt_is_not_silently_repeated(tmp_path):
    from tools.p2b_experiment.runner import execute_pairs, load_payload
    (tmp_path/'attempts.jsonl').write_text(json.dumps(dict(event='started',scenario_id='one',policy_id='priority_milp',manifest_signature='sig'))+'\n')
    calls=[]
    def fake(s,p):
        calls.append(p)
        return {'summary':{'status':'completed'}}
    execute_pairs([{'id': 'one'}], tmp_path, 'sig', fake)
    assert calls==['secure_milp']
    interrupted=load_payload(tmp_path,'one','priority_milp','sig')
    assert interrupted['summary']['status']=='interrupted_unknown'
    assert interrupted['summary']['p0_failed_any'] is None


def test_duplicate_scenario_rejected_before_execution(tmp_path):
    from tools.p2b_experiment.runner import execute_pairs
    with pytest.raises(ValueError, match='Duplicate'):
        execute_pairs([{'id': 'one'}, {'id': 'one'}], tmp_path, 'sig', lambda s,p: {})
    assert not (tmp_path/'attempts.jsonl').exists()


def test_checkpoint_tamper_and_wrong_signature_rejected(tmp_path):
    from tools.p2b_experiment.runner import execute_pairs, load_payload, payload_path
    execute_pairs([{'id': 'one'}], tmp_path, 'sig', lambda s,p: {'summary':{'status':'completed'}})
    with pytest.raises(ValueError, match='signature'):
        load_payload(tmp_path, 'one', 'priority_milp', 'other')
    path = payload_path(tmp_path, 'one', 'priority_milp')
    path.write_bytes(path.read_bytes()+b'x')
    with pytest.raises(ValueError, match='hash'):
        load_payload(tmp_path, 'one', 'priority_milp', 'sig')


def test_global_partition_rejects_budget_duplicates_and_missing():
    from tools.p2b_experiment.runner import validate_partition
    base={'scenarios':[{'id':'a'},{'id':'b'}], 'policies':['priority_milp','secure_milp'],
          'p2b_shards':[{'id':'s0','scenario_ids':['a']},{'id':'s1','scenario_ids':['b']}]}
    validate_partition(base)
    import copy
    bad=copy.deepcopy(base);bad['p2b_shards'][1]['scenario_ids']=['a']
    with pytest.raises(ValueError,match='partition'):validate_partition(bad)
    bad=copy.deepcopy(base);bad['scenarios']=[{'id':str(i)} for i in range(401)]
    with pytest.raises(ValueError,match='budget'):validate_partition(bad)


def test_runtime_drift_is_rejected_without_simulation(monkeypatch):
    from tools.p2b_experiment.runner import verify_manifest
    from hospital_ies.dataset_manifest import digest
    import hospital_ies.dataset_manifest as dm
    monkeypatch.setattr(dm,'verify_execution_source',lambda root:None)
    monkeypatch.setattr(dm,'runtime_versions',lambda:{'python':'new'})
    m={'runtime':{'python':'old'},'files':[],'p2b_dependencies':[],
       'scenarios':[{'id':'a'}], 'policies':['priority_milp','secure_milp'],
       'p2b_shards':[{'id':'s0','scenario_ids':['a']} ]}
    m['signature']=digest(m)
    with pytest.raises(ValueError,match='Runtime'):verify_manifest(m)


def test_checkpoint_cannot_be_moved_to_another_pair(tmp_path):
    from tools.p2b_experiment.runner import execute_pairs, payload_path, load_payload
    import shutil
    execute_pairs([{'id':'a'}],tmp_path,'sig',lambda s,p:{'summary':{'status':'completed'}})
    source=payload_path(tmp_path,'a','priority_milp')
    dest=payload_path(tmp_path,'b','secure_milp')
    shutil.copy2(source,dest)
    shutil.copy2(source.with_suffix('.sha256'),dest.with_suffix('.sha256'))
    with pytest.raises(ValueError,match='identity'):load_payload(tmp_path,'b','secure_milp','sig')


@pytest.mark.parametrize('events,scenario', [(['started','started'],'a'),(['finished'],'a'),(['started'],'foreign')])
def test_malformed_attempt_journal_cannot_hide_budget(tmp_path,events,scenario):
    from tools.p2b_experiment.runner import execute_pairs
    rows=[dict(event=e,scenario_id=scenario,policy_id='priority_milp',manifest_signature='sig') for e in events]
    (tmp_path/'attempts.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
    with pytest.raises(ValueError,match='Journal'):
        execute_pairs([{'id':'a'}],tmp_path,'sig',lambda s,p:{'summary':{'status':'completed'}})


def test_finished_checkpoint_missing_is_corruption_not_interruption(tmp_path):
    from tools.p2b_experiment.runner import execute_pairs
    rows=[dict(event=e,scenario_id='a',policy_id='priority_milp',manifest_signature='sig') for e in ['started','finished']]
    (tmp_path/'attempts.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
    with pytest.raises(ValueError,match='completed checkpoint'):
        execute_pairs([{'id':'a'}],tmp_path,'sig',lambda s,p:{'summary':{'status':'completed'}})


def test_saved_checkpoint_without_terminal_event_is_reconciled(tmp_path):
    from tools.p2b_experiment.runner import execute_pairs
    execute_pairs([{'id':'a'}],tmp_path,'sig',lambda s,p:{'summary':{'status':'completed'}})
    j=tmp_path/'attempts.jsonl'
    rows=[json.loads(x) for x in j.read_text().splitlines()]
    j.write_text(''.join(json.dumps(r)+'\n' for r in rows if r['event']=='started'))
    execute_pairs([{'id':'a'}],tmp_path,'sig',lambda s,p:pytest.fail('rerun'))
    assert sum(json.loads(x)['event']=='reconciled_checkpoint' for x in j.read_text().splitlines())==2
