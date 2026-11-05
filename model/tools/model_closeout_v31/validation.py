"""Four explicitly registered V31 preparation runs through the shared worker."""
from __future__ import annotations
import argparse,copy,gzip,json,os,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from tools.model_closeout_v31 import STUDY_ID,METHODS
from tools.model_closeout_v31.rules import expanded_method_config

def load_registry():return json.loads((ROOT/'configs/model_closeout_v31/cases.json').read_text(encoding='utf8'))

def resolve_case(case,registry):
    from tools.model_review_v30.validation import resolve_case as inherited
    value=inherited(case,registry)
    registration=value.get('v31_preparation',{});method=registration.get('method');base_id=registration.get('base_case_id')
    if method not in METHODS or base_id not in registry['v31_base_M_configs']:raise ValueError('Explicit V31 preparation registration required')
    expected=expanded_method_config(registry['v31_base_M_configs'][base_id],method)
    if value['policy']['alias']!='M' or value['policy']['config']!=expected:raise ValueError('Registered M configuration changed or factory overwrote it')
    if (method=='M_cooling_permission' and value['v30_context']!='b05_L3_ER') or (method=='M_steam_permission' and value['v30_context']!='b07_L4_GT_ER'):
        raise ValueError('Preparation method used outside its registered background')
    value['study_id']=STUDY_ID
    return value

def main(argv=None):
    from tools.model_correctness_v23 import validation as common
    from tools.model_closure_v22.validation import read,write,sha,now,selected_case,validate_exporter_metadata
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--case');p.add_argument('--list',action='store_true');p.add_argument('--output',type=Path)
    p.add_argument('--_worker',action='store_true',help=argparse.SUPPRESS);a=p.parse_args(argv)
    if a._worker:
        registration=read(a.output/'CASE.json');registry=registration['registry'];case=resolve_case(registration['case'],registry)
        from tools.model_closeout_v31.environment import with_preparation
        from tools.model_review_v30.evaluation import export_evaluation
        from tools.model_review_v29.validation import annotate_result
        method=case['v31_preparation']['method'];adapter=with_preparation(method,case.get('v27_load_overlay'),thermal_profile=case['v28_thermal_profile'])
        extra=copy.deepcopy(case['v28_thermal_profile']['files']);extra.update(registry.get('source_quality',{}).get('files',{}))
        code=common.worker(a.output,model_version='v25',study_identity=STUDY_ID,execution_policy_id=method+'_v31',
            environment_adapter=adapter,registered_extra_inputs=extra,controller_factory=getattr(adapter,'controller_factory',None))
        if (a.output/'RESULT.json').is_file():
            result=read(a.output/'RESULT.json');result.update(core_execution_status=result['status'],export_status='started',v31_preparation=case['v31_preparation'])
            try:
                result=annotate_result(a.output,ROOT,case,result)
                with gzip.open(a.output/'trajectory.json.gz','rt',encoding='utf8') as stream:payload=json.load(stream)
                evaluation=export_evaluation(payload,result);evaluation['provenance']['evidence_role']='new_v31'
                write(a.output/'EVALUATION.json',evaluation);result.update(v30_evaluation=evaluation,terminal_snapshot=evaluation['terminal_snapshot'],export_status='completed')
            except Exception as error:
                write(a.output/'V31_EXPORT_ERROR.json',dict(type=type(error).__name__,message=str(error),core_status=result['status']))
                result.update(status='failed_v31_export',export_status='failed',v31_export_error=str(error));code=code or 2
            write(a.output/'RESULT.json',result)
            write(a.output/'STATUS.json',{k:result.get(k) for k in ('case_id','status','core_execution_status','export_status','actual_episode_attempts','observed_until_minute','unknown_tail_minutes')})
        return code
    registry=load_registry()
    if a.list:
        for case in registry['cases']:print(case['id']+'\t'+case['v31_preparation']['method'])
        return 0
    if not a.case or not a.output:p.error('Use a registered --case and fresh --output')
    case=resolve_case(selected_case(registry,a.case),registry);validate_exporter_metadata(case['scenario'])
    output=common.checked_output(a.output)
    if os.name=='nt' and output.drive.upper() not in ('D:','E:'):raise ValueError('Research output D/E only')
    output.mkdir(parents=True,exist_ok=False)
    write(output/'CASE.json',dict(registry={k:v for k,v in registry.items() if k!='cases'},case=case,
        method_registry=registry['v31_methods'],case_input_binding=dict(path=str(ROOT/'configs/model_closeout_v31/cases.json'),sha256=sha(ROOT/'configs/model_closeout_v31/cases.json'))))
    command=[sys.executable,'-I',str(Path(__file__).resolve()),'--_worker','--output',str(output)]
    write(output/'INVOCATION.json',dict(command=command,cwd=str(ROOT),started_utc=now(),study_id=STUDY_ID,
        runner_script_sha256=sha(__file__),shared_runner_sha256=sha(Path(common.__file__)),method=case['v31_preparation']['method']))
    (output/'temp').mkdir();env=dict(os.environ,TEMP=str(output/'temp'),TMP=str(output/'temp'),PYTHONDONTWRITEBYTECODE='1',PYTHONIOENCODING='utf-8')
    with (output/'stdout.log').open('x',encoding='utf8') as out,(output/'stderr.log').open('x',encoding='utf8') as err:run=subprocess.run(command,cwd=ROOT,env=env,stdout=out,stderr=err)
    invocation=read(output/'INVOCATION.json');invocation.update(exit_code=run.returncode,finished_utc=now());write(output/'INVOCATION.json',invocation)
    result=read(output/'RESULT.json') if (output/'RESULT.json').is_file() else {}
    print(json.dumps(dict(case_id=case['id'],status=result.get('status','failed_unknown'),output=str(output),exit_code=run.returncode)))
    return run.returncode

if __name__=='__main__':raise SystemExit(main())
