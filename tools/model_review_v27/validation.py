"""V27 public supply entry: explicit objective order, unqualified heat paths off."""
from __future__ import annotations
import argparse,copy,json,os,subprocess,sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from tools.model_review_v27 import STUDY_ID

ORDERS=('legacy_before_cost','after_cost')

def preflight_case(case,registry):
    settings=case.get('model_correctness_v23',registry.get('model_correctness_v23',{}))
    if settings.get('thermal_coupling') is not None:
        raise ValueError('Temperature acceptance/coupling is not qualified: both frozen low-order models failed original eight summer cases; thermal_coupling must be null')
    for value in [case.get('dynamic_cold_interface'),registry.get('dynamic_cold_interface')]:
        if value not in (None,False,'disabled'):
            raise ValueError('Dynamic cold interface is unqualified: original and minPlant8 complete water boundaries failed frozen admission')
    if 'model_config' in case or 'hardware_profiles' in case:
        raise ValueError('No arbitrary physical capacity override')

def resolve_case(case,registry,alias=None,order='after_cost'):
    from tools.model_correctness_v23 import policy
    from tools.model_closure_v22.validation import runtime_policy
    if order not in ORDERS:raise ValueError('Unregistered objective order')
    value=copy.deepcopy(case)
    if alias is not None:value['policy_override_alias']=alias
    spec=runtime_policy(value,registry,policy)
    spec['config']=policy.policy_config(spec['alias'],spec['config'])
    spec['config']['online_GT_count_order']=order
    value['policy']=dict(alias=spec['alias'],config=spec['config'])
    value.pop('policy_override_alias',None)
    value['v27_objective_order']=order
    value['study_id']=STUDY_ID
    preflight_case(value,registry)
    return value

def policy_identity(case):
    alias=case['policy']['alias'];order=case['policy']['config']['online_GT_count_order']
    if alias=='C':return 'C_'+('reliability_then_cost_v27' if order=='after_cost' else 'legacy_order_v27')
    return alias+'_current_service_v27_'+order

def main(argv=None):
    from tools.model_correctness_v23 import validation as common
    from tools.model_closure_v22.validation import read,write,sha,now,selected_case,validate_exporter_metadata
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--case');p.add_argument('--case-file',type=Path);p.add_argument('--list',action='store_true')
    p.add_argument('--policy',choices=list('RMFCA'));p.add_argument('--configuration')
    p.add_argument('--objective-order',choices=ORDERS,default='after_cost')
    p.add_argument('--operating-rule');p.add_argument('--output',type=Path)
    p.add_argument('--thermal-interface',choices=['disabled','validate'],default='disabled')
    p.add_argument('--dynamic-cold-interface',choices=['disabled','validate'],default='disabled')
    p.add_argument('--_worker',action='store_true',help=argparse.SUPPRESS)
    a=p.parse_args(argv)
    if a._worker:
        registration=read(a.output/'CASE.json');case=registration['case'];registry=registration['registry']
        preflight_case(case,registry)
        from tools.model_review_v27.environment import with_overlay
        overlay=case.get('v27_load_overlay')
        return common.worker(a.output,model_version='v25',study_identity=STUDY_ID,execution_policy_id=policy_identity(case),
            environment_adapter=with_overlay(overlay),registered_extra_inputs=overlay['files'] if overlay else None)
    if a.thermal_interface!='disabled' or a.dynamic_cold_interface!='disabled':
        p.error('Requested interface is unqualified; V27 supports normal supply with thermal_coupling=null only')
    registry_path=ROOT/'configs/model_review_v27/cases.json';registry=read(registry_path)
    if a.list:
        for case in registry['cases']:print(case['id']+'\t'+case['purpose'])
        return 0
    if not a.output or bool(a.case)==bool(a.case_file):p.error('Use exactly one --case/--case-file and a fresh --output')
    raw=read(a.case_file) if a.case_file else selected_case(registry,a.case)
    try:case=resolve_case(raw,registry,a.policy,a.objective_order)
    except ValueError as exc:p.error(str(exc))
    if a.configuration:case['configuration']=a.configuration
    if case['configuration'] not in registry['hardware_profiles']:raise ValueError('Unregistered hardware profile')
    if a.operating_rule:case['operating_rule_id']=a.operating_rule
    from tools.model_correction_v25.validation import resolve_operating_rule
    resolve_operating_rule(registry,case);validate_exporter_metadata(case['scenario'])
    output=common.checked_output(a.output)
    if os.name=='nt' and output.drive.upper() not in ('D:','E:'):raise ValueError('Large run outputs must be on D or E')
    output.mkdir(parents=True,exist_ok=False)
    write(output/'CASE.json',dict(registry={k:v for k,v in registry.items() if k!='cases'},case=case,
        case_input_binding=dict(path=str(a.case_file or registry_path),sha256=sha(a.case_file or registry_path))))
    command=[sys.executable,'-I',str(Path(__file__).resolve()),'--_worker','--output',str(output)]
    write(output/'INVOCATION.json',dict(command=command,cwd=str(ROOT),started_utc=now(),runner_script_sha256=sha(Path(__file__)),
        shared_runner_sha256=sha(Path(common.__file__)),registry_sha256=sha(registry_path),model_version='v25',study_id=STUDY_ID))
    (output/'temp').mkdir()
    env=dict(os.environ,TEMP=str(output/'temp'),TMP=str(output/'temp'),PYTHONDONTWRITEBYTECODE='1',PYTHONIOENCODING='utf-8')
    with (output/'stdout.log').open('x',encoding='utf8') as stdout,(output/'stderr.log').open('x',encoding='utf8') as stderr:
        completed=subprocess.run(command,cwd=ROOT,env=env,stdout=stdout,stderr=stderr,check=False)
    invocation=read(output/'INVOCATION.json');invocation.update(finished_utc=now(),exit_code=completed.returncode);write(output/'INVOCATION.json',invocation)
    result=read(output/'RESULT.json') if (output/'RESULT.json').is_file() else dict(status='failed_unknown',actual_episode_attempts=None)
    print(json.dumps(dict(case_id=case['id'],status=result['status'],actual_episode_attempts=result['actual_episode_attempts'],output=str(output),exit_code=completed.returncode)))
    return completed.returncode

if __name__=='__main__':raise SystemExit(main())
