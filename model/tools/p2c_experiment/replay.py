"""Fresh P2C replay of frozen specifications with their exact recorded initial state."""
from __future__ import annotations
import argparse
import copy
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
from hospital_ies.control_env import HospitalIESEnv
from hospital_ies.resilience import RuntimeState
from hospital_ies.state_transition import TransitionCore, clean
from hospital_ies.dataset_manifest import build_manifest, digest
from hospital_ies.dataset_export import run_trajectory
from tools.p2b_experiment.runner import load_payload, execute_pairs, verify_manifest
from tools.p2c_experiment.release import validate_release, resolve_evidence, ref

OLD = ROOT / 'outputs/v43_p2b_20261013_run01/experiment'


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def assert_not_revoked(gate, gate_dir):
    """Reject a locally closed source when later raw replay has reopened S.

    Historic certificates are retained unchanged. A different numerical source
    still has to pass every ordinary release check; this is not its approval.
    """
    source = {k.replace('\\', '/'): v for k, v in gate.get('source_sha256', {}).items()}
    for path in sorted(Path(gate_dir).glob('S_REOPENED*_FAILED.json')):
        marker = json.loads(path.read_text(encoding='utf-8'))
        failed_source = {k.replace('\\', '/'): v
                         for k, v in marker.get('source_sha256', {}).items()}
        required = {'src/hospital_ies/resilience.py',
                    'src/hospital_ies/solver_diagnostics.py'}
        if not required <= failed_source.keys():
            raise ValueError('S gate revocation lacks numerical source binding')
        if not all(source.get(k) == v for k, v in failed_source.items()):
            continue
        if marker.get('passed') is not False or not marker.get('unresolved_ids'):
            raise ValueError('S gate revocation lacks unresolved raw case identities')
        for key in ('actual_new_failure_summary', 'new4_files_manifest',
                    'actual_partial_audit'):
            evidence = resolve_evidence(marker.get(key + '_path', ''))
            if not evidence.is_file() or file_hash(evidence) != marker.get(key + '_sha256'):
                raise ValueError('revocation raw evidence changed: ' + key)
        raise ValueError('S gate reopened for current numerical source: '
                         + ', '.join(marker['unresolved_ids']))


def validate_gate(path):
    path = resolve_evidence(path)
    gate = json.loads(path.read_text(encoding='utf-8'))
    if (gate.get('passed') is not True or gate.get('attempted_original_failures') != 111
            or gate.get('reproduced_original_failures') != 111
            or gate.get('corrected_full_lex_success') != 111
            or gate.get('unresolved_ids') or gate.get('tolerance_unchanged') is not True
            or not gate.get('source_sha256')):
        raise ValueError('S gate has unresolved inheritance or incomplete evidence')
    required = {'src/hospital_ies/resilience.py','src/hospital_ies/solver_diagnostics.py'}
    if not required <= {k.replace('\\','/') for k in gate['source_sha256']}:
        raise ValueError('S gate must bind both solver implementations')
    evidence = {}
    for name in ('original_summary','corrected_summary','case_files_manifest'):
        evidence_path = resolve_evidence(gate.get(name+'_path',''))
        if not evidence_path.is_file() or file_hash(evidence_path) != gate.get(name+'_sha256'):
            raise ValueError('S gate missing or changed raw evidence: ' + name)
        evidence[name] = json.loads(evidence_path.read_text(encoding='utf-8'))
    # A counts-only object must first fail the original raw-evidence contract.
    # A real historic certificate remains revoked even when moved elsewhere.
    assert_not_revoked(gate, path.parent)
    known = ROOT / 'outputs/v43_p2c_20261013_run01/solver_diagnostics'
    if path.parent.resolve() != known.resolve():
        assert_not_revoked(gate, known)
    # Raw evidence and historical revocation precede the live source check.
    # A replaced implementation does not make a revoked version acceptable.
    for rel, expected in gate['source_sha256'].items():
        if file_hash(ROOT / rel) != expected:
            raise ValueError('S gate solver source changed: ' + rel)
    original = evidence['original_summary']['records']
    corrected = evidence['corrected_summary']['records']
    originals = {r['case_id']:r for r in original}
    corrections = {r['case_id']:r for r in corrected}
    if len(original) != 111 or len(corrected) != 111 or len(originals) != 111 or set(originals) != set(corrections):
        raise ValueError('S gate raw case coverage is not exactly 111 unique pairs')
    if any(r['reproduced_status'] != 2 or r['reproduced_rank'] != r['expected_old_rank']
           or not r['frozen_source_equivalence']['exact_layer_arrays_and_options_equal']
           or r['previous_witness_max_original_violation'] > 1e-7
           or r['previous_witness_max_lock_violation'] > 1e-10
           or r['previous_witness_max_integrality_distance'] > 1e-9 for r in original):
        raise ValueError('S gate original witness evidence is incomplete')
    if any(not r['corrected_full_lex_success'] or r['all_5_layer_status'] != [0]*5
           or not r['inherited_witnesses_valid'] or not r['original_base_arrays_unchanged']
           or not r['positive_scaling_equivalence']
           or r['worst_layer_original_violation'] > 1e-7
           or r['worst_layer_lock_violation'] > 1e-10 for r in corrected):
        raise ValueError('S gate corrected raw layers are not closed')
    for rel, item in evidence['case_files_manifest']['files'].items():
        p = resolve_evidence(gate['case_files_manifest_path']).parent / rel
        if not p.is_file() or p.stat().st_size != item['bytes'] or file_hash(p) != item['sha256']:
            raise ValueError('S gate raw case file changed: ' + rel)
    return gate


def original_payload(manifest, sid, policy):
    batch = next(s for s in manifest['p2b_shards'] if sid in s['scenario_ids'])
    return load_payload(OLD / 'shards' / batch['id'], sid, policy, manifest['signature'])


def validate_parent(old, child=None):
    body = copy.deepcopy(old)
    signature = body.pop('signature')
    if digest(body) != signature:
        raise ValueError('Changed parent manifest content with stale signature')
    frozen = json.loads((ROOT/'outputs/v43_p2c_20261013_run01/manifest_before.json').read_text(encoding='utf-8'))
    rel = (OLD/'global_manifest.json').relative_to(ROOT).as_posix()
    expected = next(r['sha256'] for r in frozen['files'] if r['path'] == rel)
    if file_hash(OLD/'global_manifest.json') != expected:
        raise ValueError('Original parent manifest changed since P2C freeze')
    if json.loads((OLD/'global_manifest.json').read_text(encoding='utf-8')) != old:
        raise ValueError('Supplied parent differs from frozen original manifest')
    if child and (child['p2c_parent_signature'] != signature
                  or child['p2c_parent_manifest_sha256'] != expected):
        raise ValueError('Child parent binding changed')
    if child:
        for field in ('scenarios','policies','execution_contract','schemas','options','splits'):
            if child[field] != old[field]:
                raise ValueError('Re-signed replay changed frozen parent field: ' + field)
        for field in old:
            if field.startswith('p2b_') and field != 'p2b_dependencies' and child[field] != old[field]:
                raise ValueError('Re-signed replay changed frozen design/support: ' + field)
        if child['p2b_dependencies'][:len(old['p2b_dependencies'])] != old['p2b_dependencies']:
            raise ValueError('Original design dependencies removed from replay')


def runtime_state(record):
    state = copy.deepcopy(record)
    for key in ('gt_prev','overload_minutes','disconnected_loads'):
        state[key] = np.asarray(state[key], dtype=float)
    state['extra_faulted'] = set(state['extra_faulted'])
    return RuntimeState(**state)


class RecordedInitialEnv(HospitalIESEnv):
    """Replay-only restoration; ordinary reset and simulation semantics are untouched."""
    def __init__(self, *args, recorded_initial_by_id, **kwargs):
        super().__init__(*args, **kwargs)
        self.recorded_initial_by_id = copy.deepcopy(recorded_initial_by_id)

    def reset(self, *, scenario, seed=None):
        _, info = super().reset(scenario=scenario, seed=seed)
        computed = clean(dict(state=asdict(self.state), executed=self.core.executed))
        recorded = copy.deepcopy(self.recorded_initial_by_id[scenario['id']])
        self.state = runtime_state(recorded['state'])
        self.core = TransitionCore(self, recorded['executed'])
        # Construction recomputes derived source/auxiliary fields; confirm that
        # the original state is already consistent rather than overwriting them.
        actual = clean(dict(state=asdict(self.state), executed=self.core.executed))
        if actual != recorded:
            raise ValueError('Recorded initial state and ports are not internally consistent')
        self.event_history = self.core.events
        self.segment_history = self.core.segments
        self.solver_errors = self.core.errors
        self.replay_initialization_evidence = dict(computed=computed, recorded=recorded,
            default_initializer_equal=computed == recorded,
            policy='exact recorded P2B state and executed ports')
        return self._observation(), info


def build_replay_manifest(old):
    if any(s['experiment_role'] not in ('pilot_train','pilot_validation','development_boundary')
           for s in old['scenarios']):
        raise ValueError('Unexpected or final result role in replay')
    if len(old['scenarios']) != 400:
        raise ValueError('Replay must include the original 400 specifications')
    validate_parent(old)
    m = build_manifest(ROOT, old['scenarios'], old['policies'], old['execution_contract'],
        old['schemas'], old['options'], old['splits'])
    m.pop('signature')
    for key, value in old.items():
        if key.startswith('p2b_'):
            m[key] = copy.deepcopy(value)
    m.update(manifest_version='p2c.uniform_replay.v1',
        p2c_parent_signature=old['signature'], p2c_parent_manifest_sha256=file_hash(OLD/'global_manifest.json'),
        p2c_initial_state_policy='exact recorded P2B state and executed ports',
        p2c_claim='same historical development families, not new independent accidents or a blind test',
        registered_utc=datetime.now(timezone.utc).isoformat())
    for rel in ('tools/p2c_experiment/replay.py','tools/p2c_experiment/release.py','P2C_IMPLEMENTATION_PLAN.md'):
        m['p2b_dependencies'].append(dict(path=rel, sha256=file_hash(ROOT/rel)))
    m['signature'] = digest(m)
    verify_manifest(m)
    return m


def freeze(out, gate_path, release_path=None):
    out = Path(out)
    if out.exists():
        raise FileExistsError('New independent replay directory required')
    validate_release(release_path)
    gate = validate_gate(gate_path)
    old = json.loads((OLD/'global_manifest.json').read_text(encoding='utf-8'))
    m = build_replay_manifest(old)
    m.pop('signature')
    m['p2c_release_gate'] = ref(release_path)
    m['p2c_S_gate'] = dict(**ref(gate_path),
                         numerical_version=gate.get('numerical_version'))
    # Bind every old checkpoint and initial state, including the 111 failed prefixes.
    bindings = []
    for spec in old['scenarios']:
        pair = [original_payload(old,spec['id'],p) for p in old['policies']]
        if pair[0]['initial_state'] != pair[1]['initial_state']:
            raise ValueError('Original paired initial conditions differ')
        bindings.append(dict(scenario_id=spec['id'], initial_sha256=digest(pair[0]['initial_state']),
            old_payload_sha256={p:digest(x) for p,x in zip(old['policies'],pair)}))
    m['p2c_original_bindings'] = bindings
    m['signature'] = digest(m)
    verify_manifest(m)
    out.mkdir(parents=True)
    (out/'global_manifest.json').write_text(json.dumps(m,ensure_ascii=False,indent=2),encoding='utf-8')
    return m


def run_shard(manifest_path, index):
    path = Path(manifest_path)
    m = json.loads(path.read_text(encoding='utf-8'))
    validate_release(m['p2c_release_gate']['path'])
    if file_hash(resolve_evidence(m['p2c_release_gate']['path'])) != m['p2c_release_gate']['sha256']:
        raise ValueError('Global regression release changed after freeze')
    validate_gate(m['p2c_S_gate']['path'])
    if file_hash(resolve_evidence(m['p2c_S_gate']['path'])) != m['p2c_S_gate']['sha256']:
        raise ValueError('S gate evidence changed after freeze')
    verify_manifest(m)
    old = json.loads((OLD/'global_manifest.json').read_text(encoding='utf-8'))
    validate_parent(old,m)
    batch = m['p2b_shards'][int(index)]
    specs = {s['id']:s for s in m['scenarios']}
    selected = [specs[sid] for sid in batch['scenario_ids']]
    bindings = {r['scenario_id']:r for r in m['p2c_original_bindings']}
    initial = {}
    for s in selected:
        pair = {p:original_payload(old,s['id'],p) for p in old['policies']}
        binding = bindings[s['id']]
        if any(digest(x) != binding['old_payload_sha256'][p] for p,x in pair.items()):
            raise ValueError('Original payload changed after replay freeze')
        state = pair['priority_milp']['initial_state']
        if digest(state) != binding['initial_sha256'] or any(x['initial_state'] != state for x in pair.values()):
            raise ValueError('Original initial condition binding changed')
        initial[s['id']] = state
    env = RecordedInitialEnv(ROOT, execution_contract=m['execution_contract'], recorded_initial_by_id=initial)
    def simulate(s, policy):
        p = run_trajectory(env,s,policy,m['options']['seed'],m['options']['security_weight'],m,(15,30,60))
        p['replay_initialization_evidence'] = copy.deepcopy(getattr(env,'replay_initialization_evidence',None))
        if p.get('initial_state') is not None and p['initial_state'] != initial[s['id']]:
            raise ValueError('Replay initial state differs from frozen source')
        return p
    result = execute_pairs(selected,path.parent/'shards'/batch['id'],m['signature'],simulate)
    verify_manifest(m)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command',required=True)
    f = sub.add_parser('freeze'); f.add_argument('out'); f.add_argument('gate'); f.add_argument('release')
    r = sub.add_parser('run'); r.add_argument('manifest'); r.add_argument('shard',type=int)
    a = parser.parse_args()
    if a.command == 'freeze':
        m = freeze(a.out,a.gate,a.release)
        print(json.dumps(dict(signature=m['signature'],specifications=len(m['scenarios']))))
    else:
        print(json.dumps(run_shard(a.manifest,a.shard)))
