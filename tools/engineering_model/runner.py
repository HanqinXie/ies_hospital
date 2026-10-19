"""Thin immutable v14 campaigns using the existing exporter and original scores."""
from __future__ import annotations
import argparse
import copy
from datetime import datetime,timezone
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import sys
import time
ROOT=Path(__file__).resolve().parents[2]
RUN=ROOT/'outputs/v14_engineering_model_20261017_run01'
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from tools.executed_service import closed_loop as prior
from .environment import EngineeringHospitalIESEnv,model_contract,run_episode
from .policy import EngineeringPolicy
from .data_design import build_manifest,digest,SEALED_DAYS
from .analysis import analyze_payload,export_rows


def read(path):return json.loads(Path(path).read_text(encoding='utf-8'))
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def now():return datetime.now(timezone.utc).isoformat()


def write(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf-8') as f:json.dump(value,f,ensure_ascii=False,indent=2,allow_nan=False)


def journal(path,**value):
    with path.open('a',encoding='utf-8') as f:f.write(json.dumps(dict(utc=now(),**value),ensure_ascii=False,allow_nan=False)+'\n')


def check_output_path(path):
    value=Path(path).resolve()
    if value.drive.lower()=='c:':raise ValueError('Research data must use D/E, not C')
    return value


def source_binding():
    files=set(prior.SOURCE_FILES)
    files.update(p.relative_to(ROOT).as_posix() for p in (ROOT/'tools/engineering_model').glob('*.py'))
    files.update(('configs/p1b_execution_research.json','configs/resilience_assumptions.json','configs/plant_configs.json'))
    return {name:sha(ROOT/name) for name in sorted(files)}


def policy_specs():
    old=read(ROOT/'outputs/v13_executed_service_20261017_run01/design/DEVELOPMENT_POLICY_SPECS.json')
    f=next(x for x in old if x['policy_id']=='fixed_standby_constant_v12b')
    h=next(x for x in old if x['policy_id']=='executed_loss_feedback_v13')
    z=next(x for x in old if x['policy_id']=='static_zero_v13')
    common=dict(diagnose_full_supply=True,diagnose_hold_combination=True)
    return dict(F=f,H=h,Z=z,
        S=dict(policy_id='service_restore_stable_v14',kind='engineering',config=dict(common,mode='service_restore')),
        E=dict(policy_id='endurance_restore_p0_ready_v14r6',kind='engineering',config=dict(common,mode='endurance_restore',grid_headroom_preference=True,execution_aware_commitment_preference=True,p0_preparation_before_commitment=True)),
        C=dict(policy_id='endurance_contacts_v14',kind='engineering',config=dict(common,mode='endurance_restore',contact_mode='separate')))


def factory(spec):
    return (EngineeringPolicy(copy.deepcopy(spec['config']),spec['policy_id']) if spec['kind']=='engineering'
        else prior._factory(spec))


def matching_evidence(payloads):
    """Compare actual reset states and applied exogenous definitions, not names."""
    states=[p.get('initial_state') for p in payloads]
    fields=('start_hour','duration_steps','step_minutes','faults','shock','shock_by_priority',
        'exogenous_events','propagation_enabled','grid_limit_kW')
    streams=[{k:v for k,v in p['scenario'].items() if k in fields or k.startswith(('fault_','shock_'))}
        for p in payloads]
    state_hashes=[digest(s) if s is not None else None for s in states]
    stream_hashes=[digest(s) for s in streams]
    return dict(initial_state_sha256=state_hashes,applied_exogenous_definition_sha256=stream_hashes,
        same_initial_state=bool(states) and all(s is not None for s in states) and len(set(state_hashes))==1,
        same_exogenous_definition=bool(streams) and len(set(stream_hashes))==1,
        meaning='Actual reset state and applied scenario definition; not a stochastic independence claim')


def register(proposal,directory,specs,*,model_id,model_config,scenario_ids=None,phase='development'):
    directory=check_output_path(directory)
    if directory.exists():raise FileExistsError('Use fresh registration; old attempts are immutable')
    _,model_config=model_contract(model_id,model_config)
    specs=copy.deepcopy(list(specs));ids=[s['policy_id'] for s in specs]
    if not specs or len(specs)>8 or len(ids)!=len(set(ids)):raise ValueError('Distinct finite policy panel required')
    for spec in specs:factory(spec)
    proposal=copy.deepcopy(proposal)
    if scenario_ids is not None:
        wanted=set(scenario_ids);proposal['scenarios']=[s for s in proposal['scenarios'] if s['id'] in wanted]
        if len(proposal['scenarios'])!=len(wanted):raise ValueError('Missing registered specification')
    if not proposal['scenarios']:raise ValueError('Empty specification panel')
    count=len(proposal['scenarios'])*len(specs)
    existing=sum(read(p)['registered_pairs'] for p in (RUN/'experiments').glob('*/plan.json'))
    if existing+count>336:raise ValueError('Finite v14 registered episode ceiling exceeded')
    binding=source_binding()
    manifest=build_manifest(proposal,binding,model_id,model_config,root=ROOT,policies=ids)
    plan=dict(schema='hospital_ies.engineering_batch.v14.v1',registered_utc=now(),phase=phase,
        model_id=model_id,model_config=copy.deepcopy(model_config),policy_specs=specs,
        scenario_ids=[s['id'] for s in manifest['scenarios']],registered_pairs=count,
        source_sha256=binding,manifest_signature=manifest['signature'],manifest_sha256=None,
        original_final12='not_verified; unopened',no_figures=True,no_papers=True)
    directory.mkdir(parents=True)
    write(directory/'manifest.json',manifest);plan['manifest_sha256']=sha(directory/'manifest.json')
    write(directory/'plan.json',plan)
    # Small additive code/config snapshots preserve failed development revisions.
    for name in binding:
        if name.startswith('tools/engineering_model/'):
            destination=directory/'source_snapshot'/name
            destination.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/name,destination)
    return plan


def run_batch(directory):
    directory=check_output_path(directory);plan=read(directory/'plan.json');manifest=read(directory/'manifest.json')
    if sha(directory/'manifest.json')!=plan['manifest_sha256'] or source_binding()!=plan['source_sha256']:
        raise ValueError('Changed registration or source; never mix versions')
    if (directory/'BATCH_RESULT.json').exists():raise FileExistsError('Completed evidence is immutable')
    hours=set()
    for scene in manifest['scenarios']:
        first=int(scene['start_hour']);last=first+int(__import__('math').ceil(scene['duration_steps']*scene['step_minutes']/60.))
        hours.update(range(first,last+1))
    env=EngineeringHospitalIESEnv(ROOT,allowed_hours=hours,sealed_days=SEALED_DAYS,
        model_id=plan['model_id'],model_config=plan['model_config'])
    records=[];matches={};started=time.perf_counter()
    for scene in manifest['scenarios']:
        matching_payloads=[]
        for spec in plan['policy_specs']:
            if source_binding()!=plan['source_sha256']:raise ValueError('Source changed while a physical campaign was active')
            pair=directory/'pairs'/(scene['id']+'__'+spec['policy_id'])
            pair.mkdir(parents=True,exist_ok=False)
            journal(directory/'ATTEMPTS.jsonl',event='started',scenario_id=scene['id'],model_id=env.model_id,policy_id=spec['policy_id'])
            began=time.perf_counter()
            payload=run_episode(env,scene,factory(spec),int(scene.get('initialization_seed',20261017)),manifest)
            matching_payloads.append(dict(initial_state=payload.get('initial_state'),scenario=payload['scenario']))
            compressed=gzip.compress(json.dumps(payload,ensure_ascii=False,allow_nan=False).encode('utf-8'),mtime=0)
            raw=pair/'trajectory.json.gz';raw.write_bytes(compressed)
            raw_sha=hashlib.sha256(compressed).hexdigest()
            try:analysis=analyze_payload(payload,env)
            except Exception as exc:
                write(pair/'analysis_error.json',dict(type=type(exc).__name__,message=str(exc),raw_sha256=raw_sha,
                    physical_status=payload['summary']['status'],observed_until_minute=payload['summary']['observed_until_minute']))
                journal(directory/'ATTEMPTS.jsonl',event='analysis_failed',scenario_id=scene['id'],policy_id=spec['policy_id'],raw_sha256=raw_sha)
                raise
            record=dict(scenario_id=scene['id'],analysis_class=scene['analysis_class'],model_id=env.model_id,
                policy_id=spec['policy_id'],status=payload['summary']['status'],
                source_v13_scenario_id=scene.get('source_v13_scenario_id'),source_day_index=scene.get('source_day_index'),
                raw_path=str(raw),raw_sha256=raw_sha,elapsed_seconds=time.perf_counter()-began,
                manifest_signature=manifest['signature'],model_config_signature=manifest['model_config_signature'],
                error=payload.get('error'),analysis=analysis)
            write(pair/'RESULT.json',record);records.append(record)
            journal(directory/'ATTEMPTS.jsonl',event='finished',scenario_id=scene['id'],policy_id=spec['policy_id'],
                status=record['status'],observed_until=analysis['observed_until_minute'],raw_sha256=raw_sha,
                result_sha256=sha(pair/'RESULT.json'),elapsed_seconds=record['elapsed_seconds'])
            print(json.dumps(dict(completed=len(records),registered=plan['registered_pairs'],scenario=scene['id'],
                policy=spec['policy_id'],model=env.model_id,status=record['status'])),flush=True)
        matches[scene['id']]=matching_evidence(matching_payloads)
    if source_binding()!=plan['source_sha256']:raise ValueError('Source drift after final physical episode')
    receipt=export_rows(records,directory/'analysis')
    result=dict(schema='hospital_ies.engineering_batch_result.v14.v1',registered_pairs=plan['registered_pairs'],
        actual_attempts=len(records),complete_full=sum(r['analysis']['windows']['full']['complete'] for r in records),
        unknown_full=sum(not r['analysis']['windows']['full']['complete'] for r in records),
        elapsed_seconds=time.perf_counter()-started,source_unchanged=True,
        matching_evidence=matches,
        same_initial_state_policy_matching=all(m['same_initial_state'] for m in matches.values()),
        same_exogenous_stream_policy_matching=all(m['same_exogenous_definition'] for m in matches.values()),
        plan_sha256=sha(directory/'plan.json'),manifest_sha256=sha(directory/'manifest.json'),analysis_receipt=receipt,
        all_registered_outcomes_retained=True,physical_replay_certification=False)
    # Pair-level raw remains authoritative; no aggregate can replace unknowns.
    write(directory/'BATCH_RESULT.json',result)
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    reg=sub.add_parser('register');reg.add_argument('--proposal',type=Path,required=True);reg.add_argument('--directory',type=Path,required=True)
    reg.add_argument('--policies',required=True);reg.add_argument('--model',choices=('M0','M14_engineering'),required=True)
    reg.add_argument('--model-config',type=Path);reg.add_argument('--scenario-id',action='append');reg.add_argument('--phase',default='development')
    run=sub.add_parser('run');run.add_argument('--directory',type=Path,required=True)
    a=p.parse_args()
    if a.command=='register':
        pool=policy_specs();result=register(read(a.proposal),a.directory,[pool[k] for k in a.policies.split(',')],
            model_id=a.model,model_config=read(a.model_config) if a.model_config else {},scenario_ids=a.scenario_id,phase=a.phase)
        print(json.dumps({k:result[k] for k in ('registered_pairs','model_id','scenario_ids')}))
    else:print(json.dumps(run_batch(a.directory)))


if __name__=='__main__':main()
