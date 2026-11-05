"""Serial, identity-bound dispatch with actual attempts and independent ledgers."""
from __future__ import annotations
import argparse,hashlib,json,math,os,shutil,subprocess,sys,time
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]

def read(path):return json.loads(Path(path).read_text(encoding='utf8'))
def write(path,value):Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf8')
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def utc():return datetime.now(timezone.utc).isoformat()
def attempt_count(rows):return sum(r.get('event')=='started' for r in rows)
def require_budget(rows,limit):
    if attempt_count(rows)>=limit:raise RuntimeError('Actual trajectory attempt budget exhausted')
def require_identity(expected,actual):
    if expected!=actual:raise RuntimeError('Frozen source/input identity drift')
def require_audit(value):
    if value.get('audit_status') not in ('passed','unknown') or value.get('failed_checks'):
        raise RuntimeError('Independent actual accounting audit failed or incomplete')
    required={'observation.complete','balance.e','balance.c','steam.source_sufficiency',
        'diesel.energy_balance','diesel.electrical_action_match','diesel.shaft_budget_match','diesel.shaft_budget_chain',
        'economics.producer_fee_match','economics.raw_fee_complete','identity.producer_raw_sha256',
        'identity.frozen_accounting_inputs','identity.producer_files_unchanged','execution.producer_status'}
    required.update('request_identity.P'+str(p)+'.'+c for p in range(4) for c in ('e','h','c'))
    required.update('storage.'+s+'.'+c for s in ('ups','cold_store') for c in ('balance','terminal'))
    if required-set(value.get('checks_passed',[])):
        raise RuntimeError('Independent audit lacks required positive observable checks')
    allowed_unknown={'steam.full_closure','storage.ups.dynamics','storage.cold_store.dynamics'}
    if set(value.get('unknown_checks',[]))-allowed_unknown:
        raise RuntimeError('Independent audit contains an unqualified unknown')
    observed=value.get('observation',{})
    if not (observed.get('complete') is True and observed.get('contiguous') is True
            and observed.get('unknown_tail_minutes')==0. and observed.get('segment_count',0)>0
            and observed.get('observed_from_minute')==0.):
        raise RuntimeError('Independent audit has an empty or incomplete observation')
    fee=value.get('economics',{}).get('actual_fee_USD2001')
    if isinstance(fee,bool) or not isinstance(fee,(int,float)) or not math.isfinite(fee):
        raise RuntimeError('Independent audit fee is unknown')
    execution=value.get('execution_status',{})
    if execution.get('producer_status')!='completed' or execution.get('payload_status')!='completed':
        raise RuntimeError('Independent audit producer/payload is not complete')
    for name in ('ups','cold_store'):
        stock=value.get('storage',{}).get(name,{})
        active=any(abs(stock.get(k,0.) or 0.)>0. for k in
            ('initial_stock_kWh','final_stock_kWh','charged_input_kWh','discharged_kWh'))
        if active and stock.get('full_dynamics_check')!='pass':
            raise RuntimeError('Independent audit lacks active storage dynamics: '+name)

def verify_completed_run(item,plan,plan_path,*,receipt=None):
    """Re-read actual evidence before accepting a fresh or resumed result."""
    if receipt is not None:
        expected=dict(case_id=item['case_id'],method=item['method'],output=item['output'],plan_sha256=sha(plan_path))
        if receipt.get('accepted_complete') is not True or any(receipt.get(k)!=v for k,v in expected.items()):
            raise RuntimeError('Saved receipt belongs to a different registered plan/case')
    output=Path(item['output']);result=read(output/'RESULT.json');binding=read(output/'BINDINGS.json')
    from tools.sci_engineering.design import digest
    require_identity(plan['identity'],dict(source_sha256=digest(binding['source']),input_sha256=digest(binding['inputs'])))
    require_identity(plan['identity']['source_sha256'],result.get('source_sha256'))
    require_identity(plan['identity']['input_sha256'],digest(result.get('input_sha256')))
    if read(output/'CASE.json').get('case')!=read(item['case_file']):
        raise RuntimeError('Saved actual case differs from registered case')
    if result.get('case_id')!=item['case_id'] or result.get('status')!='completed' or result.get('unknown_tail_minutes')!=0.:
        raise RuntimeError('Saved actual run is not the complete registered case')
    scene=read(item['case_file'])['scenario'];end=float(scene['duration_steps'])*float(scene['step_minutes'])
    if result.get('requested_end_minute')!=end or result.get('observed_until_minute')!=end:
        raise RuntimeError('Saved actual run does not cover the registered endpoint')
    require_identity(result.get('raw_sha256'),sha(output/'trajectory.json.gz'))
    from .ledger import audit_run
    audit=audit_run(output,repo=ROOT);require_audit(audit)
    if audit['observation']['requested_end_minute']!=end:
        raise RuntimeError('Independent audit endpoint differs from registered case')
    return audit
def identity():
    from tools.sci_engineering.runner import source_binding,input_binding
    from tools.sci_engineering.design import digest
    return dict(source_sha256=digest(source_binding(ROOT)),input_sha256=digest(input_binding(ROOT)))
def append(path,value):
    with Path(path).open('a',encoding='utf8') as stream:
        stream.write(json.dumps(value,ensure_ascii=False,allow_nan=False)+'\n');stream.flush()

def register_plan(case_files,destination,*,study_root,max_attempts=512,max_wall_hours=24.):
    from .registration import load_registry,validate_case
    from . import STUDY_ID
    reg=load_registry();dest=Path(destination).resolve();base=Path(study_root).resolve()
    if dest.exists():raise FileExistsError('New plan path required')
    if os.name=='nt' and base.drive.upper() not in ('D:','E:'):raise ValueError('Study data D/E only')
    items=[];ids=set()
    for path in map(Path,case_files):
        case=validate_case(read(path),reg)
        if case['id'] in ids:raise ValueError('Duplicate registered actual case id')
        ids.add(case['id']);items.append(dict(case_file=str(path.resolve()),case_sha256=sha(path),case_id=case['id'],
            method=case['operating_rules_study']['method'],output=str(base/'runs'/case['id'])))
    plan=dict(study_id=STUDY_ID,created_utc=utc(),identity=identity(),items=items,study_root=str(base),
        max_actual_attempts=max_attempts,max_wall_hours=max_wall_hours,physical_workers=1,
        source_history_scope='same-year historically exposed; no external or untouched blind claim',
        continue_only_when='actual run complete and independent observable accounting has no failed checks')
    dest.parent.mkdir(parents=True,exist_ok=True);write(dest,plan)
    return plan

def run_plan(path):
    plan=read(path);base=Path(plan['study_root']);journal=base/'TRAJECTORY_ATTEMPTS.jsonl'
    rows=[json.loads(line) for line in journal.read_text(encoding='utf8').splitlines() if line.strip()] if journal.exists() else []
    began=time.monotonic();finished=[]
    for item in plan['items']:
        require_identity(plan['identity'],identity())
        require_identity(item['case_sha256'],sha(item['case_file']))
        output=Path(item['output'])
        if output.exists():
            receipt=output/'STUDY_ATTEMPT.json'
            if receipt.is_file() and read(receipt).get('accepted_complete') is True:
                verify_completed_run(item,plan,path,receipt=read(receipt))
                finished.append(read(receipt));continue
            raise RuntimeError('Prior unfinished/failed attempt preserved; diagnose and explicitly register a fresh retry: '+str(output))
        require_budget(rows,int(plan['max_actual_attempts']))
        first_start=next((r.get('utc') for r in rows if r.get('event')=='started'),None)
        study_elapsed=(datetime.now(timezone.utc)-datetime.fromisoformat(first_start)).total_seconds() if first_start else time.monotonic()-began
        if study_elapsed>3600.*float(plan['max_wall_hours']):raise RuntimeError('Registered study experiment wall-clock budget exhausted')
        if shutil.disk_usage(base).free<20_000_000_000:raise RuntimeError('Less than20GB free; no new actual dispatch')
        try:
            import psutil
            if psutil.virtual_memory().percent>=85.:raise RuntimeError('Memory dispatch threshold reached')
        except ImportError:pass
        command=[sys.executable,'-B','-m','tools.operating_rules_study.validation','--case-file',item['case_file'],'--output',str(output)]
        event=dict(event='started',case_id=item['case_id'],method=item['method'],utc=utc(),command=command,output=str(output),plan_sha256=sha(path))
        append(journal,event);rows.append(event)
        log=base/'steps'/('actual_'+item['case_id']);log.mkdir(parents=True,exist_ok=False)
        call=dict(command=command,cwd=str(ROOT),started_utc=utc());write(log/'COMMAND.json',call);start=time.monotonic()
        env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTHONIOENCODING='utf-8',TEMP=str(base/'temp'),TMP=str(base/'temp'))
        with (log/'stdout.log').open('x',encoding='utf8') as out,(log/'stderr.log').open('x',encoding='utf8') as err:
            proc=subprocess.run(command,cwd=ROOT,env=env,stdout=out,stderr=err)
        call.update(exit_code=proc.returncode,elapsed_seconds=time.monotonic()-start,finished_utc=utc());write(log/'COMMAND.json',call)
        result=read(output/'RESULT.json') if (output/'RESULT.json').is_file() else {}
        complete=(proc.returncode==0 and result.get('status')=='completed' and result.get('unknown_tail_minutes')==0.)
        audit=None;error=None
        if complete:
            try:
                audit=verify_completed_run(item,plan,path);write(output/'INDEPENDENT_LEDGER.json',audit)
            except Exception as exc:error=dict(type=type(exc).__name__,message=str(exc));complete=False
        attempt=dict(case_id=item['case_id'],method=item['method'],output=str(output),accepted_complete=complete,
            core_status=result.get('status'),unknown_tail_minutes=result.get('unknown_tail_minutes'),exit_code=proc.returncode,
            elapsed_seconds=call['elapsed_seconds'],audit_status=audit.get('audit_status') if audit else None,audit_error=error,
            fee_USD2001=(audit or {}).get('economics',{}).get('actual_fee_USD2001'),finished_utc=utc(),plan_sha256=sha(path))
        if output.exists():write(output/'STUDY_ATTEMPT.json',attempt)
        append(journal,dict(event='finished',**attempt));finished.append(attempt)
        write(Path(path).with_name(Path(path).stem+'_PROGRESS.json'),dict(plan=str(path),records=finished,actual_attempts=attempt_count(rows)))
        print(json.dumps(attempt,ensure_ascii=False),flush=True)
        if not complete:raise RuntimeError('Actual run or observable accounting gate failed; dependent batch stopped, raw prefix retained')
    return finished

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--plan',required=True,type=Path)
    a=p.parse_args(argv);run_plan(a.plan);return 0

if __name__=='__main__':raise SystemExit(main())
