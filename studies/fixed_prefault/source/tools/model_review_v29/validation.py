"""V29 adopted-demand mechanism entry, sharing V28 physics and C/M rules."""
from __future__ import annotations
import argparse,copy,json,os,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from tools.model_review_v29 import STUDY_ID,DEMAND_CONTRACT,CORE_REUSE,ADOPTED_THERMAL_PROFILE

def resolve_case(case,registry,alias=None):
    from tools.model_review_v28.validation import resolve_case as inherited
    if case.get('v28_demand_version')!='V28_adopted':raise ValueError('V29 requires explicit V28_adopted demand version in every case')
    if case.get('v28_thermal_profile')!=ADOPTED_THERMAL_PROFILE:raise ValueError('V29 thermal profile must match the frozen adopted thermal files exactly')
    if registry.get('adopted_thermal_profile',ADOPTED_THERMAL_PROFILE)!=ADOPTED_THERMAL_PROFILE:raise ValueError('Registered adopted thermal binding changed')
    closure=case.get('model_closure_v22',registry.get('model_closure_v22',{}))
    if closure.get('research_stress_proxy') is not False:raise ValueError('Old research stress proxy must remain false')
    value=inherited(case,registry,alias)
    if value['configuration'] not in registry['hardware_profiles']:raise ValueError('Unregistered hardware profile')
    value.update(study_id=STUDY_ID,v29_core_reuse=copy.deepcopy(CORE_REUSE))
    return value

def annotate_result(output,root,case,result):
    """Evaluation-only metadata: this function never touches the environment."""
    from tools.model_review_v29.source_scope import window_scope
    from tools.model_closure_v22.validation import write
    value=copy.deepcopy(result)
    scope=window_scope(root,case['scenario'],observed_until_minute=result.get('observed_until_minute'))
    value.update(v29_source_scope=scope,v29_core_reuse=copy.deepcopy(CORE_REUSE),
                 v28_demand_version='V28_adopted',v28_demand_contract=DEMAND_CONTRACT,
                 v29_mechanism=copy.deepcopy(case.get('v29_mechanism',{})))
    write(Path(output)/'SOURCE_SCOPE.json',scope)
    return value

def main(argv=None):
    from tools.model_correctness_v23 import validation as common
    from tools.model_closure_v22.validation import read,write,sha,now,selected_case,validate_exporter_metadata
    from tools.model_correction_v25.validation import resolve_operating_rule
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--case');p.add_argument('--case-file',type=Path);p.add_argument('--list',action='store_true')
    p.add_argument('--policy',choices=['C','M']);p.add_argument('--configuration');p.add_argument('--operating-rule')
    p.add_argument('--output',type=Path);p.add_argument('--demand-version',choices=['V28_adopted'],default='V28_adopted')
    p.add_argument('--objective-order',choices=['after_cost'],default='after_cost')
    p.add_argument('--thermal-interface',choices=['disabled','validate'],default='disabled')
    p.add_argument('--dynamic-cold-interface',choices=['disabled','validate'],default='disabled')
    p.add_argument('--_worker',action='store_true',help=argparse.SUPPRESS)
    a=p.parse_args(argv)
    if a._worker:
        registration=read(a.output/'CASE.json');registry=registration['registry'];case=resolve_case(registration['case'],registry)
        from tools.model_review_v29.source_scope import window_scope
        window_scope(ROOT,case['scenario']) # Pure input preflight; never enters policy/observations.
        from tools.model_review_v28.environment import with_boundary
        from tools.model_review_v28.validation import policy_identity
        overlay=case.get('v27_load_overlay');extra=copy.deepcopy(overlay['files']) if overlay else {}
        extra.update(case['v28_thermal_profile']['files']);extra.update(registry.get('source_quality',{}).get('files',{}))
        code=common.worker(a.output,model_version='v25',study_identity=STUDY_ID,execution_policy_id=policy_identity(case),
            environment_adapter=with_boundary(overlay,demand_version='V28_adopted',thermal_profile=case['v28_thermal_profile']),registered_extra_inputs=extra)
        if (a.output/'RESULT.json').is_file():
            result=read(a.output/'RESULT.json')
            try:result=annotate_result(a.output,ROOT,case,result)
            except Exception as error:
                write(a.output/'SOURCE_SCOPE_ERROR.json',dict(type=type(error).__name__,message=str(error),core_status=result['status'],metrics_not_zero_imputed=True))
                result.update(status='failed_scope_annotation',scope_annotation_error=dict(type=type(error).__name__,message=str(error)))
                write(a.output/'RESULT.json',result)
                return code or 2
            write(a.output/'RESULT.json',result)
        return code
    if a.thermal_interface!='disabled' or a.dynamic_cold_interface!='disabled':p.error('Dynamic/temperature interfaces are not qualified; thermal_coupling=null')
    registry_path=ROOT/'configs/model_review_v29/cases.json';registry=read(registry_path)
    if a.list:
        for case in registry['cases']:print(case['id']+'\t'+case['purpose'])
        return 0
    if not a.output or bool(a.case)==bool(a.case_file):p.error('Use exactly one --case/--case-file and a fresh --output')
    raw=read(a.case_file) if a.case_file else selected_case(registry,a.case)
    try:case=resolve_case(raw,registry,a.policy)
    except ValueError as error:p.error(str(error))
    if a.configuration and a.configuration!=case['configuration']:p.error('Registered case has fixed hardware identity; choose the matching registered case')
    if a.operating_rule:case['operating_rule_id']=a.operating_rule
    resolve_operating_rule(registry,case);validate_exporter_metadata(case['scenario'])
    from tools.model_review_v29.source_scope import window_scope
    window_scope(ROOT,case['scenario'])
    output=common.checked_output(a.output)
    if os.name=='nt' and output.drive.upper() not in ('D:','E:'):raise ValueError('Large outputs must remain on D or E')
    output.mkdir(parents=True,exist_ok=False)
    write(output/'CASE.json',dict(registry={k:v for k,v in registry.items() if k!='cases'},case=case,case_input_binding=dict(path=str(a.case_file or registry_path),sha256=sha(a.case_file or registry_path))))
    command=[sys.executable,'-I',str(Path(__file__).resolve()),'--_worker','--output',str(output)]
    write(output/'INVOCATION.json',dict(command=command,cwd=str(ROOT),started_utc=now(),runner_script_sha256=sha(Path(__file__)),shared_runner_sha256=sha(Path(common.__file__)),registry_sha256=sha(registry_path),model_version='v25',study_id=STUDY_ID,demand_contract=DEMAND_CONTRACT,demand_version='V28_adopted',reused_core=CORE_REUSE))
    (output/'temp').mkdir();env=dict(os.environ,TEMP=str(output/'temp'),TMP=str(output/'temp'),PYTHONDONTWRITEBYTECODE='1',PYTHONIOENCODING='utf-8')
    with (output/'stdout.log').open('x',encoding='utf8') as out,(output/'stderr.log').open('x',encoding='utf8') as err:r=subprocess.run(command,cwd=ROOT,env=env,stdout=out,stderr=err,check=False)
    invocation=read(output/'INVOCATION.json');invocation.update(finished_utc=now(),exit_code=r.returncode);write(output/'INVOCATION.json',invocation)
    result=read(output/'RESULT.json') if (output/'RESULT.json').is_file() else dict(status='failed_unknown',actual_episode_attempts=None)
    print(json.dumps(dict(case_id=case['id'],status=result['status'],actual_episode_attempts=result['actual_episode_attempts'],output=str(output),exit_code=r.returncode)))
    return r.returncode

if __name__=='__main__':raise SystemExit(main())
