"""Explicit V30 adopted-hardware C/M entry with pure terminal/evaluation exports."""
from __future__ import annotations
import argparse,copy,gzip,json,os,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from tools.model_review_v30 import STUDY_ID

def resolve_case(case,registry,alias=None):
    from tools.model_review_v29.validation import resolve_case as inherited
    value=inherited(case,registry,alias)
    value['study_id']=STUDY_ID
    return value

def main(argv=None):
    from tools.model_correctness_v23 import validation as common
    from tools.model_closure_v22.validation import read,write,sha,now,selected_case,validate_exporter_metadata
    from tools.model_correction_v25.validation import resolve_operating_rule
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--case');p.add_argument('--case-file',type=Path);p.add_argument('--list',action='store_true')
    p.add_argument('--policy',choices=['C','M']);p.add_argument('--output',type=Path)
    p.add_argument('--_worker',action='store_true',help=argparse.SUPPRESS)
    a=p.parse_args(argv)
    registry_path=ROOT/'configs/model_review_v30/cases.json'
    if a._worker:
        registration=read(a.output/'CASE.json');registry=registration['registry'];case=resolve_case(registration['case'],registry)
        from tools.model_review_v30.environment import with_terminal
        from tools.model_review_v30.evaluation import export_evaluation
        from tools.model_review_v29.validation import annotate_result
        from tools.model_review_v29.source_scope import window_scope
        from tools.model_review_v28.validation import policy_identity
        window_scope(ROOT,case['scenario'])
        overlay=case.get('v27_load_overlay');extra=copy.deepcopy(overlay['files']) if overlay else {}
        extra.update(case['v28_thermal_profile']['files']);extra.update(registry.get('source_quality',{}).get('files',{}))
        code=common.worker(a.output,model_version='v25',study_identity=STUDY_ID,execution_policy_id=policy_identity(case),
            environment_adapter=with_terminal(overlay,thermal_profile=case['v28_thermal_profile']),registered_extra_inputs=extra)
        if (a.output/'RESULT.json').is_file():
            result=read(a.output/'RESULT.json')
            result.update(core_execution_status=result['status'],export_status='started')
            try:
                result=annotate_result(a.output,ROOT,case,result)
                with gzip.open(a.output/'trajectory.json.gz','rt',encoding='utf8') as stream:payload=json.load(stream)
                evaluation=export_evaluation(payload,result)
                evaluation['provenance']['evidence_role']='new_actual_simulation'
                write(a.output/'EVALUATION.json',evaluation)
                result.update(v30_evaluation=evaluation,terminal_snapshot=evaluation['terminal_snapshot'],export_status='completed')
            except Exception as error:
                write(a.output/'V30_EXPORT_ERROR.json',dict(type=type(error).__name__,message=str(error),core_status=result['status']))
                result.update(status='failed_v30_export',export_status='failed',v30_export_error=str(error));code=code or 2
            write(a.output/'RESULT.json',result)
            write(a.output/'STATUS.json',{key:result.get(key) for key in ('case_id','status','core_execution_status','export_status','actual_episode_attempts','observed_until_minute','unknown_tail_minutes')})
        return code
    registry=read(registry_path)
    if a.list:
        for case in registry['cases']:print(case['id']+'\t'+case['purpose'])
        return 0
    if not a.output or bool(a.case)==bool(a.case_file):p.error('Use exactly one --case/--case-file and a fresh --output')
    raw=read(a.case_file) if a.case_file else selected_case(registry,a.case)
    try:case=resolve_case(raw,registry,a.policy)
    except ValueError as error:p.error(str(error))
    resolve_operating_rule(registry,case);validate_exporter_metadata(case['scenario'])
    from tools.model_review_v29.source_scope import window_scope
    window_scope(ROOT,case['scenario'])
    output=common.checked_output(a.output)
    if os.name=='nt' and output.drive.upper() not in ('D:','E:'):raise ValueError('Large outputs must remain D/E')
    output.mkdir(parents=True,exist_ok=False)
    write(output/'CASE.json',dict(registry={k:v for k,v in registry.items() if k!='cases'},case=case,
        case_input_binding=dict(path=str(a.case_file or registry_path),sha256=sha(a.case_file or registry_path))))
    command=[sys.executable,'-I',str(Path(__file__).resolve()),'--_worker','--output',str(output)]
    write(output/'INVOCATION.json',dict(command=command,cwd=str(ROOT),started_utc=now(),study_id=STUDY_ID,
        runner_script_sha256=sha(Path(__file__)),shared_runner_sha256=sha(Path(common.__file__)),registry_sha256=sha(registry_path)))
    (output/'temp').mkdir();env=dict(os.environ,TEMP=str(output/'temp'),TMP=str(output/'temp'),PYTHONDONTWRITEBYTECODE='1',PYTHONIOENCODING='utf-8')
    with (output/'stdout.log').open('x',encoding='utf8') as out,(output/'stderr.log').open('x',encoding='utf8') as err:
        run=subprocess.run(command,cwd=ROOT,env=env,stdout=out,stderr=err,check=False)
    invocation=read(output/'INVOCATION.json');invocation.update(finished_utc=now(),exit_code=run.returncode);write(output/'INVOCATION.json',invocation)
    result=read(output/'RESULT.json') if (output/'RESULT.json').is_file() else dict(status='failed_unknown')
    print(json.dumps(dict(case_id=case['id'],status=result['status'],output=str(output),exit_code=run.returncode)))
    return run.returncode

if __name__=='__main__':raise SystemExit(main())
