"""Explicit R_off schedule replay, separate from the causal C/M entry."""
from __future__ import annotations
import argparse,copy,gzip,hashlib,json,os,subprocess,sys
from pathlib import Path

EXECUTION_ID='offline_onoff_preparation_plus_common_realtime_allocation'

def _sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def _read(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))

class OfflineSchedulePolicy:
    def __init__(self,alias,config=None,plant=None,policy_id=None):
        if alias!='R_off':raise ValueError('Explicit R_off registration required; never substitute C/M')
        self.config=copy.deepcopy(config or {});self.policy_id=policy_id or 'R_off_queued_preparation_v30'
        path=Path(self.config['actions_path'])
        if self.config.get('actions_sha256') not in (None,_sha(path)):raise ValueError('Frozen offline schedule changed')
        schedule=_read(path)
        if (schedule.get('execution_identity')!=EXECUTION_ID or schedule.get('information_permission')!='offline_known_window'
            or schedule.get('full_continuous_plan_replayed') is not False):raise ValueError('Wrong offline reference identity/permission')
        self.requests=copy.deepcopy(schedule['requests'])
        if not self.requests or any(self.requests[i]['time_minute']>self.requests[i+1]['time_minute'] for i in range(len(self.requests)-1)):
            raise ValueError('No valid finite plan; do not replace unknown by zero dispatch')

    def decide(self,records):
        minute=float(records[-1]['decision_time_min'])
        available=[r for r in self.requests if r['time_minute']<=minute+1e-9]
        if not available:raise ValueError('No registered offline command for current decision')
        request=copy.deepcopy(available[-1])
        return request['action'],dict(policy_id=self.policy_id,execution_identity=EXECUTION_ID,
            information_permission='offline_known_window',schedule_time_minute=request['time_minute'],
            decision_time_minute=minute,full_continuous_plan_replayed=False,
            strategy_fallback=False,fallback_reason=None,
            storage_plan_written_to_core=False,source='preregistered offline requests, no C/M preparation function')

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--project',type=Path,default=Path(__file__).resolve().parents[2])
    p.add_argument('--base-case',default='dev_grid_180');p.add_argument('--mode',choices=['normal','grid'],default='grid')
    p.add_argument('--actions',type=Path);p.add_argument('--offline-spec',type=Path);p.add_argument('--offline-plan',type=Path)
    p.add_argument('--offline-snapshot',type=Path);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--_worker',action='store_true',help=argparse.SUPPRESS);a=p.parse_args(argv)
    root=a.project.resolve();sys.path[:0]=[str(root),str(root/'src')]
    from tools.model_correctness_v23 import validation as common
    from tools.model_closure_v22.validation import write,now
    from tools.model_review_v30.environment import with_terminal
    from tools.model_review_v30.evaluation import export_evaluation
    from tools.model_review_v30 import STUDY_ID
    from tools.model_review_v29.validation import annotate_result
    if a._worker:
        registration=_read(a.output/'CASE.json');case=registration['case'];registry=registration['registry']
        if case['policy']['alias']!='R_off' or registration['method_registry']['R_off']!=EXECUTION_ID:raise ValueError('Unregistered reference method')
        # No reference schedule can flow into C/M: their separate entry has no
        # custom controller factory or plan-file arguments.
        extra=copy.deepcopy(case['v28_thermal_profile']['files']);extra.update(registry.get('source_quality',{}).get('files',{}))
        extra.update(registration['reference_file_bindings'])
        code=common.worker(a.output,model_version='v25',study_identity=STUDY_ID,
            execution_policy_id='R_off_queued_preparation_45min_v30',controller_factory=OfflineSchedulePolicy,
            environment_adapter=with_terminal(None,thermal_profile=case['v28_thermal_profile']),registered_extra_inputs=extra)
        if (a.output/'RESULT.json').is_file():
            result=_read(a.output/'RESULT.json')
            result.update(core_execution_status=result['status'],export_status='started')
            try:
                result=annotate_result(a.output,root,case,result)
                with gzip.open(a.output/'trajectory.json.gz','rt',encoding='utf8') as stream:payload=json.load(stream)
                evaluation=export_evaluation(payload,result);evaluation['provenance']['evidence_role']='new_actual_offline_schedule_replay'
                write(a.output/'EVALUATION.json',evaluation)
                result.update(v30_evaluation=evaluation,terminal_snapshot=evaluation['terminal_snapshot'],export_status='completed',
                    offline_reference=dict(execution_identity=EXECUTION_ID,full_continuous_plan_replayed=False,
                        causal_comparison_eligible=False,information_permission='offline_known_window',
                        planned_storage_ports_executed=False,reference_file_bindings=registration['reference_file_bindings']))
            except Exception as error:
                write(a.output/'V30_EXPORT_ERROR.json',dict(type=type(error).__name__,message=str(error),core_status=result['status']))
                result.update(status='failed_v30_export',export_status='failed',v30_export_error=str(error));code=code or 2
            write(a.output/'RESULT.json',result)
            write(a.output/'STATUS.json',{key:result.get(key) for key in ('case_id','status','core_execution_status','export_status','actual_episode_attempts','observed_until_minute','unknown_tail_minutes')})
        return code
    if any(x is None for x in (a.actions,a.offline_spec,a.offline_plan,a.offline_snapshot)):p.error('All four frozen reference inputs are required')
    from tools.model_review_v30.validation import resolve_case
    registry=_read(root/'configs/model_review_v30/cases.json')
    base=copy.deepcopy(next(c for c in registry['cases'] if c['id']==a.base_case))
    case=resolve_case(base,registry,'C') # Validate inherited physical scope before explicit new method registration.
    if a.mode=='normal':
        scene=case['scenario'];scene.update(faults={},auxiliary_faults={},fault_duration_minutes=0.,fault_target='',
            normal_verification=True,normal_exogenous_fault_present=False)
    case.update(id='reference_'+a.mode+'_R_off',purpose='Actual constrained offline on/off-preparation replay',
        evaluation_role='offline_known_window_reference_replay')
    case['scenario']['id']='v30_reference_'+a.mode
    actions=a.actions.resolve();spec=_read(a.offline_spec);plan=_read(a.offline_plan);snapshot=_read(a.offline_snapshot)
    if plan.get('plan') is None or plan.get('status') not in ('optimal','time_limit'):raise ValueError('No incumbent plan; no execution/zero-imputation permitted')
    if abs(sum(spec['duration_h'])*60-case['scenario']['duration_steps']*case['scenario']['step_minutes'])>1e-7:
        raise ValueError('Plan and actual observation endpoint differ')
    expected=registry['hardware_profiles'][case['configuration']]['sci_engineering']
    for name in ('ups','cold_store'):
        initial=float(expected.get(name,{}).get('capacity_kWh',0.))*float(expected.get(name,{}).get('initial_soc',0.))
        if abs(snapshot['initial_stocks'][name]-initial)>1e-7:raise ValueError('Reference initial inventory mismatch')
    case['policy']=dict(alias='R_off',config=dict(actions_path=str(actions),actions_sha256=_sha(actions),
        execution_identity=EXECUTION_ID,information_permission='offline_known_window',preparation_lead_minutes=45.,
        decision_sampling_minutes=15.,full_continuous_plan_replayed=False,storage_direct_power_port=False))
    OfflineSchedulePolicy('R_off',case['policy']['config'])
    from tools.sci_engineering.policy import validate_action
    for request in _read(actions)['requests']:validate_action(request['action'])
    output=common.checked_output(a.output)
    if os.name=='nt' and output.drive.upper() not in ('D:','E:'):raise ValueError('Research output D/E only')
    output.mkdir(parents=True,exist_ok=False)
    bindings={name:dict(path=str(path.resolve()),sha256=_sha(path)) for name,path in dict(
        offline_actions=a.actions,offline_spec=a.offline_spec,offline_plan=a.offline_plan,offline_snapshot=a.offline_snapshot).items()}
    write(output/'CASE.json',dict(registry={k:v for k,v in registry.items() if k!='cases'},case=case,
        method_registry={'R_off':EXECUTION_ID},reference_file_bindings=bindings,
        scientific_scope='C-conditioned native request planning envelope; not same-information ranking or a proven bound'))
    command=[sys.executable,'-I',str(Path(__file__).resolve()),'--project',str(root),'--_worker','--output',str(output)]
    write(output/'INVOCATION.json',dict(command=command,cwd=str(root),started_utc=now(),study_id=STUDY_ID,
        reference_script_sha256=_sha(__file__),shared_runner_sha256=_sha(common.__file__),reference_file_bindings=bindings))
    (output/'temp').mkdir();env=dict(os.environ,TEMP=str(output/'temp'),TMP=str(output/'temp'),PYTHONDONTWRITEBYTECODE='1',PYTHONIOENCODING='utf-8')
    with (output/'stdout.log').open('x',encoding='utf8') as out,(output/'stderr.log').open('x',encoding='utf8') as err:
        run=subprocess.run(command,cwd=root,env=env,stdout=out,stderr=err)
    invocation=_read(output/'INVOCATION.json');invocation.update(finished_utc=now(),exit_code=run.returncode);write(output/'INVOCATION.json',invocation)
    print(json.dumps(dict(output=str(output),exit_code=run.returncode,execution_identity=EXECUTION_ID)))
    return run.returncode

if __name__=='__main__':raise SystemExit(main())
