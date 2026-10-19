"""Strict release-root proof around the unchanged V20 scalar replay helper.

This publication layer never changes production, input or test bytes. A normal
invocation starts exactly the one episode registered by the frozen helper;
--check-only reads compact evidence and starts no episode.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import uuid

ROOT=Path(__file__).resolve().parents[1]
RUN_REL='outputs/v20_load_accounting_20261019_run01'
MODEL='M20_load_accounting'
FREEZE_REL=RUN_REL+'/MODEL_FREEZE_ACCOUNTING01.json'
MATCH_FIELDS=('actual_initial_state_sha256','actual_exogenous_sha256','actual_injection_state_sha256','source_binding_sha256',
    'model_config_sha256','runtime_inputs_sha256','registered_scene_sha256','policy_sha256',
    'metric_fields_sha256')
STREAM_FIELDS={'start_hour','duration_steps','step_minutes','faults','shock','shock_by_priority',
    'exogenous_events','enable_propagation','propagation_enabled','grid_limit_kW',
    'pre_event_grid_limit_kW','pre_event_warmup_steps','response_delay_minutes',
    'automatic_fast_shedding','initialization_seed'}


def read(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))
def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024**2),b''):h.update(chunk)
    return h.hexdigest()
def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=True,
        separators=(',',':'),allow_nan=False).encode('utf8')).hexdigest()
def require(condition,message):
    if not condition:raise ValueError(message)
def is_sha(value):return isinstance(value,str) and re.fullmatch('[0-9a-f]{64}',value) is not None
def safe(root,name):
    require(isinstance(name,str) and name and ':' not in name and '\\' not in name,'Unsafe relative path')
    part=PurePosixPath(name)
    require(not part.is_absolute() and '..' not in part.parts,'Unsafe relative path')
    value=(Path(root)/name).resolve()
    require(value.is_relative_to(Path(root).resolve()),'Path escaped release root')
    return value
def write_new(path,value):
    with Path(path).open('x',encoding='utf8',newline='\n') as stream:
        json.dump(value,stream,ensure_ascii=False,indent=2,allow_nan=False);stream.write('\n')
def applied_stream_hash(scene):
    return digest({k:v for k,v in scene.items() if k in STREAM_FIELDS or k.startswith(('fault_','shock_'))})


def validate_root_isolation(root,marker):
    root=Path(root).resolve();forbidden=[]
    for field in ('source_root','original_scientific_root','original_research_root'):
        value=marker.get(field)
        require(isinstance(value,str) and Path(value).is_absolute(),'Missing absolute original root: '+field)
        original=Path(value).resolve()
        require(not root.is_relative_to(original) and not original.is_relative_to(root),
            'Independent release root overlaps '+field)
        if original not in forbidden:forbidden.append(original)
    return forbidden


def validate_root_provenance(marker,starting):
    for field,original in (('original_scientific_root','new_root'),('original_research_root','source_root')):
        value=starting.get(original)
        require(isinstance(value,str) and Path(value).is_absolute(),'Starting root provenance missing: '+original)
        require(Path(marker[field]).resolve()==Path(value).resolve(),'Marker original root differs from STARTING_POINT: '+field)


def validate_release(root):
    root=Path(root).resolve()
    marker_path=safe(root,'publication/V20_RELEASE_ROOT.json')
    selection_path=safe(root,'publication/V20_FILE_SELECTION.json')
    require(marker_path.is_file() and selection_path.is_file(),'Release marker and selection are required')
    marker=read(marker_path);forbidden=validate_root_isolation(root,marker)
    require(marker.get('status') in ('candidate','final'),'Unknown release status')
    require(isinstance(marker.get('supplemental_closure'),list),'Supplemental closure missing')
    require(marker.get('freeze_sha256')==sha(safe(root,FREEZE_REL)),'Release freeze SHA differs')
    require(marker.get('wrapper_sha256')==sha(safe(root,'publication/build_v20_release.py')),
        'Publishing wrapper SHA differs')
    own=sha(safe(root,'publication/reproduce_v20.py'))
    if 'reproduction_wrapper_sha256' in marker:
        require(marker['reproduction_wrapper_sha256']==own,'Reproduction wrapper SHA differs')
    rows=read(selection_path).get('files');require(isinstance(rows,list) and rows,'Empty selection')
    indexed={}
    for row in rows:
        name=row.get('path');path=safe(root,name)
        require(name not in indexed,'Duplicate selected path')
        require(type(row.get('bytes')) is int and 0<=row['bytes']<100*1024**2 and is_sha(row.get('sha256')),
            'Invalid or oversized selected member: '+name)
        require(path.is_file() and path.stat().st_size==row['bytes'] and sha(path)==row['sha256'],
            'Selected bytes differ: '+name)
        indexed[name]=row
    starting_rel=RUN_REL+'/provenance/STARTING_POINT.json'
    for name in ('publication/reproduce_v20.py','publication/build_v20_release.py',
                 'publication/V20_RELEASE_ROOT.json',FREEZE_REL,starting_rel):
        require(name in indexed,'Required release file absent from selection: '+name)
    validate_root_provenance(marker,read(safe(root,starting_rel)))
    for row in marker['supplemental_closure']:
        selected=indexed.get(row.get('path'))
        require(selected and all(row.get(k)==selected.get(k) for k in ('bytes','sha256')),
            'Supplemental closure not bound by selection')
    return dict(marker=marker,forbidden_roots=[str(p) for p in forbidden],
        marker_sha256=sha(marker_path),selection_sha256=sha(selection_path),
        reproduction_wrapper_sha256=own,selected_files=len(rows))


def qualify_evidence(bundle,freeze,freeze_sha256):
    """Fail closed on compact actual evidence; never turn unknown into zero."""
    p,m,t,r=(bundle[k] for k in ('plan','manifest','terminal','record'))
    file_sha=bundle['file_sha256'];specs=p['policy_specs'];scenes=m['scenarios']
    require(p.get('model_id')==m.get('execution_model_id')==freeze.get('model_id')==MODEL,'Model mismatch')
    require(p.get('source_sha256')==m.get('source_sha256')==freeze['source_sha256'],'Source mismatch')
    require(p.get('model_config')==m.get('model_config')==freeze['model_config'],'Resolved configuration mismatch')
    require(m.get('model_config_signature')==digest(freeze['model_config']),'Configuration signature mismatch')
    require(m.get('files')==freeze['runtime_inputs'] and bool(m.get('files')),'Runtime input inventory mismatch')
    require(m.get('signature')==digest({k:v for k,v in m.items() if k!='signature'}),'Manifest signature mismatch')
    require(p.get('manifest_sha256')==file_sha['manifest.json'] and p.get('manifest_signature')==m['signature'],
        'Plan does not bind actual manifest')
    require(m.get('freeze_signature')==freeze['signature']
        and m.get('source_freeze_artifact',{}).get('sha256')==freeze_sha256,'Freeze binding mismatch')
    require(len(scenes)==1 and p.get('scenario_ids')==[s['id'] for s in scenes],'Expected one registered scene')
    require(specs and len({s['policy_id'] for s in specs})==len(specs),'Invalid policy panel')
    require(all(s in freeze['policies'].values() for s in specs),'Panel contains an unfrozen policy preference')
    require(m.get('policies')==[s['policy_id'] for s in specs]
        and m.get('policy_specs_sha256')==digest(specs),'Policy panel signature mismatch')
    policy=freeze['policies']['E'];indices=[i for i,s in enumerate(specs) if s==policy]
    require(len(indices)==1,'Frozen E preference absent or changed')
    index=indices[0];scene=scenes[0];count=len(specs)
    require(scene.get('duration_steps',0)*scene.get('step_minutes',15.)==600.,'Reference must cover exactly 600 minutes')
    require(p.get('registered_pairs')==t.get('registered_pairs')==count,'Registered denominator mismatch')
    require(t.get('source_unchanged') is True and t.get('plan_sha256')==file_sha['plan.json']
        and t.get('manifest_sha256')==file_sha['manifest.json'],'Unbound actual terminal')
    require(t.get('actual_attempts')==t.get('complete_full')==count and t.get('unknown_full')==0
        and t.get('analysis_failure_or_unavailable_count')==0 and t.get('status_counts')=={'completed':count},
        'Terminal attempts are incomplete or failed')
    require(not bundle.get('batch_issues'),'Actual batch qualification failed')
    require(bundle.get('execution_qualified') is True and not bundle.get('execution_reasons')
        and not bundle.get('journal_issues'),'Execution qualification failed')
    require(r.get('scenario_id')==scene['id'] and r.get('policy_id')==policy['policy_id']
        and r.get('model_id')==MODEL and r.get('manifest_signature')==m['signature']
        and r.get('model_config_signature')==m['model_config_signature'],'Actual RESULT identity mismatch')
    require(r.get('status')=='completed' and r.get('error') is None and r.get('analysis_error') is None,
        'Failed or unknown actual RESULT')
    journal=bundle['journal'];expected={(scene['id'],s['policy_id']) for s in specs}
    require(len(journal)==2*count and {j.get('event') for j in journal}=={'started','finished'},
        'Unexpected or duplicate attempt journal entries')
    for event in ('started','finished'):
        own=[j for j in journal if j.get('event')==event]
        require(len(own)==count and {(j.get('scenario_id'),j.get('policy_id')) for j in own}==expected,
            'Actual attempt identities/count differ')
    finishes=[j for j in journal if j.get('event')=='finished' and j.get('policy_id')==policy['policy_id']]
    require(finishes[0].get('result_sha256')==file_sha['RESULT.json']
        and finishes[0].get('raw_sha256')==r.get('raw_sha256') and is_sha(r.get('raw_sha256')),
        'Actual finish does not bind RESULT/raw fingerprints')
    a=r['analysis'];w=a['v18']['windows']['registered_full'];ledger=a['v20']['service_ledgers']['registered_full']
    require(a.get('execution_model_id')==MODEL and a.get('observed_until_minute')==600.,'Actual coverage mismatch')
    require(a['v20']['episode_audit'].get('status')=='passed' and a['v20']['episode_audit'].get('issues')==[],
        'Actual audit not passed')
    for value in (w,ledger):
        require(value.get('complete') is True and value.get('window_minutes')==[0.,600.]
            and value.get('unknown_tail_minutes') is None and value.get('all_registered') is not None,
            'Unknown, incomplete or mismatched accounting window')
    require(ledger.get('observed_finish_minute')==600.,'Ledger observed finish mismatch')
    metrics=bundle['snapshot_metrics']
    require(len(metrics)==94 and all(type(v) in (int,float) and math.isfinite(v) for v in metrics.values()),
        'All 94 scalar metrics must be known finite values')
    evidence=t.get('matching_evidence',{}).get(scene['id'])
    require(isinstance(evidence,dict) and evidence.get('same_initial_state') is True
        and evidence.get('same_exogenous_definition') is True,'Actual matching evidence unknown/false')
    for field in ('initial_state_sha256','applied_exogenous_definition_sha256'):
        values=evidence.get(field)
        require(isinstance(values,list) and len(values)==count and all(is_sha(v) for v in values)
            and len(set(values))==1,'Actual matching fingerprint missing or panel-inconsistent')
    require(evidence['applied_exogenous_definition_sha256'][index]==applied_stream_hash(scene),
        'Actual applied stream does not bind registered physical fields')
    injection=evidence.get('injection_state_sha256')
    require(isinstance(injection,list) and len(injection)==count and is_sha(injection[index]),
        'Actual E injection fingerprint unknown')
    require(isinstance(m.get('evaluation_role'),str) and bool(m['evaluation_role']),'Evaluation role unknown')
    return dict(scenario_id=scene['id'],policy_id=policy['policy_id'],window_minutes=[0.,600.],
        actual_initial_state_sha256=evidence['initial_state_sha256'][index],
        actual_exogenous_sha256=evidence['applied_exogenous_definition_sha256'][index],
        actual_injection_state_sha256=injection[index],
        source_binding_sha256=digest(p['source_sha256']),model_config_sha256=digest(p['model_config']),
        runtime_inputs_sha256=digest(m['files']),registered_scene_sha256=digest(scene),
        policy_sha256=digest(policy),policy_panel_sha256=digest(specs),metric_fields_sha256=digest(sorted(metrics)),
        evaluation_role=m.get('evaluation_role'),metric_count=len(metrics),file_sha256=file_sha,
        actual_raw_sha256=r['raw_sha256'],execution_qualified=True,metrics=metrics)


def match_qualified_pairs(reference,replay):
    comparisons={k:reference.get(k)==replay.get(k) and is_sha(reference.get(k)) for k in MATCH_FIELDS}
    require(all(comparisons.values()),'Reference/replay actual source/config/input/reset/exogenous/scene/policy mismatch')
    require(reference['scenario_id']==replay['scenario_id'] and reference['policy_id']==replay['policy_id'],
        'Reference/replay identity mismatch')
    require(reference.get('evaluation_role') is not None
        and reference['evaluation_role']==replay.get('evaluation_role'),'Reference/replay evaluation role mismatch')
    return dict(matched=True,fields=comparisons,
        reference_policy_panel_sha256=reference['policy_panel_sha256'],replay_policy_panel_sha256=replay['policy_panel_sha256'],
        reference_evaluation_role=reference.get('evaluation_role'),replay_evaluation_role=replay.get('evaluation_role'),
        policy_panel_difference_is_preserved=True)


def load_module(root,name,relative):
    path=safe(root,relative);spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    require(Path(module.__file__).resolve()==path,'Helper import escaped release root')
    return module


def load_bundle(root,directory,freeze,repository,report,campaign):
    directory=safe(root,directory);plan=read(directory/'plan.json');manifest=read(directory/'manifest.json')
    scene=manifest['scenarios'][0];policy=freeze['policies']['E']
    relative=(directory.relative_to(root).as_posix()+'/pairs/'+scene['id']+'__'+policy['policy_id']+'/RESULT.json')
    path=safe(root,relative);record=read(path);journal=[];issues=[]
    for number,line in enumerate((directory/'ATTEMPTS.jsonl').read_text(encoding='utf-8-sig').splitlines(),1):
        if not line.strip():continue
        try:
            value=json.loads(line)
            if not isinstance(value,dict):raise ValueError('journal row must be an object')
            journal.append(value)
        except (ValueError,TypeError) as exc:issues.append(dict(line=number,error=str(exc)))
    terminal=read(directory/'BATCH_RESULT.json')
    qualified,reasons=report.execution_qualification(record,journal,path,audit_key='v20',journal_issues=issues)
    return dict(plan=plan,manifest=manifest,terminal=terminal,record=record,journal=journal,journal_issues=issues,
        execution_qualified=qualified,execution_reasons=reasons,batch_issues=campaign.terminal_issues(directory,terminal),
        snapshot_metrics=repository.metric_snapshot(record),
        file_sha256={name:sha(value) for name,value in (
            ('plan.json',directory/'plan.json'),('manifest.json',directory/'manifest.json'),
            ('BATCH_RESULT.json',directory/'BATCH_RESULT.json'),('ATTEMPTS.jsonl',directory/'ATTEMPTS.jsonl'),
            ('RESULT.json',path))})


GUARD_CODE=r'''import atexit,json,os,sys
from pathlib import Path
config=json.loads((Path(__file__).parent/'guard_config.json').read_text(encoding='utf8'))
root=Path(config['root']).resolve();forbidden=[Path(x).resolve() for x in config['forbidden_roots']]
violations=[]
def checked(value):
    if isinstance(value,(str,bytes,os.PathLike)):
        try:path=Path(os.fsdecode(value)).resolve()
        except (OSError,ValueError):return
        if any(path.is_relative_to(old) for old in forbidden):
            violations.append(str(path));raise RuntimeError('Original research tree access forbidden: '+str(path))
def audit(event,args):
    if event in ('open','os.listdir','os.scandir','os.chdir') and args:checked(args[0])
    elif event=='import' and len(args)>1 and args[1]:checked(args[1])
sys.addaudithook(audit)
def finish():
    imports=[]
    for name,module in list(sys.modules.items()):
        if name=='tools' or name.startswith(('tools.','hospital_ies')):
            paths=[]
            if getattr(module,'__file__',None):paths.append(module.__file__)
            paths.extend(list(getattr(module,'__path__',[]) or []))
            for value in paths:
                path=Path(value).resolve();imports.append(dict(module=name,path=str(path)))
                if not path.is_relative_to(root):violations.append('project import escaped root: '+str(path))
    value=dict(status='failed' if violations else 'passed',token=config['token'],root=str(root),
        forbidden_roots=config['forbidden_roots'],violations=violations,project_imports=imports,
        meaning='Audit hook active during child execution; no audited reads/imports from the declared original trees',
        limitation='CPython audit events and project module paths; not an OS sandbox or proof about un-audited native I/O or grandchildren')
    with Path(config['receipt']).open('x',encoding='utf8') as stream:json.dump(value,stream,ensure_ascii=False,indent=2)
atexit.register(finish)
'''


def write_child_guard(directory,root,forbidden_roots,receipt):
    directory=Path(directory);token=uuid.uuid4().hex
    config=dict(root=str(Path(root).resolve()),forbidden_roots=[str(Path(p).resolve()) for p in forbidden_roots],
        receipt=str(Path(receipt).resolve()),token=token)
    write_new(directory/'guard_config.json',config)
    with (directory/'sitecustomize.py').open('x',encoding='utf8',newline='\n') as stream:stream.write(GUARD_CODE)
    return config


def guarded_environment(environment,guard):
    env=os.environ.copy();env.update(environment)
    env['PYTHONPATH']=str(Path(guard).resolve())+(os.pathsep+env['PYTHONPATH'] if env.get('PYTHONPATH') else '')
    env['PYTHONDONTWRITEBYTECODE']='1';env['PYTHONIOENCODING']='utf-8'
    return env


class PythonReadGuard:
    """Parent-side CPython audit hook; disabled on leaving the context."""
    def __init__(self,root,forbidden):
        self.root=Path(root).resolve();self.forbidden=[Path(p).resolve() for p in forbidden]
        self.active=False;self.violations=[];self.operations=0
    def __enter__(self):
        self.active=True;sys.addaudithook(self.audit);return self
    def __exit__(self,*ignored):self.active=False
    def audit(self,event,args):
        if not self.active:return
        value=None
        if event in ('open','os.listdir','os.scandir','os.chdir') and args:value=args[0]
        elif event=='import' and len(args)>1:value=args[1]
        if isinstance(value,(str,bytes,os.PathLike)):
            try:path=Path(os.fsdecode(value)).resolve()
            except (OSError,ValueError):return
            self.operations+=1
            if any(path.is_relative_to(original) for original in self.forbidden):
                self.violations.append(str(path));raise RuntimeError('Original research tree access forbidden: '+str(path))
    def summary(self):
        return dict(active_during_parent_execution=True,root=str(self.root),
            forbidden_roots=[str(p) for p in self.forbidden],violations=list(self.violations),
            audited_python_operations=self.operations,
            limitation='CPython audit events and project module paths; not an OS sandbox or proof about un-audited native I/O')


def check_project_imports(root):
    root=Path(root).resolve();rows=[]
    for name,module in list(sys.modules.items()):
        if name=='tools' or name.startswith(('tools.','hospital_ies')):
            values=([module.__file__] if getattr(module,'__file__',None) else [])
            values+=list(getattr(module,'__path__',[]) or [])
            for value in values:
                path=Path(value).resolve();require(path.is_relative_to(root),'Project import escaped release root: '+str(path))
                rows.append(dict(module=name,path=str(path)))
    return rows


def prepare(root):
    root=Path(root).resolve();release=validate_release(root)
    sys.path[:0]=[str(root),str(root/'src')]
    repository=load_module(root,'v20_frozen_scalar_repository','scripts/v20_repository.py')
    require(repository.ROOT.resolve()==root,'Frozen helper ROOT mismatch')
    report=load_module(root,'v20_frozen_report_qualification','scripts/v20_report.py')
    campaign=load_module(root,'v20_frozen_campaign_qualification','scripts/v20_campaign.py')
    require(repository.verify()['status']=='passed','Frozen closure verification failed')
    freeze=read(safe(root,FREEZE_REL));reference=load_bundle(root,RUN_REL+'/experiments/v20_main_000',
        freeze,repository,report,campaign)
    qualified=qualify_evidence(reference,freeze,release['marker']['freeze_sha256'])
    imports=check_project_imports(root)
    return release,freeze,repository,report,campaign,qualified,imports


def _reproduce_with_parent_guard(name,check_only,parent_guard):
    require(re.fullmatch('[A-Za-z0-9_]+',name) is not None,'Use a fresh ASCII replay name')
    if os.name=='nt':require(ROOT.drive.lower() in ('d:','e:'),'Scientific outputs must use D/E')
    release,freeze,repository,report,campaign,reference,imports=prepare(ROOT)
    require(not parent_guard.violations,'Parent qualification attempted an original-tree read')
    target_rel=RUN_REL+'/experiments/reproduce_'+name
    own_review=safe(ROOT,RUN_REL+'/review/independent_repository_'+name)
    frozen_review=safe(ROOT,RUN_REL+'/review/reproduce_'+name)
    require(not own_review.exists() and not frozen_review.exists() and not safe(ROOT,target_rel).exists(),
        'Replay name already has evidence; retain it and use a fresh name')
    if check_only:
        print(json.dumps(dict(status='qualified_no_replay_started',root=str(ROOT),metric_count=reference['metric_count'],
            release_status=release['marker']['status'],reference=reference),ensure_ascii=True))
        return 0
    own_review.mkdir(parents=True,exist_ok=False)
    write_new(own_review/'PREREGISTRATION.json',dict(schema='hospital_ies.v20.independent_reproduction_preregistration.v1',
        utc=datetime.now(timezone.utc).isoformat(),current_root=str(ROOT),release=release,
        reference=reference,frozen_scalar_helper_sha256=sha(ROOT/'scripts/v20_repository.py'),
        intended_actual_attempts=1,original_final12_unopened=True,preflight_project_imports=imports))
    guard=own_review/'child_guard';guard.mkdir();guard_receipt=own_review/'CHILD_GUARD.json'
    config=write_child_guard(guard,ROOT,release['forbidden_roots'],guard_receipt)
    expected=[sys.executable,'-B','-m','tools.load_accounting.runner','run','--directory',target_rel]
    original_run=subprocess.run;calls=[];returncode=None;error=None;scalar=None;actual=None;matching=None;rows=[]
    def guarded_run(command,**kwargs):
        require(not calls and command==expected and Path(kwargs.get('cwd','')).resolve()==ROOT,
            'Frozen scalar helper attempted an unexpected child command/cwd')
        require(not kwargs.get('shell'),'Shell execution is forbidden')
        kwargs['env']=guarded_environment(kwargs['env'],guard)
        calls.append(dict(command=command,cwd=str(ROOT),guard_token=config['token']))
        return original_run(command,**kwargs)
    try:
        # The frozen function and bytes are unchanged. Only its process-launch
        # boundary gets an environment guard, which its own PYTHONPATH would
        # otherwise overwrite. The command, cwd and numerical inputs stay fixed.
        subprocess.run=guarded_run
        try:returncode=repository.reproduce(name)
        finally:subprocess.run=original_run
        require(returncode==0 and len(calls)==1,'Frozen scalar helper did not complete one replay')
        scalar=read(frozen_review/'REPRODUCTION_PROOF.json')
        require(scalar.get('status')=='passed' and scalar.get('metric_count')==94
            and scalar.get('registered_duration_minutes')==600.,'Scalar proof failed or incomplete')
        require(len(scalar.get('comparisons',[]))==94 and all(r.get('passed') is True for r in scalar['comparisons']),
            'Scalar comparisons failed')
        after=validate_release(ROOT)
        require(after['marker_sha256']==release['marker_sha256'] and after['selection_sha256']==release['selection_sha256'],
            'Release marker/selection changed during replay')
        require(repository.verify()['status']=='passed','Frozen closure changed during replay')
        replay=load_bundle(ROOT,target_rel,freeze,repository,report,campaign)
        actual=qualify_evidence(replay,freeze,release['marker']['freeze_sha256'])
        matching=match_qualified_pairs(reference,actual)
        rows=repository.compare_metrics(actual['metrics'],reference['metrics'])
        require(len(rows)==94 and all(r['passed'] for r in rows),'Strict known scalar comparison failed')
        guard_proof=read(guard_receipt)
        require(guard_proof.get('status')=='passed' and guard_proof.get('token')==config['token']
            and guard_proof.get('violations')==[] and guard_proof.get('project_imports'),
            'Child import/read guard did not certify the actual runner')
        actual_raw=safe(ROOT,target_rel+'/pairs/'+actual['scenario_id']+'__'+actual['policy_id']+'/trajectory.json.gz')
        require(actual_raw.is_file() and sha(actual_raw)==actual['actual_raw_sha256'],'Actual fresh raw bytes SHA differs')
        imports=check_project_imports(ROOT)
        require(not parent_guard.violations,'Parent attempted an original-tree read')
    except Exception as exc:error=dict(type=type(exc).__name__,message=str(exc))
    finally:subprocess.run=original_run
    success=error is None
    proof=dict(schema='hospital_ies.v20.strict_independent_reproduction.v1',status='passed' if success else 'failed_unknown',
        utc=datetime.now(timezone.utc).isoformat(),current_root=str(ROOT),release_status=release['marker']['status'],
        release_marker_sha256=release['marker_sha256'],release_selection_sha256=release['selection_sha256'],
        wrapper_sha256=sha(Path(__file__)),frozen_scalar_helper_sha256=sha(ROOT/'scripts/v20_repository.py'),
        frozen_scalar_helper_returncode=returncode,scalar_comparison_proof=str(frozen_review/'REPRODUCTION_PROOF.json'),
        scalar_proof_scope='Finite scalar comparison only; strict qualification is supplied by this separate proof',
        actual_registration_directory=target_rel,launches=calls,reference=reference,replay=actual,matching=matching,
        strict_scalar_comparisons=rows,child_guard_path=str(guard_receipt),
        child_guard_sha256=sha(guard_receipt) if guard_receipt.is_file() else None,
        parent_python_read_guard=parent_guard.summary(),postflight_project_imports=imports,error=error,
        scientific_inputs_or_thresholds_changed=False,
        all_failed_unknown_evidence_retained=True,original_final12_unopened=True)
    write_new(own_review/'INDEPENDENT_REPRODUCTION_PROOF.json',proof)
    print(json.dumps(dict(status=proof['status'],proof=str(own_review/'INDEPENDENT_REPRODUCTION_PROOF.json'),error=error)))
    return 0 if success else 2


def reproduce(name,check_only=False):
    # Initial explicit reads are confined to ROOT by validate_release/safe.
    # The guard is active before importing/calling any frozen project helper.
    release=validate_release(ROOT)
    with PythonReadGuard(ROOT,release['forbidden_roots']) as parent_guard:
        return _reproduce_with_parent_guard(name,check_only,parent_guard)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--name',required=True)
    parser.add_argument('--check-only',action='store_true',help='Verify release/reference only; start no replay')
    args=parser.parse_args()
    try:return reproduce(args.name,args.check_only)
    except Exception as exc:
        print(json.dumps(dict(status='wrapper_failed_unknown',type=type(exc).__name__,message=str(exc),
            actual_attempts_started='Not inferred from this exception; inspect the immutable replay journal')))
        return 2


if __name__=='__main__':raise SystemExit(main())
