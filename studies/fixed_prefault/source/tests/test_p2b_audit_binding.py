import hashlib
import json
import pytest


def test_checkpoint_digest_binds_bytes_and_canonical_payload(tmp_path):
    from tools.p2b_experiment.audit import checkpoint_digest
    payload = {'identity': {'scenario_id': 'a', 'policy_id': 'priority_milp'}, 'x': None}
    p = tmp_path/'a.json.gz'
    p.write_bytes(b'checkpoint bytes')
    r = checkpoint_digest(p, payload, 'a', 'priority_milp', tmp_path)
    assert r['checkpoint_sha256'] == hashlib.sha256(p.read_bytes()).hexdigest()
    assert r['payload_sha256'] == hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode('utf-8')).hexdigest()
    assert r['path'] == 'a.json.gz'
    assert r['scenario_id'] == 'a' and r['policy_id'] == 'priority_milp'
    changed = dict(payload, x=0)
    assert checkpoint_digest(p,changed,'a','priority_milp',tmp_path)['payload_sha256'] != r['payload_sha256']


def test_entirely_missing_journal_cannot_hide_checkpoint(tmp_path,monkeypatch):
    from tools.p2b_experiment import audit as module
    monkeypatch.setattr(module,'verify_manifest',lambda _:None)
    manifest=dict(signature='s',scenarios=[{'id':'a'}],policies=['priority_milp','secure_milp'],p2b_shards=[{'id':'s0','scenario_ids':['a']}])
    p=tmp_path/'manifest.json';p.write_text(json.dumps(manifest))
    folder=tmp_path/'shards/s0/checkpoints';folder.mkdir(parents=True)
    (folder/'some.json.gz').write_bytes(b'orphan')
    with pytest.raises((AssertionError,ValueError),match='journal'):
        module.audit(p,'synthetic')


def test_temperature_comparison_uses_registered_absolute_tolerance():
    from tools.p2b_experiment.audit import pair_category
    a=dict(score_outcome_valid=True,score_P0_failure=0,cost_USD=0)
    for g in range(4):
        a.update({f'P{g}_{k}_ENS_kWh':0 for k in 'ehc'})
        a[f'P{g}_max_temperature_C']=23
        a[f'P{g}_violation_minutes']=0
    b=dict(a,P0_max_temperature_C=23+1.01e-6)
    assert pair_category(a,b)=='both_no_P0_other_different'
