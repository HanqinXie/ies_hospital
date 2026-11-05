"""V28 fixed-direct/aux supply entry, retaining after_cost and original physics."""
from __future__ import annotations
import argparse,copy,json,os,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from tools.model_review_v28 import STUDY_ID,DEMAND_CONTRACT

def resolve_case(case,registry,alias=None):
    from tools.model_review_v27.validation import resolve_case as original
    value=original(case,registry,alias,order='after_cost')
    version=value.get('v28_demand_version',registry.get('default_demand_version','A_original_heat'))
    if version not in ('A_original_heat','V28_adopted'):raise ValueError('Unknown registered demand version')
    if version=='A_original_heat' and value.get('v28_thermal_profile') is not None:raise ValueError('A_original_heat forbids an adopted thermal profile; keep the original heat input')
    if value['policy']['alias'] not in ('C','M'):raise ValueError('V28 registered comparison uses established C/M rules')
    if version=='V28_adopted' and not value.get('v28_thermal_profile'):raise ValueError('Adopted V28 demand version requires bound thermal input')
    value.update(study_id=STUDY_ID,v28_demand_version=version,v28_demand_contract=DEMAND_CONTRACT)
    return value

def policy_identity(case):return case['policy']['alias']+'_fixed_direct_aux_v28_'+case['v28_demand_version']

def main(argv=None):
    from tools.model_correctness_v23 import validation as common
    from tools.model_closure_v22.validation import read,write,sha,now,selected_case,validate_exporter_metadata
    from tools.model_review_v27.validation import preflight_case
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--case');p.add_argument('--case-file',type=Path);p.add_argument('--list',action='store_true')
    p.add_argument('--policy',choices=['C','M']);p.add_argument('--configuration')
    p.add_argument('--objective-order',choices=['after_cost'],default='after_cost')
    p.add_argument('--operating-rule');p.add_argument('--output',type=Path)
    p.add_argument('--demand-version',choices=['A_original_heat','V28_adopted'])
    p.add_argument('--thermal-interface',choices=['disabled','validate'],default='disabled')
    p.add_argument('--dynamic-cold-interface',choices=['disabled','validate'],default='disabled')
    p.add_argument('--_worker',action='store_true',help=argparse.SUPPRESS)
    a=p.parse_args(argv)
    if a._worker:
        registration=read(a.output/'CASE.json');case=registration['case'];registry=registration['registry']
        preflight_case(case,registry)
        from tools.model_review_v28.environment import with_boundary
        overlay=case.get('v27_load_overlay');thermal=case.get('v28_thermal_profile');extra=copy.deepcopy(overlay['files']) if overlay else {}
        if thermal:extra.update(thermal['files'])
        return common.worker(a.output,model_version='v25',study_identity=STUDY_ID,execution_policy_id=policy_identity(case),
            environment_adapter=with_boundary(overlay,demand_version=case['v28_demand_version'],thermal_profile=thermal),registered_extra_inputs=extra or None)
    if a.thermal_interface!='disabled' or a.dynamic_cold_interface!='disabled':p.error('Requested dynamic/temperature interface is not qualified; supply uses thermal_coupling=null')
    registry_path=ROOT/'configs/model_review_v28/cases.json';registry=read(registry_path)
    if a.list:
        for case in registry['cases']:print(case['id']+'\t'+case['purpose'])
        return 0
    if not a.output or bool(a.case)==bool(a.case_file):p.error('Use exactly one --case/--case-file and a fresh --output')
    raw=read(a.case_file) if a.case_file else selected_case(registry,a.case)
    if a.demand_version:raw=dict(raw,v28_demand_version=a.demand_version)
    try:case=resolve_case(raw,registry,a.policy)
    except ValueError as exc:p.error(str(exc))
    if a.configuration:case['configuration']=a.configuration
    if case['configuration'] not in registry['hardware_profiles']:raise ValueError('Unregistered hardware profile')
    if a.operating_rule:case['operating_rule_id']=a.operating_rule
    from tools.model_correction_v25.validation import resolve_operating_rule
    resolve_operating_rule(registry,case);validate_exporter_metadata(case['scenario'])
    output=common.checked_output(a.output)
    if os.name=='nt' and output.drive.upper() not in ('D:','E:'):raise ValueError('Large outputs must remain on D or E')
    output.mkdir(parents=True,exist_ok=False)
    write(output/'CASE.json',dict(registry={k:v for k,v in registry.items() if k!='cases'},case=case,case_input_binding=dict(path=str(a.case_file or registry_path),sha256=sha(a.case_file or registry_path))))
    command=[sys.executable,'-I',str(Path(__file__).resolve()),'--_worker','--output',str(output)]
    write(output/'INVOCATION.json',dict(command=command,cwd=str(ROOT),started_utc=now(),runner_script_sha256=sha(Path(__file__)),shared_runner_sha256=sha(Path(common.__file__)),registry_sha256=sha(registry_path),model_version='v25',study_id=STUDY_ID,demand_contract=DEMAND_CONTRACT,demand_version=case['v28_demand_version']))
    (output/'temp').mkdir();env=dict(os.environ,TEMP=str(output/'temp'),TMP=str(output/'temp'),PYTHONDONTWRITEBYTECODE='1',PYTHONIOENCODING='utf-8')
    with (output/'stdout.log').open('x',encoding='utf8') as stdout,(output/'stderr.log').open('x',encoding='utf8') as stderr:r=subprocess.run(command,cwd=ROOT,env=env,stdout=stdout,stderr=stderr,check=False)
    invocation=read(output/'INVOCATION.json');invocation.update(finished_utc=now(),exit_code=r.returncode);write(output/'INVOCATION.json',invocation)
    result=read(output/'RESULT.json') if (output/'RESULT.json').is_file() else dict(status='failed_unknown',actual_episode_attempts=None)
    print(json.dumps(dict(case_id=case['id'],status=result['status'],actual_episode_attempts=result['actual_episode_attempts'],output=str(output),exit_code=r.returncode)))
    return r.returncode

if __name__=='__main__':raise SystemExit(main())
