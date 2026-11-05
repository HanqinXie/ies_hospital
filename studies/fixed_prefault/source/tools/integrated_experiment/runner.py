"""Same-core, failure-complete replay and new development attempt runner."""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]

from hospital_ies.dataset_manifest import build_manifest, digest, runtime_versions, verify_execution_source
from hospital_ies.dataset_export import run_trajectory
from hospital_ies.control_env import HospitalIESEnv
from tools.p2b_experiment.runner import execute_pairs
from tools.p2c_experiment.replay import (OLD, RecordedInitialEnv, original_payload,
                                         build_replay_manifest, validate_parent)
from .dependency import read, sha, reference, checked_reference, validate_core_gate

POLICIES = ('priority_milp', 'secure_milp')
ROLES = ('pilot_train', 'pilot_validation', 'development_boundary')


def validate_partition(m):
    specs = m['scenarios']
    cohort = m['integrated_cohort']
    if cohort == 'original_replay':
        if len(specs) != 400:
            raise ValueError('Original replay must contain exactly 400 specifications')
    elif cohort == 'new_development':
        if len(specs) > 1200:
            raise ValueError('New development attempted specification budget exceeds 1200')
    else:
        raise ValueError('Unregistered cohort')
    ids = [s['id'] for s in specs]
    assigned = [sid for sh in m['p2b_shards'] for sid in sh['scenario_ids']]
    if (len(ids) != len(set(ids)) or len(assigned) != len(set(assigned))
        or sorted(ids) != sorted(assigned)
        or len({sh['id'] for sh in m['p2b_shards']}) != len(m['p2b_shards'])):
        raise ValueError('Shard partition has duplicate or missing specifications')
    if sorted(m['policies']) != sorted(POLICIES):
        raise ValueError('Baseline generation requires both unchanged original policies')
    groups = {}
    for s in specs:
        assignment = m['splits'][s['id']]
        if s.get('is_final_blind') or assignment.get('is_final_blind'):
            raise ValueError('Original sealed final backgrounds remain prohibited')
        role = s['experiment_role']
        if role not in ROLES:
            raise ValueError('Unregistered development role')
        group = assignment['group_id']
        if group in groups and groups[group] != role:
            raise ValueError('A connected background group crosses development roles')
        groups[group] = role


def verify_manifest(m):
    verify_execution_source(ROOT)
    body = copy.deepcopy(m)
    signature = body.pop('signature')
    if digest(body) != signature:
        raise ValueError('Manifest content changed')
    if m['runtime'] != runtime_versions():
        raise ValueError('Runtime backend or installed dependency changed')
    validate_partition(m)
    verify_registered_design(m)
    for item in m['files'] + m.get('p2b_dependencies', []):
        if sha(ROOT / item['path']) != item['sha256']:
            raise ValueError('Frozen dependency changed: ' + item['path'])
    path = checked_reference(m['integrated_core_gate'])
    validate_core_gate(path)


def verify_registered_design(m):
    if m['integrated_cohort'] != 'new_development':
        return
    from .data_design import validate_proposal, manifest_components
    proposal = read(checked_reference(m['integrated_proposal']))
    validate_proposal(proposal)
    if m['integrated_sampling_phase'] == 'train_only_adaptive':
        materialized = read(checked_reference(m['integrated_materialized_adaptive']))
    elif m['integrated_sampling_phase'] == 'fixed_crosscoverage':
        materialized = proposal['fixed_scenarios']
    else:
        raise ValueError('Unregistered sampling phase')
    if sorted(materialized, key=lambda s: s['id']) != m['scenarios']:
        raise ValueError('Actual scenario scripts differ from registered materialization')
    components = manifest_components(proposal, m['scenarios'])
    for key, value in components.items():
        if m.get(key) != value:
            raise ValueError('Registered data design/role changed: ' + key)


def freeze_replay(out, core_gate):
    out = Path(out)
    if out.exists():
        raise FileExistsError('Fresh original replay output required')
    validate_core_gate(core_gate)
    old = read(OLD / 'global_manifest.json')
    m = build_replay_manifest(old)
    m.pop('signature')
    m['manifest_version'] = 'integrated.original_replay.v1'
    m['integrated_cohort'] = 'original_replay'
    m['integrated_core_gate'] = reference(core_gate)
    bindings = []
    for s in old['scenarios']:
        pair = {p: original_payload(old, s['id'], p) for p in POLICIES}
        initial = pair[POLICIES[0]]['initial_state']
        if initial is None or any(p['initial_state'] != initial for p in pair.values()):
            raise ValueError('Original pair has unequal or missing initial state')
        bindings.append({'scenario_id': s['id'], 'initial_sha256': digest(initial),
                         'old_payload_sha256': {p: digest(x) for p, x in pair.items()}})
    m['p2c_original_bindings'] = bindings
    # Only the running orchestrator is frozen here. Unrelated learner tools can
    # evolve without changing this physical data version.
    for rel in ('tools/integrated_experiment/runner.py', 'tools/integrated_experiment/dependency.py'):
        m['p2b_dependencies'].append({'path': rel, 'sha256': sha(ROOT / rel)})
    m['signature'] = digest(m)
    verify_manifest(m)
    out.mkdir(parents=True)
    (out / 'global_manifest.json').write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding='utf-8')
    return m


def freeze_new(out, core_gate, proposal_path, adaptive_path=None):
    """Materialized fixed or train-only adaptive slots; no rejected replacement."""
    from hospital_ies.data_contract import SCHEMA_VERSIONS
    from .data_design import validate_proposal, manifest_components
    out = Path(out)
    if out.exists():
        raise FileExistsError('Fresh new-development output required')
    validate_core_gate(core_gate)
    proposal = read(proposal_path)
    validate_proposal(proposal)
    specs = sorted(read(adaptive_path) if adaptive_path else proposal['fixed_scenarios'],
                   key=lambda scenario: scenario['id'])
    components = manifest_components(proposal, specs)
    contract = read(ROOT / 'configs/p1b_execution_research.json')
    m = build_manifest(ROOT, specs, POLICIES, contract, SCHEMA_VERSIONS,
                       components['options'], components['splits'])
    m.pop('signature')
    m.update(components)
    m.update(manifest_version='integrated.new_development.v1',
             integrated_cohort='new_development', integrated_core_gate=reference(core_gate),
             integrated_proposal=reference(proposal_path),
             integrated_sampling_phase='train_only_adaptive' if adaptive_path else 'fixed_crosscoverage')
    if adaptive_path:
        m['integrated_materialized_adaptive'] = reference(adaptive_path)
    m['p2b_dependencies'] = [{'path': rel, 'sha256': sha(ROOT / rel)} for rel in (
        'tools/integrated_experiment/runner.py', 'tools/integrated_experiment/dependency.py',
        'tools/integrated_experiment/data_design.py', 'tools/p2b_experiment/runner.py')]
    m['signature'] = digest(m)
    verify_manifest(m)
    out.mkdir(parents=True)
    (out / 'global_manifest.json').write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding='utf-8')
    return m


def run_shard(path, index):
    path = Path(path)
    m = read(path)
    verify_manifest(m)
    batch = m['p2b_shards'][int(index)]
    by_id = {s['id']: s for s in m['scenarios']}
    selected = [by_id[sid] for sid in batch['scenario_ids']]
    initial = None
    if m['integrated_cohort'] == 'original_replay':
        old = read(OLD / 'global_manifest.json')
        validate_parent(old, m)
        binding = {r['scenario_id']: r for r in m['p2c_original_bindings']}
        initial = {}
        for s in selected:
            pair = {p: original_payload(old, s['id'], p) for p in POLICIES}
            if any(digest(x) != binding[s['id']]['old_payload_sha256'][p] for p, x in pair.items()):
                raise ValueError('Original payload changed')
            state = pair[POLICIES[0]]['initial_state']
            if digest(state) != binding[s['id']]['initial_sha256']:
                raise ValueError('Original initial state changed')
            initial[s['id']] = state
        env = RecordedInitialEnv(ROOT, execution_contract=m['execution_contract'], recorded_initial_by_id=initial)
    else:
        env = HospitalIESEnv(ROOT, execution_contract=m['execution_contract'])

    def simulate(s, policy):
        payload = run_trajectory(env, s, policy, m['options']['seed'], .35, m, (15, 30, 60))
        if initial is not None:
            payload['replay_initialization_evidence'] = copy.deepcopy(env.replay_initialization_evidence)
            if payload.get('initial_state') != initial[s['id']]:
                raise ValueError('Replay did not restore the exact original initial state')
        return payload

    result = execute_pairs(selected, path.parent / 'shards' / batch['id'], m['signature'], simulate)
    verify_manifest(m)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command', required=True)
    f = sub.add_parser('freeze-replay')
    f.add_argument('out'); f.add_argument('core_gate')
    n = sub.add_parser('freeze-new')
    n.add_argument('out'); n.add_argument('core_gate'); n.add_argument('proposal')
    n.add_argument('--adaptive')
    r = sub.add_parser('run')
    r.add_argument('manifest'); r.add_argument('shard', type=int)
    a = parser.parse_args()
    if a.command == 'freeze-replay':
        result = freeze_replay(a.out, a.core_gate)
        print(json.dumps({'signature': result['signature'], 'specifications': len(result['scenarios'])}))
    elif a.command == 'freeze-new':
        result = freeze_new(a.out, a.core_gate, a.proposal, a.adaptive)
        print(json.dumps({'signature': result['signature'], 'specifications': len(result['scenarios'])}))
    else:
        print(json.dumps(run_shard(a.manifest, a.shard)))
