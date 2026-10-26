"""Bounded complete-state prefix, static inspection and actual continuation."""
from __future__ import annotations
import argparse,copy,gzip,hashlib,json,time,traceback
from pathlib import Path
from .validation import ROOT
from .factory import build_context

def read(p):return json.loads(Path(p).read_text(encoding='utf8'))
def write(p,value):Path(p).write_text(json.dumps(value,indent=2,ensure_ascii=False,allow_nan=False),encoding='utf8')

def validate_resume_binding(current,stored):
    for key in ['source_sha256','input_sha256','scenario_sha256','model_config_sha256']:
        if not current.get(key) or not stored.get(key) or current[key]!=stored[key]:
            raise ValueError('Current source/inputs/scenario/physical config differ from prefix snapshot: '+key)

def main(argv=None):
    from tools.model_correctness_v23.validation import checked_output
    from tools.model_closure_v22.validation import native_process,digest
    from tools.sci_engineering.runner import source_binding,input_binding,compact_metrics
    from .objective_experiment import capture_prefix,continue_from_snapshot
    from .objective_snapshot import BoundExecutionSnapshot
    from .objective_inspection import inspect_snapshot
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('mode',choices=['prefix','inspect','continue'])
    p.add_argument('--original-case',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--snapshot',type=Path);p.add_argument('--order',choices=['legacy_before_cost','after_cost'],default='legacy_before_cost')
    a=p.parse_args(argv);out=checked_output(a.output);out.mkdir(parents=True,exist_ok=False)
    registration=read(a.original_case);case,registry=registration['case'],registration['registry']
    if a.mode!='prefix' and a.snapshot is None:p.error('A bound complete snapshot is required')
    if a.mode=='prefix' and a.order!='legacy_before_cost':p.error('The baseline F prefix uses original legacy order')
    before=dict(source=source_binding(ROOT),inputs=input_binding(ROOT));start=time.perf_counter()
    result=dict(mode=a.mode,order=a.order,status='started',actual_trajectory_attempts=0 if a.mode=='inspect' else 1,
        original_scenario_unchanged=True,requested_end_minute=180.,metrics=None,error=None,source_sha256=digest(before['source']))
    write(out/'BINDINGS.json',before);write(out/'EXPERIMENT_INPUT.json',dict(original_case=str(a.original_case),original_case_sha256=hashlib.sha256(a.original_case.read_bytes()).hexdigest(),snapshot=str(a.snapshot) if a.snapshot else None,order=a.order))
    ctx=None;experiment=None
    try:
        short='prefix' if a.mode=='prefix' else ('A0' if a.order=='legacy_before_cost' else 'A1')+'_'+a.mode
        ctx=build_context(case,registry,out,order=a.order,sidecar_basename=short+'_solver_rows.jsonl.gz',domain_basename=short+'_domains')
        if a.mode=='prefix':
            experiment=capture_prefix(ctx.env,ctx.scene,ctx.controller,int(ctx.scene.get('initialization_seed',20261023)),stop_minute=90.,binding=ctx.binding,manifest=ctx.manifest)
            if experiment.snapshot is None:raise RuntimeError('Complete prefix snapshot not captured: '+json.dumps(experiment.payload.get('error')))
            experiment.snapshot.save(out/'EXECUTION_SNAPSHOT.json')
            result.update(status='captured_prefix',observed_until_minute=90.,unknown_tail_minutes=90.,
                snapshot_content_sha256=experiment.snapshot.content_sha256,snapshot_physical_state_sha256=experiment.snapshot.physical_state_sha256)
        else:
            document=read(a.snapshot)['payload'];bound=document['binding']
            validate_resume_binding(ctx.binding,bound)
            snapshot=BoundExecutionSnapshot.load(a.snapshot,template_env=ctx.env,binding=bound)
            result.update(snapshot_content_sha256=snapshot.content_sha256,snapshot_physical_state_sha256=snapshot.physical_state_sha256)
            if a.mode=='inspect':
                inspection=inspect_snapshot(snapshot,ctx.controller,io_resources=ctx.io_resources)
                write(out/'STATIC_INSPECTION.json',inspection)
                result.update(status=inspection['status'],inspection='STATIC_INSPECTION.json',observed_until_minute=90.,unknown_tail_minutes=None)
            else:
                experiment=continue_from_snapshot(snapshot,ctx.controller,end_minute=180.,manifest=ctx.manifest,io_resources=ctx.io_resources)
                result.update(status=experiment.payload['summary']['status'],observed_until_minute=experiment.payload['summary']['observed_until_minute'],unknown_tail_minutes=max(0.,180.-experiment.payload['summary']['observed_until_minute']))
        if experiment is not None:
            payload=experiment.payload
            with gzip.open(out/'trajectory.json.gz','wt',encoding='utf8') as f:json.dump(payload,f,ensure_ascii=False,allow_nan=False,separators=(',',':'))
            native_process(payload,out/'PROCESS.csv')
            factor=registry['emissions_factor_registry'];factorpath=ROOT/factor['path'];factors=read(factorpath) if factorpath.is_file() and hashlib.sha256(factorpath.read_bytes()).hexdigest()==factor['sha256'] else None
            result['metrics']=compact_metrics(payload,env=experiment.env,factor_registry=factors)
            result['error']=payload.get('error')
            result['initial_state']=payload.get('initial_state')
            if payload.get('error') is not None:result['status']='failed_unknown'
    except Exception as exc:
        result.update(status='failed_unknown',error=dict(type=type(exc).__name__,message=str(exc),traceback=traceback.format_exc()))
        if experiment is not None and not (out/'trajectory.json.gz').exists():
            with gzip.open(out/'trajectory.json.gz','wt',encoding='utf8') as f:json.dump(experiment.payload,f,ensure_ascii=False,allow_nan=False)
    finally:
        if ctx is not None:
            try:result['io_receipt']=ctx.close()
            except Exception as exc:result.update(status='failed_unknown',io_error=repr(exc))
        unchanged=before==dict(source=source_binding(ROOT),inputs=input_binding(ROOT))
        result.update(source_and_inputs_unchanged=unchanged,elapsed_seconds=time.perf_counter()-start)
        if not unchanged:result['status']='failed_unknown'
        write(out/'RESULT.json',result)
    print(json.dumps({k:result.get(k) for k in ['mode','order','status','actual_trajectory_attempts','observed_until_minute','error','elapsed_seconds']},ensure_ascii=False))
    return 0 if result['status'] in ['captured_prefix','completed','static_command_generated'] else 1

if __name__=='__main__':raise SystemExit(main())
