"""One explicit study run; preserves all native execution and failure records."""
from __future__ import annotations
import argparse,copy,gzip,json,os,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from tools.operating_rules_study import STUDY_ID
from tools.operating_rules_study.registration import load_registry,validate_case

def preflight(case,registry):
    value=validate_case(case,registry)
    from tools.model_review_v28.environment import validate_scenario,validate_events
    s=copy.deepcopy(value['scenario']);s.pop('model_closure_events',None)
    validate_scenario(s);validate_events(value['scenario'].get('model_closure_events',[]))
    from tools.model_review_v29.source_scope import window_scope
    window_scope(ROOT,value['scenario'])
    return value

def main(argv=None):
    from tools.model_correctness_v23 import validation as common
    from tools.model_closure_v22.validation import read,write,sha,now
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--case-file',type=Path);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--_worker',action='store_true',help=argparse.SUPPRESS)
    a=p.parse_args(argv)
    registry=load_registry()
    if a._worker:
        registration=read(a.output/'CASE.json');case=preflight(registration['case'],registry)
        from tools.operating_rules_study.environment import with_study,response_windows
        from tools.model_review_v30.evaluation import export_evaluation
        from tools.model_review_v29.validation import annotate_result
        meta=case['operating_rules_study']
        overlay=case.get('v27_load_overlay');extra=copy.deepcopy(overlay['files']) if overlay else {}
        extra.update(case['v28_thermal_profile']['files']);extra.update(registry.get('source_quality',{}).get('files',{}))
        adapter=with_study(meta['method'],meta['clock_contract'],overlay_spec=overlay,thermal_profile=case['v28_thermal_profile'])
        code=common.worker(a.output,model_version='v25',study_identity=STUDY_ID,
            execution_policy_id=meta['method']+'_'+meta['clock_contract']+'_20261029',environment_adapter=adapter,
            registered_extra_inputs=extra,controller_factory=adapter.controller_factory)
        result_path=a.output/'RESULT.json'
        if result_path.is_file():
            result=read(result_path);result.update(core_execution_status=result['status'],export_status='started',
                operating_rules_study=dict(meta,response_windows=response_windows(case['scenario'],meta['clock_contract'])))
            try:
                result=annotate_result(a.output,ROOT,case,result)
                with gzip.open(a.output/'trajectory.json.gz','rt',encoding='utf8') as stream:payload=json.load(stream)
                evaluation=export_evaluation(payload,result)
                evaluation['provenance']['evidence_role']='new_operating_rules_actual_simulation'
                write(a.output/'EVALUATION.json',evaluation)
                result.update(v30_evaluation=evaluation,terminal_snapshot=evaluation['terminal_snapshot'],export_status='completed')
            except Exception as error:
                write(a.output/'STUDY_EXPORT_ERROR.json',dict(type=type(error).__name__,message=str(error),core_status=result['status']))
                result.update(status='failed_study_export',export_status='failed',study_export_error=str(error));code=code or 2
            write(result_path,result)
            write(a.output/'STATUS.json',{k:result.get(k) for k in ('case_id','status','core_execution_status','export_status',
                'actual_episode_attempts','observed_until_minute','unknown_tail_minutes')})
        return code
    if not a.case_file:p.error('--case-file is required')
    case=preflight(read(a.case_file),registry)
    output=common.checked_output(a.output)
    if os.name=='nt' and output.drive.upper() not in ('D:','E:'):raise ValueError('Actual study output must remain on D/E')
    output.mkdir(parents=True,exist_ok=False)
    write(output/'CASE.json',dict(registry={k:v for k,v in registry.items() if k!='cases'},case=case,
        case_input_binding=dict(path=str(a.case_file.resolve()),sha256=sha(a.case_file))))
    command=[sys.executable,'-I',str(Path(__file__).resolve()),'--_worker','--output',str(output)]
    write(output/'INVOCATION.json',dict(command=command,cwd=str(ROOT),started_utc=now(),study_id=STUDY_ID,
        runner_script_sha256=sha(__file__),shared_runner_sha256=sha(common.__file__),
        registry_sha256=sha(ROOT/'configs/model_review_v30/cases.json')))
    (output/'temp').mkdir()
    env=dict(os.environ,TEMP=str(output/'temp'),TMP=str(output/'temp'),PYTHONDONTWRITEBYTECODE='1',PYTHONIOENCODING='utf-8')
    with (output/'stdout.log').open('x',encoding='utf8') as out,(output/'stderr.log').open('x',encoding='utf8') as err:
        run=subprocess.run(command,cwd=ROOT,env=env,stdout=out,stderr=err)
    invocation=read(output/'INVOCATION.json');invocation.update(exit_code=run.returncode,finished_utc=now())
    write(output/'INVOCATION.json',invocation)
    result=read(output/'RESULT.json') if (output/'RESULT.json').is_file() else {}
    print(json.dumps(dict(case_id=case['id'],method=case['operating_rules_study']['method'],
        status=result.get('status','failed_unknown'),output=str(output),exit_code=run.returncode)))
    return run.returncode

if __name__=='__main__':raise SystemExit(main())
