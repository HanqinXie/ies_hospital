"""Explicit b01 independent-input offline reference through the common queue."""
from __future__ import annotations
import argparse,copy,gzip,hashlib,json,os,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from tools.model_reference_v32 import STUDY_ID,METHOD_ID,EXECUTION_ID
def read(p):return json.loads(Path(p).read_text(encoding='utf-8-sig'))
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()

class OfflinePlanPolicy:
    def __init__(self,alias,config=None,plant=None,policy_id=None):
        if alias!=METHOD_ID:raise ValueError('Explicit V32 method required; never substitute C/M')
        self.config=copy.deepcopy(config or {});self.policy_id=policy_id or METHOD_ID
        path=Path(self.config['actions_path'])
        if self.config.get('actions_sha256') not in (None,sha(path)):raise ValueError('Frozen V32 requests changed')
        schedule=read(path)
        if schedule.get('method_id')!=METHOD_ID or schedule.get('execution_identity')!=EXECUTION_ID or schedule.get('information_permission')!='offline_known_window' or schedule.get('full_continuous_plan_replayed') is not False or schedule.get('planned_storage_ports_executed') is not False:
            raise ValueError('Wrong offline registration or unsupported continuous plan claim')
        self.requests=copy.deepcopy(schedule['requests'])
        if not self.requests or any(self.requests[i]['time_minute']>self.requests[i+1]['time_minute'] for i in range(len(self.requests)-1)):raise ValueError('No ordered verified plan; no zero fallback')
    def decide(self,records):
        now=float(records[-1]['decision_time_min']);available=[r for r in self.requests if r['time_minute']<=now+1e-9]
        if not available:raise ValueError('No registered offline request at this actual decision time')
        request=copy.deepcopy(available[-1])
        return request['action'],dict(policy_id=self.policy_id,information_permission='offline_known_window',execution_identity=EXECUTION_ID,
            schedule_time_minute=request['time_minute'],decision_time_minute=now,full_continuous_plan_replayed=False,
            planned_storage_ports_executed=False,planned_unmet_directly_executed=False,strategy_fallback=False,fallback_reason=None,
            online_solver_calls=0,source='frozen independent-input offline request table; no C/M controller call')

def main(argv=None):
    from tools.model_correctness_v23 import validation as common
    from tools.model_closure_v22.validation import write,now
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--branch',choices=['normal','grid'])
    for name in ('spec','plan','actions','initial','selection'):p.add_argument('--'+name,type=Path)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--_worker',action='store_true',help=argparse.SUPPRESS);a=p.parse_args(argv)
    if a._worker:
        registration=read(a.output/'CASE.json');case=registration['case'];registry=registration['registry']
        if case['policy']['alias']!=METHOD_ID or registration['method_registry'].get(METHOD_ID)!=EXECUTION_ID:raise ValueError('Unregistered external reference')
        from tools.model_review_v30.environment import with_terminal
        from tools.model_review_v30.evaluation import export_evaluation
        from tools.model_review_v29.validation import annotate_result
        extra=copy.deepcopy(case['v28_thermal_profile']['files']);extra.update(registry.get('source_quality',{}).get('files',{}));extra.update(registration['reference_file_bindings'])
        code=common.worker(a.output,model_version='v25',study_identity=STUDY_ID,execution_policy_id=case['v32_reference']['policy_id'],
            controller_factory=OfflinePlanPolicy,environment_adapter=with_terminal(case.get('v27_load_overlay'),thermal_profile=case['v28_thermal_profile']),registered_extra_inputs=extra)
        if (a.output/'RESULT.json').is_file():
            result=read(a.output/'RESULT.json');result.update(core_execution_status=result['status'],export_status='started',external_reference=case['v32_reference'])
            try:
                result=annotate_result(a.output,ROOT,case,result)
                with gzip.open(a.output/'trajectory.json.gz','rt',encoding='utf8') as f:payload=json.load(f)
                evaluation=export_evaluation(payload,result);evaluation['provenance']['evidence_role']='new_v32_independent_offline_reference'
                write(a.output/'EVALUATION.json',evaluation);result.update(v30_evaluation=evaluation,terminal_snapshot=evaluation['terminal_snapshot'],export_status='completed')
            except Exception as error:
                write(a.output/'V32_EXPORT_ERROR.json',dict(type=type(error).__name__,message=str(error),core_status=result['status']))
                result.update(status='failed_v32_export',export_status='failed',export_error=str(error));code=code or 2
            write(a.output/'RESULT.json',result);write(a.output/'STATUS.json',{k:result.get(k) for k in ('case_id','status','core_execution_status','export_status','actual_episode_attempts','observed_until_minute','unknown_tail_minutes')})
        return code
    if a.branch is None or any(getattr(a,k) is None for k in ('spec','plan','actions','initial','selection')):p.error('Explicit b01 --branch and all five frozen reference inputs required')
    from tools.model_reference_v32.snapshot import load_frozen_case
    from tools.model_reference_v32.planner import audit_plan
    from tools.sci_engineering.policy import validate_action
    case,registry=load_frozen_case(ROOT,a.branch);spec=read(a.spec);plan=read(a.plan);schedule=read(a.actions);selection=read(a.selection);initial=read(a.initial)
    formulation=selection['selected_formulation']
    if plan.get('formulation')!=formulation or schedule.get('formulation')!=formulation or plan.get('plan') is None:raise ValueError('Normal/grid must use the same selected verified version')
    if not audit_plan(spec,plan['plan'],formulation=formulation,objective_locks=plan.get('objective_locks'))['valid']:raise ValueError('Selected plan failed independent arithmetic')
    if abs(sum(spec['duration_h'])*60-180.)>1e-8 or float(case['scenario']['start_hour'])!=1160. or case['configuration']!='H1':raise ValueError('Wrong b01 horizon/source/hardware')
    for store in ('ups','cold_store'):
        expected=float(registry['hardware_profiles']['H1']['sci_engineering'].get(store,{}).get('capacity_kWh',0.))*float(registry['hardware_profiles']['H1']['sci_engineering'].get(store,{}).get('initial_soc',0.))
        if initial['initial_stocks'][store]!=expected:raise ValueError('Independent initialization stock differs')
    for request in schedule['requests']:validate_action(request['action'])
    case.update(id='v32_b01_'+a.branch+'_external',study_id=STUDY_ID,evaluation_role='offline_known_window_independent_reference',purpose='Independent raw-input fixed-capacity external reference, not same-information ranking')
    case['v32_reference']=dict(branch=a.branch,policy_id=METHOD_ID+'_'+formulation+'_v32',method_id=METHOD_ID,execution_identity=EXECUTION_ID,
        selected_formulation=formulation,information_permission='offline_known_window',input_independent_of_completed_C_M=True,
        full_continuous_plan_replayed=False,planned_storage_ports_executed=False,planned_unmet_directly_executed=False,
        preparation_lead_minutes=schedule['preparation_lead_minutes'],actual_communication_delay_minutes=case['scenario']['response_delay_minutes'],
        common_preparation='unchanged shared M23 current critical boiler/EC minimum permissions after the frozen ordinary request',
        same_information_ranking_or_bound=False)
    case['policy']=dict(alias=METHOD_ID,config=dict(actions_path=str(a.actions.resolve()),actions_sha256=sha(a.actions),information_permission='offline_known_window',selected_formulation=formulation))
    output=common.checked_output(a.output)
    if os.name=='nt' and output.drive.upper() not in ('D:','E:'):raise ValueError('New raw output must remain D/E')
    output.mkdir(parents=True,exist_ok=False)
    bindings={name:dict(path=str(getattr(a,name).resolve()),sha256=sha(getattr(a,name))) for name in ('spec','plan','actions','initial','selection')}
    write(output/'CASE.json',dict(registry={k:v for k,v in registry.items() if k!='cases'},case=case,method_registry={METHOD_ID:EXECUTION_ID},reference_file_bindings=bindings))
    command=[sys.executable,'-I',str(Path(__file__).resolve()),'--_worker','--output',str(output)]
    write(output/'INVOCATION.json',dict(command=command,cwd=str(ROOT),started_utc=now(),study_id=STUDY_ID,reference_script_sha256=sha(__file__),shared_runner_sha256=sha(common.__file__),reference_file_bindings=bindings))
    (output/'temp').mkdir();env=dict(os.environ,TEMP=str(output/'temp'),TMP=str(output/'temp'),PYTHONDONTWRITEBYTECODE='1',PYTHONIOENCODING='utf-8')
    with (output/'stdout.log').open('x',encoding='utf8') as out,(output/'stderr.log').open('x',encoding='utf8') as err:run=subprocess.run(command,cwd=ROOT,env=env,stdout=out,stderr=err)
    invocation=read(output/'INVOCATION.json');invocation.update(exit_code=run.returncode,finished_utc=now());write(output/'INVOCATION.json',invocation)
    print(json.dumps(dict(output=str(output),exit_code=run.returncode,method_id=METHOD_ID,branch=a.branch,formulation=formulation)))
    return run.returncode
if __name__=='__main__':raise SystemExit(main())
