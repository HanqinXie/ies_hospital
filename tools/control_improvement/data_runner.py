"""Failure-complete new300 baseline runner with all four registered horizons.

Uses the existing physical environment, exporter and atomic attempt store.
Independent learner/auditor development cannot silently alter a frozen producer.
"""
from __future__ import annotations
import argparse,copy,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from hospital_ies.dataset_manifest import build_manifest,digest,inventory,runtime_versions,verify_execution_source
from hospital_ies.data_contract import SCHEMA_VERSIONS
from hospital_ies.dataset_export import run_trajectory
from hospital_ies.control_env import HospitalIESEnv
from tools.p2b_experiment.runner import execute_pairs
from tools.integrated_experiment.dependency import validate_core_gate,sha,read
from .data_design import validate_proposal,manifest_components,initial_training_subset

POLICIES=('priority_milp','secure_milp')
ROLES=('pilot_train','pilot_validation','development_boundary')
PRODUCER_DEPENDENCIES=('tools/control_improvement/data_runner.py','tools/control_improvement/data_design.py',
    'tools/integrated_experiment/data_design.py','tools/p2b_experiment/design.py','tools/p2b_experiment/runner.py','tools/integrated_experiment/dependency.py')
_PRODUCER_SOURCE_AT_IMPORT={p:sha(ROOT/p) for p in PRODUCER_DEPENDENCIES}

def reference(path):
    path=Path(path).resolve()
    try:name=path.relative_to(ROOT).as_posix()
    except ValueError:name=str(path) # Explicit external test/output reference, still byte bound.
    return dict(path=name,sha256=sha(path))

def checked_reference(ref):
    path=ROOT/ref['path']
    if not path.is_file() or sha(path)!=ref['sha256']:raise ValueError('Missing or changed actual reference: '+ref['path'])
    return path

def validate_partition(m):
    specs=m['scenarios'];ids=[s['id'] for s in specs]
    if not 0<len(ids)<=1200:raise ValueError('Attempted specification budget exceeds1200')
    assigned=[sid for sh in m['p2b_shards'] for sid in sh['scenario_ids']]
    if len(set(ids))!=len(ids) or len(set(assigned))!=len(assigned) or sorted(ids)!=sorted(assigned) or len({s['id'] for s in m['p2b_shards']})!=len(m['p2b_shards']):
        raise ValueError('Global shard partition has duplicate/missing specifications')
    if sorted(m['policies'])!=sorted(POLICIES):raise ValueError('Two unchanged original policies required')
    groups={}
    for s in specs:
        a=m['splits'][s['id']];role=s['experiment_role']
        if s.get('is_final_blind') or a.get('is_final_blind'):raise ValueError('Original sealed final backgrounds prohibited')
        if role not in ROLES or a.get('experiment_role')!=role:raise ValueError('Unregistered or changed role')
        gid=a['group_id']
        if gid in groups and groups[gid]!=role:raise ValueError('Connected group crosses roles')
        groups[gid]=role

def verify_registered_design(m):
    p=read(checked_reference(m['control_improvement_proposal']));validate_proposal(p)
    if m['scenarios']!=sorted(p['fixed_scenarios'],key=lambda s:s['id']):raise ValueError('Registered complete fixed300 scenarios changed')
    c=manifest_components(p,m['scenarios'])
    for k,v in c.items():
        if m.get(k)!=v:raise ValueError('Registered design metadata changed: '+k)
    return True

def verify_manifest(m):
    verify_execution_source(ROOT)
    for rel,expected in _PRODUCER_SOURCE_AT_IMPORT.items():
        if sha(ROOT/rel)!=expected:raise ValueError('Data producer changed after import; restart before new version')
    body=copy.deepcopy(m);signature=body.pop('signature')
    if digest(body)!=signature:raise ValueError('Manifest content/signature changed')
    if m['runtime']!=runtime_versions():raise ValueError('Runtime/backend dependency changed')
    validate_partition(m)
    if m.get('manifest_version')!='control_improvement.fixed_new300.v1':raise ValueError('Unregistered manifest version')
    if m['files']!=inventory(ROOT):raise ValueError('Physical source/input inventory changed')
    if m['schemas']!=SCHEMA_VERSIONS:raise ValueError('Exporter schemas changed')
    if m['execution_contract']!=read(ROOT/'configs/p1b_execution_research.json'):raise ValueError('Execution capability/configuration changed')
    for item in m['p2b_dependencies']:
        if sha(ROOT/item['path'])!=item['sha256']:raise ValueError('Declared producer dependency changed: '+item['path'])
    expected_paths=set(PRODUCER_DEPENDENCIES)
    if {x['path'] for x in m['p2b_dependencies']}!=expected_paths:raise ValueError('Producer dependency closure incomplete')
    validate_core_gate(checked_reference(m['control_improvement_core_gate']))
    verify_registered_design(m)
    return True

def freeze_new(out,old_core_gate,proposal_path):
    out=Path(out)
    if out.exists():raise FileExistsError('Fresh output required; no old resume mixture')
    validate_core_gate(old_core_gate)
    p=read(proposal_path);validate_proposal(p)
    specs=sorted(p['fixed_scenarios'],key=lambda s:s['id']);c=manifest_components(p,specs)
    m=build_manifest(ROOT,specs,POLICIES,read(ROOT/'configs/p1b_execution_research.json'),SCHEMA_VERSIONS,c['options'],c['splits'])
    m.pop('signature');m.update(c)
    m.update(manifest_version='control_improvement.fixed_new300.v1',control_improvement_cohort='new_fixed_development',
        control_improvement_core_gate=reference(old_core_gate),control_improvement_proposal=reference(proposal_path),
        control_improvement_first_batch_ids=initial_training_subset(p,8),
        p2b_dependencies=[dict(path=rel,sha256=sha(ROOT/rel)) for rel in PRODUCER_DEPENDENCIES])
    m['signature']=digest(m);verify_manifest(m)
    out.mkdir(parents=True);(out/'global_manifest.json').write_text(json.dumps(m,indent=2,ensure_ascii=False),encoding='utf-8')
    return m

def run_shard(path,index,scenario_ids=None):
    path=Path(path);m=read(path);verify_manifest(m);batch=m['p2b_shards'][int(index)]
    owned=batch['scenario_ids']
    if scenario_ids is not None:
        if len(set(scenario_ids))!=len(scenario_ids) or not set(scenario_ids)<=set(owned):raise ValueError('Initial subset outside registered shard')
        owned=[sid for sid in owned if sid in set(scenario_ids)]
    if not owned:return dict(attempted=0,checkpoints=0,status_counts={},manifest_signature=m['signature'])
    byid={s['id']:s for s in m['scenarios']};scenarios=[byid[sid] for sid in owned]
    env=HospitalIESEnv(ROOT,execution_contract=m['execution_contract'])
    def simulate(s,policy):
        return run_trajectory(env,s,policy,m['options']['seed'],m['options']['security_weight'],m,tuple(m['options']['label_horizons']))
    result=execute_pairs(scenarios,path.parent/'shards'/batch['id'],m['signature'],simulate)
    verify_manifest(m)
    return result

def run_initial8(path):
    """16 registered attempts, later whole-shard replay reuses their checkpoints."""
    path=Path(path);m=read(path);verify_manifest(m);selected=set(m['control_improvement_first_batch_ids']);out=[]
    for index,shard in enumerate(m['p2b_shards']):
        ids=[sid for sid in shard['scenario_ids'] if sid in selected]
        if ids:out.append(dict(shard=shard['id'],result=run_shard(path,index,ids)))
    return dict(registered_specifications=8,maximum_attempts=16,shards=out)

if __name__=='__main__':
    p=argparse.ArgumentParser();sub=p.add_subparsers(dest='command',required=True)
    f=sub.add_parser('freeze-new');f.add_argument('out');f.add_argument('core_gate');f.add_argument('proposal')
    r=sub.add_parser('run');r.add_argument('manifest');r.add_argument('shard',type=int)
    i=sub.add_parser('initial8');i.add_argument('manifest')
    a=p.parse_args()
    if a.command=='freeze-new':
        m=freeze_new(a.out,a.core_gate,a.proposal);print(json.dumps(dict(signature=m['signature'],specifications=len(m['scenarios']),label_horizons=m['options']['label_horizons'])))
    elif a.command=='run':print(json.dumps(run_shard(a.manifest,a.shard)))
    else:print(json.dumps(run_initial8(a.manifest)))
