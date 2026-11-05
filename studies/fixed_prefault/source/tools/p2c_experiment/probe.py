"""Registered fixed q_probe, after closed solver and complete same-version replay."""
from __future__ import annotations
import argparse
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT),str(ROOT/'src')]
from hospital_ies.dataset_manifest import build_manifest, digest
from hospital_ies.dataset_export import run_trajectory
from hospital_ies.control_env import HospitalIESEnv
from hospital_ies.resilience import validate_scenario
from tools.p2b_experiment.design import assign_components, support_interval
from tools.p2b_experiment.runner import execute_pairs, verify_manifest
from tools.p2c_experiment.replay import validate_gate, file_hash
from tools.p2c_experiment.release import validate_release, resolve_evidence, ref


def pressure_values(u):
    if not 0 <= u <= 1:
        raise ValueError('Pressure path outside registered support')
    anchors = np.array([[0,0,.2,0],[.5,.5,.2,0],[1.3,1.1,.2,.2],[2.1,1.25,.2,.5]])
    k = min(int(u*3),2); w = u*3-k
    return (anchors[k]*(1-w)+anchors[k+1]*w).tolist()


def supported_pressure(v):
    e,c,h,p = v
    return ((0 <= e <= .5 and 0 <= c <= .5 and 0 <= h <= .25 and p == 0)
        or (.4-1e-12 <= e <= 1.3+1e-12 and .3-1e-12 <= c <= 1.1+1e-12
            and .1 <= h <= .5 and 0 <= p <= .2+1e-12)
        or (.8 <= e <= 2.1+1e-12 and .45 <= c <= 1.25+1e-12 and .05 <= h <= .55
            and .2-1e-12 <= p <= .9))


def assignments_for_specs(specs):
    rows = []
    roles = {}
    for s in specs:
        role = s['experiment_role']
        if role not in ('pilot_train','pilot_validation'):
            raise ValueError('Probe must use registered development train/validation roles')
        key = s['root_family_id']
        if key in roles and roles[key] != role:
            raise ValueError('Probe family crossed roles')
        roles[key] = role
        rows.append(dict(scenario_id=s['id'],family_id=s['family_id'],
            parent_template_id=s['parent_template_id'],base_state_id=s['base_state_id'],
            exogenous_stream_id=s['exogenous_stream_id'],time_block_id=s['time_block_id'],
            role=role,**support_interval(s)))
    return assign_components(rows)


def replay_ready(path):
    audit = json.loads(Path(path).read_text(encoding='utf-8'))
    if (audit.get('passed') is not True or audit.get('complete_budget') is not True
        or audit.get('attempted_trajectories') != 800 or audit.get('checkpoints') != 800
        or audit.get('attempted_specifications') != 400 or audit.get('complete_pairs') != 400):
        raise ValueError('Probe requires audited complete uniform replay')
    # Newly unexplained errors require source diagnosis, not automatic probe generation.
    if audit.get('score_unknown',0) or any(k != 'completed' and v for k,v in audit.get('status_counts',{}).items()):
        raise ValueError('Unresolved new replay outcomes: review their solver paths before probes')
    return audit


def freeze_fixed(proposal_path, replay_manifest, replay_audit, gate_path, out, release_path=None):
    out = Path(out)
    if out.exists():
        raise FileExistsError('Fresh probe output required')
    validate_release(release_path)
    validate_gate(gate_path)
    audit = replay_ready(replay_audit)
    replay = json.loads(Path(replay_manifest).read_text(encoding='utf-8'))
    verify_manifest(replay)
    if audit['manifest_signature'] != replay['signature']:
        raise ValueError('Probe replay audit/manifest mismatch')
    proposal = json.loads(Path(proposal_path).read_text(encoding='utf-8'))
    body = copy.deepcopy(proposal); signature = body.pop('proposal_sha256')
    if digest(body) != signature:
        raise ValueError('Pre-result probe proposal changed')
    specs = proposal['fixed_scenarios']
    if len(specs) != 80 or len({s['id'] for s in specs}) != 80:
        raise ValueError('Fixed probe must retain all registered 80 specifications')
    sealed = set(proposal['exposure']['sealed_final_days'])
    blocks = {b['time_block_id']:b for b in proposal['blocks']}
    for s in specs:
        validate_scenario(s)
        if blocks[s['time_block_id']]['day_index'] in sealed:
            raise ValueError('Sealed final background in probe')
        if not supported_pressure([s['shock']['e'],s['shock']['c'],s['shock']['h'],
            s.get('shock_by_priority',{}).get('P0',{}).get('e',0)]):
            raise ValueError('Pressure outside original joint support')
    assignments = assignments_for_specs(specs)
    m = build_manifest(ROOT,specs,replay['policies'],replay['execution_contract'],replay['schemas'],
        replay['options'],assignments)
    m.pop('signature')
    m.update(manifest_version='p2c.fixed_probe.v1',
        registered_utc=datetime.now(timezone.utc).isoformat(),
        probability_semantics='q_probe registered coverage; no density or real accident probability',
        p2c_parent_replay_signature=replay['signature'],
        p2c_probe_proposal_sha256=file_hash(proposal_path),
        p2c_replay_audit=ref(replay_audit),p2c_release_gate=ref(release_path),
        p2c_S_gate=ref(gate_path),
        p2b_time_blocks=proposal['blocks'],p2b_study_distribution={},
        p2b_shards=[dict(id=f'shard_{i:02d}',scenario_ids=[s['id'] for s in specs[i*40:(i+1)*40]]) for i in range(2)],
        p2b_dependencies=[dict(path=str(Path(p).resolve().relative_to(ROOT).as_posix()),sha256=file_hash(p)) for p in
            [Path(__file__),ROOT/'tools/p2c_experiment/replay.py',ROOT/'tools/p2c_experiment/release.py',
             ROOT/'tools/p2b_experiment/design.py',ROOT/'tools/p2b_experiment/runner.py',
             ROOT/'P2C_IMPLEMENTATION_PLAN.md',Path(proposal_path)]])
    m['signature'] = digest(m)
    verify_manifest(m)
    out.mkdir(parents=True)
    (out/'global_manifest.json').write_text(json.dumps(m,ensure_ascii=False,indent=2),encoding='utf-8')
    return m


def run(manifest_path, index):
    path = Path(manifest_path);m = json.loads(path.read_text(encoding='utf-8'))
    verify_manifest(m)
    validate_release(m['p2c_release_gate']['path'])
    if file_hash(resolve_evidence(m['p2c_release_gate']['path'])) != m['p2c_release_gate']['sha256']:
        raise ValueError('Global regression release changed')
    validate_gate(m['p2c_S_gate']['path'])
    if file_hash(resolve_evidence(m['p2c_S_gate']['path'])) != m['p2c_S_gate']['sha256']:
        raise ValueError('Solver gate evidence drift')
    replay_ready(resolve_evidence(m['p2c_replay_audit']['path']))
    if file_hash(resolve_evidence(m['p2c_replay_audit']['path'])) != m['p2c_replay_audit']['sha256']:
        raise ValueError('Replay audit changed')
    batch = m['p2b_shards'][index]
    by_id = {s['id']:s for s in m['scenarios']}
    env = HospitalIESEnv(ROOT,execution_contract=m['execution_contract'])
    def simulate(s,p):
        return run_trajectory(env,s,p,m['options']['seed'],m['options']['security_weight'],m,(15,30,60))
    result = execute_pairs([by_id[s] for s in batch['scenario_ids']],path.parent/'shards'/batch['id'],m['signature'],simulate)
    verify_manifest(m)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command',required=True)
    f = sub.add_parser('freeze');f.add_argument('proposal');f.add_argument('replay_manifest');f.add_argument('replay_audit');f.add_argument('gate');f.add_argument('out');f.add_argument('release')
    r = sub.add_parser('run');r.add_argument('manifest');r.add_argument('shard',type=int)
    args = parser.parse_args()
    if args.command == 'freeze':
        m = freeze_fixed(args.proposal,args.replay_manifest,args.replay_audit,args.gate,args.out,args.release)
        print(json.dumps(dict(signature=m['signature'],specifications=len(m['scenarios']))))
    else:
        print(json.dumps(run(args.manifest,args.shard)))
