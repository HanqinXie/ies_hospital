"""Separate M14 reference and M15 local-response environments and legal sensors."""
from __future__ import annotations
import copy,json,math,time,hashlib
from pathlib import Path
import numpy as np
from hospital_ies.control_env import HospitalIESEnv as NativeEnvironment
from hospital_ies.data_contract import decision_snapshot as native_snapshot
from hospital_ies.state_transition import clean
from hospital_ies import dataset_export
from tools.autonomous_dispatch.environment import SelectiveHospitalIESEnv
from tools.engineering_model.environment import (EngineeringCommandCore,
    model_contract as old_model_contract,isolated,decision_snapshot as old_snapshot,
    validate_action as old_validate)
from tools.engineering_model.execution import EngineeringDynamicsMixin,validate_dynamics
from tools.integrated_learning import episode as native_episode
from .execution import LocalResponseMixin,MODEL_ID,DYNAMICS_CONTRACT_ID
from .diesel import validate_diesel_config
from .readiness import SCHEMA as READINESS_SCHEMA, command_ramp_window
from .thermal_readiness import validate_hrsg_thermal_config, hrsg_thermal_readiness
from .policy import validate_action as operating_validate,PLANNING_INTERFACE
from . import planner

ROOT=Path(__file__).resolve().parents[2]

def model_contract(model_id,model_config):
    if model_id in ('M0','M14_engineering'):return old_model_contract(model_id,model_config)
    if model_id!=MODEL_ID or not isinstance(model_config,dict) or set(model_config)-{'gt_dynamics','diesel_local_response','hrsg_thermal_readiness'}:
        raise ValueError('Unknown explicit operating model/configuration')
    gt=validate_dynamics(model_config.get('gt_dynamics',{}))
    dg=validate_diesel_config(model_config.get('diesel_local_response',{}))
    hrsg=validate_hrsg_thermal_config(model_config.get('hrsg_thermal_readiness',{}))
    base,_=old_model_contract('M14_engineering',gt)
    base.update(name=base['name']+'_v15_local_diesel',diesel_dispatch_mode='arrived_ceiling',
        provenance='Explicit finite local shaft/rotor response; no new electrical permission; v15 research sources')
    return base,dict(gt_dynamics=gt,diesel_local_response=dg,hrsg_thermal_readiness=hrsg)

def validate_action(value):
    return operating_validate(value) if isinstance(value,dict) and 'operating_joint' in value else old_validate(value)

class OperatingCommandCore(EngineeringCommandCore):
    def _hrsg_thermal_state(self,availability,minute):
        clocks=getattr(self,'gt_clocks',None)
        return hrsg_thermal_readiness(availability,time_minute=float(minute),
            gt_running=None if clocks is None else clocks.running.tolist(),
            last_actual_on_minute=None if clocks is None else clocks.last_on.tolist(),
            config=getattr(self.env,'hrsg_thermal_config',{}))

    def hrsg_thermal_state(self,minute):
        # Raw hardware availability stays distinct from a causal warm-up gate.
        return self._hrsg_thermal_state(super().data_at(minute)[-1],minute)

    def data_at(self,minute):
        h,row,loads,availability=super().data_at(minute)
        cfg=getattr(self.env,'hrsg_thermal_config',{})
        if cfg.get('warmup_delay_minutes',0.)<=0.:
            return h,row,loads,availability
        return h,row,loads,self._hrsg_thermal_state(availability,minute)['effective_availability']

    def observe(self):
        value=super().observe()
        if getattr(self.env,'hrsg_thermal_config',{}).get('warmup_delay_minutes',0.)>0.:
            value['hrsg_thermal_readiness']=self.hrsg_thermal_state(self.time)
        return value

    def readiness_context(self):
        powers=np.asarray(self.state.gt_prev,float);now=float(self.time)
        clocks=getattr(self,'gt_clocks',None);pending=getattr(self,'gt_start_pending',{})
        latches=getattr(self,'interlock_latched',{})
        _,_,_,f=self.data_at(now);statuses=[];earliest=[];slots=[]
        for i in range(3):
            device=f'GT_{i+1}';p=pending.get(i)
            if powers[i]>1e-9:status='RUNNING'
            elif device in getattr(self.state,'extra_faulted',set()):status='PROTECTION_LOCKED'
            elif f[device]<=1e-9:status='EXOGENOUS_UNAVAILABLE'
            elif device in latches:status='INTERLOCK_LATCHED'
            elif p is not None:status='WAIT_MIN_OFF' if p['started']>now+1e-9 else 'STARTING'
            else:status='OFF'
            statuses.append(status)
            start=clocks.earliest_start(i,now) if clocks is not None else now
            if device in latches:start=max(start,float(latches[device]['reset_eligible_minute']))
            earliest.append(float(start))
            slots.append({k:float(p[k]) for k in ('started','ready','requested')} if p is not None else None)
        ramp=command_ramp_window(executed_gt_kW=self.executed['gt_electric_kW'],
            observation_time_minute=now,previous_ramp_time_minute=self.ramp_time,
            previous_ramp_anchor_kW=getattr(self,'ramp_anchor',None),
            previous_ramp_budget_kW=getattr(self,'ramp_budget',0.),
            dt_minutes=self.env.dt_minutes,gt_ramp_kW_per_h=float(self.env.config['gt_ramp_kW_per_hour']))
        critical=(np.asarray(self.segments[-1]['unserved_kW'],float)[:2].tolist()
            if self.segments else np.zeros((2,3)).tolist())
        queue=[dict(command_id=c['id'],generated_minute=float(c['generated_minute']),
            planned_arrival_minute=None,permitted_dispatch=copy.deepcopy(c['dispatch']))
            for c in self.pending_commands if c['generated_minute']<=now+1e-9]
        return dict(schema=READINESS_SCHEMA,observation_time_minute=now,
            actual_gt_kW=powers.tolist(),gt_physical_status=statuses,earliest_start_minute=earliest,
            pending_starts=slots,gt_can_stop=[clocks.can_stop(i,now) if clocks is not None else True for i in range(3)],
            arrived_dispatch=copy.deepcopy(self.executed),actual_disconnected_kW=np.asarray(self.state.disconnected_loads).tolist(),
            observed_critical_unserved_kW=critical,known_command_queue=queue,**ramp,
            diesel_connected=self.state.diesel_status=='CONNECTED',actual_diesel_kW=float(self.state.last_diesel_kW),
            diesel_arrived_cap_kW=float(self.executed['diesel_kW']))

    def generate_command(self,action):
        if not isinstance(action,dict) or 'operating_joint' not in action:return super().generate_command(action)
        request=operating_validate(action)
        if self.failed:raise RuntimeError('Previous physical error retained')
        _,row,loads,f=self.data_at(self.time);joint=request['operating_joint'];began=time.perf_counter()
        context=self.readiness_context()
        try:
            result=planner.solve_joint(row,self.env.plant,loads,f,self.state,self.env.dt_h,
                float(self.env.scenario.get('grid_limit_kW',self.env.config['grid_import_limit_kW'])),
                self.diesel_available(self.time,f),float(self.env.config['gt_ramp_kW_per_hour']),
                float(self.env.config['solver_time_limit_sec']),self.env.config,
                execution_contract=self.env.execution_contract,joint_options=joint['options'],
                risk_loads=joint.get('risk_loads_kW'),operating_options=joint['operating_options'],
                readiness_context=context)
        except Exception as exc:self._record_error(exc,loads,f,self.env.dt_h,'operating_command_generation',request,began)
        self.solver_calls.append(dict(time_minute=self.time,phase='operating_command_generation',dt_h=self.env.dt_h,
            elapsed_seconds=time.perf_counter()-began,status='success',
            solver_backend_layers=copy.deepcopy(result.get('solver_backend_layers',[]))))
        delayed,release=self.delayed_at(self.time);self.sequence+=1
        diagnostic=copy.deepcopy(result['joint_diagnostic'])
        diagnostic.setdefault('planned_unserved_kW',np.asarray(result['lost']).tolist())
        contacts=np.asarray(result.get('command_disconnected',result['lost']))
        cmd=dict(id=self.sequence,generated_minute=self.time,arrival_minute=release if delayed else self.time,
            request=request,dispatch=copy.deepcopy(result['action']),disconnected=contacts.tolist(),
            reference_cooling_kW=float(loads[:,2].sum()),weight=0.,optimizer_warning=result.get('optimizer_warning',''),
            joint_dispatch=clean(dict(planning_interface=PLANNING_INTERFACE,
                planned_current_action=result['planned_current_action'],diagnostic=diagnostic,
                readiness_context=context,planning_is_not_execution=True)))
        self.pending_commands.append(cmd);self.log('command_generated',command=cmd)
        return cmd

class M14OperatingCore(EngineeringDynamicsMixin,OperatingCommandCore):pass
class LocalOperatingCore(LocalResponseMixin,EngineeringDynamicsMixin,OperatingCommandCore):pass

class OperatingHospitalIESEnv(SelectiveHospitalIESEnv):
    def __init__(self,root,*,allowed_hours,sealed_days,model_id='M14_engineering',model_config=None):
        contract,resolved=model_contract(model_id,model_config or {})
        initial_contract={k:v for k,v in contract.items() if k!='diesel_dispatch_mode'}
        super().__init__(root,allowed_hours=allowed_hours,sealed_days=sealed_days,
            execution_contract=initial_contract,model_id='M0')
        self.model_id=model_id;self.operating_model_config=copy.deepcopy(resolved)
        # Legacy planners accept only their original contract schema. Keep the
        # full model semantics separately for the new execution balance; do not
        # leak a versioned physical port extension into the old planner parser.
        self.execution_contract=copy.deepcopy(initial_contract)
        self.operating_execution_contract=copy.deepcopy(contract)
        self.engineering_config=resolved['gt_dynamics'] if model_id==MODEL_ID else resolved
        self.local_response_config=resolved.get('diesel_local_response',{}) if model_id==MODEL_ID else {}
        self.hrsg_thermal_config=resolved.get('hrsg_thermal_readiness',{}) if model_id==MODEL_ID else {}

    def reset(self,*,scenario,seed=None):
        # data_at observes the hourly row at floor(elapsed/60), including
        # the actual final boundary. Ceil adds an unused future hour whenever
        # the registered duration ends inside an hour (e.g.630min).
        first=int(scenario['start_hour']);last=first+math.floor(scenario['duration_steps']*scenario.get('step_minutes',15.)/60.)
        required=set(range(first,last+1))
        if not required<=self.allowed_hours or any((h-1)//24 in self.sealed_days for h in required):
            raise ValueError('Scenario support is not registered or crosses sealed backgrounds')
        core=LocalOperatingCore if self.model_id==MODEL_ID else M14OperatingCore if self.model_id=='M14_engineering' else OperatingCommandCore
        self.planning_queries=[]
        return isolated(NativeEnvironment.reset,dict(NativeEnvironment.reset.__globals__,TransitionCore=core))(
            self,scenario=scenario,seed=seed)

def decision_snapshot(env):
    value=old_snapshot(env)
    # The old exporter labels simulator release as a plan. No independent ACK
    # exists here, so v15 never exposes that exogenous future clock online.
    for queued in value.get('known_command_queue',[]):queued['planned_arrival_minute']=None
    value['operating_readiness']=copy.deepcopy(env.core.readiness_context())
    if hasattr(env.core,'diesel_rotor'):value['diesel_local_response']=copy.deepcopy(env.core.observe()['diesel_local_response'])
    if getattr(env,'hrsg_thermal_config',{}).get('warmup_delay_minutes',0.)>0.:
        value['hrsg_thermal_readiness']=copy.deepcopy(env.core.hrsg_thermal_state(env.core.time))
    return value

class OperatingPolicyEnvironment(native_episode.PolicyEnvironment):
    step=isolated(native_episode.PolicyEnvironment.step,dict(native_episode.PolicyEnvironment.step.__globals__,
        decision_snapshot=decision_snapshot,validate_action=validate_action))

def run_episode(env,scenario,policy,seed,manifest):
    exporter=isolated(dataset_export.run_trajectory,dict(dataset_export.run_trajectory.__globals__,decision_snapshot=decision_snapshot))
    fn=isolated(native_episode.run_episode,dict(native_episode.run_episode.__globals__,
        PolicyEnvironment=OperatingPolicyEnvironment,run_trajectory=exporter))
    payload=fn(env,scenario,policy,seed,manifest)
    if (payload.get('error') or {}).get('underlying_category')=='local_response_domain_exceeded':
        payload['summary']['original_export_status']=payload['summary']['status']
        payload['summary']['status']='local_response_domain_exceeded'
        payload['error']['classification']='local_response_domain_exceeded'
    payload.update(execution_model_id=env.model_id,model_config=copy.deepcopy(env.operating_model_config),
        operating_execution_contract=copy.deepcopy(env.operating_execution_contract),
        engineering_model_contract_id=DYNAMICS_CONTRACT_ID,model_config_signature=manifest['model_config_signature'],
        input_access_receipt=env.input_access_receipt)
    # Offline source provenance only, after control has finished. Baseline
    # temperatures are never fed to a disturbance forecast or called actual.
    required=set(range(int(scenario['start_hour']),int(scenario['start_hour'])+
        math.floor(scenario['duration_steps']*scenario.get('step_minutes',15.)/60.)+1))
    for receipt in sorted((ROOT/'outputs/v15_engineering_operation_20261017_run01/source_data').glob('*/P3_SOURCE_RECEIPT.json')):
        content=receipt.read_bytes();meta=json.loads(content)
        if required<=set(meta['allowed_hours']):
            payload['p3_baseline_receipt']=dict(path=str(receipt.relative_to(ROOT)),
                sha256=hashlib.sha256(content).hexdigest(),baseline_only=True,
                disturbed_temperature_C=None,source_support_covered=True)
            break
    return payload
