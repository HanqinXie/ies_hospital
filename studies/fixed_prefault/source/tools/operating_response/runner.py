"""Thin v15 immutable campaigns; caller's one payload is compressed/analyzed once."""
from __future__ import annotations

import argparse
from collections import Counter
import copy
from datetime import datetime,timezone
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import sys
import time
import traceback

ROOT=Path(__file__).resolve().parents[2]
RUN=ROOT/'outputs/v15_engineering_operation_20261017_run01'
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from tools.engineering_model import runner as legacy
from .environment import OperatingHospitalIESEnv,model_contract,run_episode
from .policy import OperatingPolicy
from .data_design import build_manifest,digest,SEALED_DAYS,RESOURCE_LIMITS,allowed_hours
from .analysis import analyze_payload,export_rows


def read(path):return json.loads(Path(path).read_text(encoding='utf8'))
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def now():return datetime.now(timezone.utc).isoformat()


def write(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf8') as stream:json.dump(value,stream,ensure_ascii=False,indent=2,allow_nan=False)


def journal(path,**value):
    with Path(path).open('a',encoding='utf8') as stream:
        stream.write(json.dumps(dict(utc=now(),**value),ensure_ascii=False,allow_nan=False)+'\n')


def check_output_path(path):
    value=Path(path).resolve()
    if value.drive.lower()=='c:':raise ValueError('Research data must use D/E, not C')
    return value


def _experiment_path(path):
    directory=check_output_path(path);canonical=(RUN/'experiments').resolve()
    if directory==canonical or not directory.is_relative_to(canonical):
        raise ValueError('Registration/run must use the canonical RUN experiments directory for one budget')
    return directory


def source_binding():
    binding=legacy.source_binding()
    for path in (ROOT/'tools/operating_response').rglob('*'):
        if path.is_file() and '__pycache__' not in path.parts and path.suffix!='.pyc':
            binding[path.relative_to(ROOT).as_posix()]=sha(path)
    return dict(sorted(binding.items()))


def policy_specs():
    old=legacy.policy_specs()
    pool={key:copy.deepcopy(old[key]) for key in ('F','E')}
    for name in ('K0','K1','K2'):
        pool[name]=dict(policy_id='readiness_handoff_'+name+'_v15',kind='operating_response',
            config=dict(preparation_profile=name,phase_constraints_enabled=True,continuity=True))
    pool['A2']=dict(policy_id='readiness_multiplier_only_K2_v15',kind='operating_response',
        config=dict(preparation_profile='K2',phase_constraints_enabled=False,continuity=True))
    pool['C2']=dict(policy_id='readiness_no_continuity_K2_v15',kind='operating_response',
        config=dict(preparation_profile='K2',phase_constraints_enabled=True,continuity=False))
    return pool


def factory(spec):
    old=legacy.policy_specs()
    for key in ('F','E'):
        if spec.get('policy_id')==old[key]['policy_id'] and spec!=old[key]:
            raise ValueError('Frozen baseline identity cannot accept changed kind/configuration')
    if spec.get('kind')=='operating_response':
        return OperatingPolicy(copy.deepcopy(spec['config']),spec['policy_id'])
    return legacy.factory(copy.deepcopy(spec))


def _injection_state(payload):
    scene=payload['scenario'];first=scene.get('first_event_minute')
    if first is None:return None
    first=float(first)
    for segment in payload.get('segments',[]):
        if abs(float(segment['start_minute'])-first)<=1e-8:
            return copy.deepcopy(segment.get('pre_state'))
    candidates=[s for s in payload.get('segments',[]) if abs(float(s['end_minute'])-first)<=1e-8]
    return copy.deepcopy(candidates[-1].get('post_state')) if candidates else None


def matching_evidence(payloads):
    states=[p.get('initial_state') for p in payloads]
    fields={'start_hour','duration_steps','step_minutes','faults','shock','shock_by_priority',
        'exogenous_events','enable_propagation','propagation_enabled','grid_limit_kW',
        'pre_event_grid_limit_kW','pre_event_warmup_steps','response_delay_minutes',
        'automatic_fast_shedding','initialization_seed'}
    streams=[{k:v for k,v in p['scenario'].items() if k in fields or k.startswith(('fault_','shock_'))} for p in payloads]
    state_hashes=[digest(s) if s is not None else None for s in states]
    stream_hashes=[digest(s) for s in streams]
    injected=[_injection_state(p) for p in payloads]
    injected_hashes=[digest(s) if s is not None else None for s in injected]
    known=bool(injected) and all(s is not None for s in injected)
    return dict(initial_state_sha256=state_hashes,applied_exogenous_definition_sha256=stream_hashes,
        same_initial_state=bool(states) and all(s is not None for s in states) and len(set(state_hashes))==1,
        same_exogenous_definition=bool(streams) and len(set(stream_hashes))==1,
        injection_state_sha256=injected_hashes,same_injection_state=len(set(injected_hashes))==1 if known else None,
        policy_dependent_prefix_states_possible=any(float(p['scenario'].get('first_event_minute') or 0.)>0. for p in payloads),
        injection_state_coverage_known=known,
        meaning='Reset matching and applied streams; genuine policy-dependent normal prefixes may create different injection states; no iid claim')


def register(proposal,directory,specs,*,model_id,model_config,scenario_ids=None,phase='development'):
    directory=_experiment_path(directory)
    if directory.exists():raise FileExistsError('Use fresh registration; old attempts are immutable')
    _,resolved=model_contract(model_id,model_config)
    specs=copy.deepcopy(list(specs));ids=[s['policy_id'] for s in specs]
    if not specs or len(specs)>8 or len(ids)!=len(set(ids)):
        raise ValueError('Distinct finite policy panel of at most eight required')
    for spec in specs:factory(spec)
    proposal=copy.deepcopy(proposal)
    if scenario_ids is not None:
        requested=list(scenario_ids)
        if len(requested)!=len(set(requested)):raise ValueError('Duplicate requested scenario identity')
        wanted=set(requested);proposal['scenarios']=[s for s in proposal['scenarios'] if s['id'] in wanted]
        if len(proposal['scenarios'])!=len(wanted):raise ValueError('Missing registered specification')
    if not proposal['scenarios']:raise ValueError('Empty specification panel')
    count=len(proposal['scenarios'])*len(specs)
    existing=sum(int(read(p)['registered_pairs']) for p in (RUN/'experiments').rglob('plan.json'))
    ceiling=int(RESOURCE_LIMITS['max_actual_attempts'])
    if existing+count>ceiling:raise ValueError('Provisional canonical v15 registered episode ceiling exceeded; record evidence before budget revision')
    binding=source_binding()
    manifest=build_manifest(proposal,binding,model_id,resolved,root=ROOT,policies=ids)
    plan=dict(schema='hospital_ies.operating_batch.v15.v1',registered_utc=now(),phase=phase,
        model_id=model_id,model_config=copy.deepcopy(resolved),policy_specs=specs,
        scenario_ids=[s['id'] for s in manifest['scenarios']],registered_pairs=count,
        source_sha256=binding,manifest_signature=manifest['signature'],manifest_sha256=None,
        canonical_run=str(RUN.resolve()),resource_limits=copy.deepcopy(RESOURCE_LIMITS),
        original_final12='not_verified; unopened',no_figures=True,no_papers=True,no_training=True)
    directory.mkdir(parents=True)
    write(directory/'manifest.json',manifest);plan['manifest_sha256']=sha(directory/'manifest.json')
    write(directory/'plan.json',plan)
    for name in binding:
        if name.startswith(('tools/operating_response/','tools/engineering_model/')):
            destination=directory/'source_snapshot'/name
            destination.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/name,destination)
    if source_binding()!=binding:raise ValueError('Source changed during registration; retain partial registration, use fresh source-bound batch')
    return plan


def _complete(record,window):
    return bool(record.get('analysis') and record['analysis']['windows'][window]['complete'])


def run_batch(directory):
    directory=_experiment_path(directory);plan=read(directory/'plan.json');manifest=read(directory/'manifest.json')
    if sha(directory/'manifest.json')!=plan['manifest_sha256'] or source_binding()!=plan['source_sha256']:
        raise ValueError('Changed registration or source; never mix versions')
    if (directory/'BATCH_RESULT.json').exists():raise FileExistsError('Completed evidence is immutable')
    if (directory/'ATTEMPTS.jsonl').exists() or (directory/'pairs').exists():
        raise FileExistsError('Partial attempts are immutable; resume is not implemented, register a fresh attempt')
    env=OperatingHospitalIESEnv(ROOT,allowed_hours=allowed_hours(manifest),sealed_days=SEALED_DAYS,
        model_id=plan['model_id'],model_config=plan['model_config'])
    records=[];matches={};started=time.perf_counter()
    for scene in manifest['scenarios']:
        matching_payloads=[]
        for spec in plan['policy_specs']:
            if source_binding()!=plan['source_sha256']:raise ValueError('Source changed while a physical campaign was active')
            pair=directory/'pairs'/(scene['id']+'__'+spec['policy_id']);pair.mkdir(parents=True,exist_ok=False)
            journal(directory/'ATTEMPTS.jsonl',event='started',scenario_id=scene['id'],model_id=env.model_id,policy_id=spec['policy_id'])
            began=time.perf_counter();analysis=None;raw_sha=None;raw=None;analysis_error=None
            try:
                payload=run_episode(env,scene,factory(spec),int(scene.get('initialization_seed',20261017)),manifest)
            except Exception as exc:
                failure=dict(type=type(exc).__name__,message=str(exc),traceback=traceback.format_exc(),
                    phase='unhandled_run_episode',observed_until_minute=getattr(getattr(env,'core',None),'time',None),
                    requested_end_minute=scene['duration_steps']*scene.get('step_minutes',15.),
                    physical_tail='unknown; no substitute zero trajectory generated')
                write(pair/'UNHANDLED_ATTEMPT_ERROR.json',failure)
                matching_payloads.append(dict(initial_state=None,scenario=scene,segments=[]))
                status='runner_unhandled_exception';error=failure
            else:
                matching_payloads.append(dict(initial_state=payload.get('initial_state'),scenario=payload['scenario'],
                    segments=[{k:s.get(k) for k in ('start_minute','end_minute','pre_state','post_state')} for s in payload.get('segments',[])]))
                compressed=gzip.compress(json.dumps(payload,ensure_ascii=False,allow_nan=False).encode('utf8'),mtime=0)
                raw=pair/'trajectory.json.gz';raw.write_bytes(compressed);raw_sha=hashlib.sha256(compressed).hexdigest()
                status=payload['summary']['status'];error=payload.get('error')
                try:analysis=analyze_payload(payload,env)
                except Exception as exc:
                    analysis_error=dict(type=type(exc).__name__,message=str(exc),traceback=traceback.format_exc(),raw_sha256=raw_sha,
                        physical_status=status,observed_until_minute=payload['summary']['observed_until_minute'])
                    write(pair/'analysis_error.json',analysis_error)
            record=dict(scenario_id=scene['id'],analysis_class=scene['analysis_class'],model_id=env.model_id,
                policy_id=spec['policy_id'],status=status,source_day_index=scene.get('source_day_index'),
                raw_path=str(raw) if raw else None,raw_sha256=raw_sha,elapsed_seconds=time.perf_counter()-began,
                manifest_signature=manifest['signature'],model_config_signature=manifest['model_config_signature'],
                error=error,analysis_error=analysis_error,analysis=analysis)
            write(pair/'RESULT.json',record);records.append(record)
            journal(directory/'ATTEMPTS.jsonl',event='finished',scenario_id=scene['id'],policy_id=spec['policy_id'],
                status=status,analysis_status='saved' if analysis is not None else 'unknown_or_failed',
                observed_until=analysis['observed_until_minute'] if analysis else None,raw_sha256=raw_sha,
                result_sha256=sha(pair/'RESULT.json'),elapsed_seconds=record['elapsed_seconds'])
            print(json.dumps(dict(completed=len(records),registered=plan['registered_pairs'],scenario=scene['id'],
                policy=spec['policy_id'],model=env.model_id,status=status,analysis_available=analysis is not None)),flush=True)
        matches[scene['id']]=matching_evidence(matching_payloads)
    if source_binding()!=plan['source_sha256']:raise ValueError('Source drift after final physical episode')
    analyzed=[r for r in records if r['analysis'] is not None]
    receipt=export_rows(analyzed,directory/'analysis')
    result=dict(schema='hospital_ies.operating_batch_result.v15.v1',registered_pairs=plan['registered_pairs'],
        actual_attempts=len(records),complete_full=sum(_complete(r,'registered_full') for r in records),
        unknown_full=sum(not _complete(r,'registered_full') for r in records),
        complete_legacy_full600=sum(_complete(r,'full') for r in records),
        unknown_legacy_full600=sum(not _complete(r,'full') for r in records),
        analysis_failure_or_unavailable_count=len(records)-len(analyzed),status_counts=dict(Counter(r['status'] for r in records)),
        elapsed_seconds=time.perf_counter()-started,source_unchanged=True,matching_evidence=matches,
        same_initial_state_policy_matching=all(m['same_initial_state'] for m in matches.values()),
        same_exogenous_stream_policy_matching=all(m['same_exogenous_definition'] for m in matches.values()),
        policy_dependent_continuous_prefix_injection_states_are_not_required_equal=True,
        plan_sha256=sha(directory/'plan.json'),manifest_sha256=sha(directory/'manifest.json'),analysis_receipt=receipt,
        all_registered_outcomes_retained=True,physical_replay_certification=False,
        completion_semantics='complete_full is entire actual requested [0,end); legacy full600 is separate',
        pair_index=[{k:v for k,v in r.items() if k!='analysis'} for r in records])
    write(directory/'BATCH_RESULT.json',result)
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='command',required=True)
    reg=sub.add_parser('register');reg.add_argument('--proposal',type=Path,required=True);reg.add_argument('--directory',type=Path,required=True)
    reg.add_argument('--policies',required=True);reg.add_argument('--model',choices=('M0','M14_engineering','M15_local_response'),required=True)
    reg.add_argument('--model-config',type=Path);reg.add_argument('--scenario-id',action='append');reg.add_argument('--phase',default='development')
    run=sub.add_parser('run');run.add_argument('--directory',type=Path,required=True)
    args=parser.parse_args(argv)
    if args.command=='register':
        pool=policy_specs();keys=['E' if k=='E6' else k for k in args.policies.split(',')]
        result=register(read(args.proposal),args.directory,[pool[k] for k in keys],model_id=args.model,
            model_config=read(args.model_config) if args.model_config else {},scenario_ids=args.scenario_id,phase=args.phase)
        print(json.dumps({k:result[k] for k in ('registered_pairs','model_id','scenario_ids')},ensure_ascii=False))
    else:print(json.dumps(run_batch(args.directory),ensure_ascii=False))
    return 0


if __name__=='__main__':raise SystemExit(main())
