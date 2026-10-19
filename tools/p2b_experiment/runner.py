"""Serial-per-shard P2B runner; frozen P2A physical core, failure-complete journal.

Every started trajectory consumes its registered attempt. An interrupted journal
without a checkpoint becomes an explicit unknown and is never repeated. Shards have disjoint
specification IDs and all use the same read-only global manifest.
"""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import sys
import traceback

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT/'src')]
POLICIES = ('priority_milp', 'secure_milp')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def payload_path(out, scenario_id, policy):
    key = sha(json.dumps([scenario_id, policy]).encode())
    return Path(out)/'checkpoints'/f'{key}.json.gz'


def load_payload(out, scenario_id, policy, signature):
    p = payload_path(out, scenario_id, policy)
    raw = p.read_bytes()
    if p.with_suffix('.sha256').read_text() != sha(raw):
        raise ValueError('Checkpoint hash mismatch')
    record = json.loads(gzip.decompress(raw))
    if record['manifest_signature'] != signature:
        raise ValueError('Checkpoint signature mismatch')
    if record.get('scenario_id')!=scenario_id or record.get('policy_id')!=policy:
        raise ValueError('Checkpoint pair identity mismatch')
    identity=record['payload'].get('identity')
    if identity is not None and (identity.get('scenario_id')!=scenario_id or identity.get('policy_id')!=policy):
        raise ValueError('Payload identity mismatch')
    return record['payload']


def save_payload(out, scenario_id, policy, signature, payload):
    p = payload_path(out, scenario_id, policy)
    p.parent.mkdir(parents=True, exist_ok=True)
    raw = gzip.compress(json.dumps(dict(manifest_signature=signature, scenario_id=scenario_id,
                        policy_id=policy, payload=payload),
                        ensure_ascii=False, allow_nan=False).encode('utf-8'), mtime=0)
    temp = p.with_suffix('.tmp')
    temp.write_bytes(raw)
    temp.replace(p)
    p.with_suffix('.sha256').write_text(sha(raw), encoding='ascii')


def execute_pairs(scenarios, out, signature, simulate):
    """The callback runs ONE policy; its failure never suppresses its pair."""
    out = Path(out)
    ids = [s['id'] for s in scenarios]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate scenario IDs')
    out.mkdir(parents=True, exist_ok=True)
    journal = out/'attempts.jsonl'
    records = [json.loads(x) for x in journal.read_text(encoding='utf-8').splitlines()] if journal.exists() else []
    if any(r['manifest_signature'] != signature for r in records):
        raise ValueError('Journal manifest signature mismatch')
    started=set();finished=set()
    for r in records:
        key=(r['scenario_id'],r['policy_id'])
        if key[0] not in ids or key[1] not in POLICIES:
            raise ValueError('Journal contains unowned scenario or policy')
        if r['event']=='started':
            if key in started:raise ValueError('Journal duplicate started attempt')
            started.add(key)
        elif r['event'] in ('finished','adjudicated_interrupted_unknown','reconciled_checkpoint'):
            if key not in started or key in finished:raise ValueError('Journal invalid completion sequence')
            finished.add(key)
        else:raise ValueError('Journal unknown event')
    def append(event, s, policy, **extra):
        row = dict(event=event, scenario_id=s['id'], policy_id=policy,
                   manifest_signature=signature, utc=datetime.now(timezone.utc).isoformat(), **extra)
        with journal.open('a', encoding='utf-8') as f:
            f.write(json.dumps(row, ensure_ascii=False)+'\n')
            f.flush()
    counts = Counter()
    for s in scenarios:
        for policy in POLICIES:
            key = (s['id'], policy)
            if payload_path(out, *key).exists():
                if key not in started:
                    raise ValueError('Unowned checkpoint without started journal')
                payload = load_payload(out, *key, signature)
                if key not in finished:
                    append('reconciled_checkpoint', s, policy, status=payload['summary']['status'])
            else:
                if key in started:
                    if key in finished:
                        raise ValueError('Missing completed checkpoint is corruption, not an interrupted attempt')
                    payload = dict(scenario=s, policy_id=policy, segments=[], decisions=[], events=[],
                        summary=dict(scenario_id=s['id'], policy_id=policy,
                            status='interrupted_unknown', attempted=True, completed=False,
                            p0_failed_any=None, physical_service_failure=False,
                            full_trajectory_outcome_valid=False),
                        error=dict(type='InterruptedAttempt', message='Started journal has no completed checkpoint; no rerun; in-memory prefix unavailable'))
                    save_payload(out, *key, signature, payload)
                    append('adjudicated_interrupted_unknown', s, policy, status='interrupted_unknown')
                    counts['interrupted_unknown'] += 1
                    continue
                append('started', s, policy)
                started.add(key)
                try:
                    payload = simulate(s, policy)
                except Exception as exc:
                    # This outer failure is infrastructure/data orchestration,
                    # not a solver or clinical failure. Physical exceptions are
                    # classified and retained inside frozen run_trajectory.
                    payload = dict(scenario=s, policy_id=policy, segments=[], decisions=[], events=[],
                        summary=dict(scenario_id=s['id'], policy_id=policy,
                            status='orchestration_error', attempted=True, completed=False,
                            p0_failed_any=None, physical_service_failure=False,
                            full_trajectory_outcome_valid=False),
                        error=dict(type=type(exc).__name__, message=str(exc), traceback=traceback.format_exc()))
                save_payload(out, *key, signature, payload)
                append('finished', s, policy, status=payload['summary']['status'])
                print(s['id'], policy, payload['summary']['status'], flush=True)
            counts[payload['summary']['status']] += 1
    result = dict(attempted=len(started), checkpoints=sum(counts.values()),
        status_counts=dict(counts), manifest_signature=signature)
    (out/'shard_status.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    return result


def validate_partition(manifest):
    ids = [s['id'] for s in manifest['scenarios']]
    if len(ids)>400:
        raise ValueError('Specification budget exceeds 400')
    shards=manifest['p2b_shards']
    assigned=[sid for sh in shards for sid in sh['scenario_ids']]
    if (len(ids)!=len(set(ids)) or len(assigned)!=len(set(assigned))
        or sorted(ids)!=sorted(assigned) or len({s['id'] for s in shards})!=len(shards)):
        raise ValueError('Global shard partition has duplicates or missing specifications')
    if sorted(manifest['policies'])!=sorted(POLICIES):
        raise ValueError('Only the two frozen original policies are authorized')


def verify_manifest(manifest):
    from hospital_ies.dataset_manifest import digest, verify_execution_source, runtime_versions
    verify_execution_source(ROOT)
    body = dict(manifest)
    signature = body.pop('signature')
    if signature != digest(body):
        raise ValueError('Global manifest content mismatch')
    if manifest['runtime'] != runtime_versions():
        raise ValueError('Runtime dependency versions changed; use a separately versioned run')
    validate_partition(manifest)
    for item in manifest['files'] + manifest['p2b_dependencies']:
        if sha((ROOT/item['path']).read_bytes()) != item['sha256']:
            raise ValueError('Frozen dependency changed: '+item['path'])


def run_shard(manifest_path, shard_index):
    from hospital_ies.control_env import HospitalIESEnv
    from hospital_ies.dataset_export import run_trajectory
    manifest_path = Path(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    verify_manifest(manifest)
    batch = manifest['p2b_shards'][int(shard_index)]
    by_id = {s['id']:s for s in manifest['scenarios']}
    scenarios = [by_id[sid] for sid in batch['scenario_ids']]
    env = HospitalIESEnv(ROOT, execution_contract=manifest['execution_contract'])
    def simulate(s, policy):
        return run_trajectory(env, s, policy, manifest['options']['seed'], .35, manifest, (15,30,60))
    out = manifest_path.parent/'shards'/batch['id']
    result = execute_pairs(scenarios, out, manifest['signature'], simulate)
    verify_manifest(manifest)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('manifest', type=Path)
    parser.add_argument('shard', type=int)
    args = parser.parse_args()
    print(json.dumps(run_shard(args.manifest, args.shard)))
