"""One selective environment for archived M0 and explicit M14 actuator dynamics."""
from __future__ import annotations
import copy
import json
import math
from pathlib import Path
import time
from types import FunctionType
import numpy as np
from hospital_ies.control_env import HospitalIESEnv as NativeEnvironment
from hospital_ies.data_contract import decision_snapshot as native_snapshot
from hospital_ies.state_transition import clean
from tools.autonomous_dispatch.environment import SelectiveHospitalIESEnv
from tools.coordinated_dispatch.interface import CoordinatedTransitionCore,validate_action as old_validate
from tools.executed_service.history import make_snapshot
from tools.integrated_learning import episode as native_episode
from hospital_ies import dataset_export
from .execution import MODEL_ID,EngineeringDynamicsMixin,validate_dynamics
from .policy import validate_action as engineering_validate
from . import planner

ROOT=Path(__file__).resolve().parents[2]


def isolated(function,namespace):
    result=FunctionType(function.__code__,namespace,function.__name__,function.__defaults__,function.__closure__)
    result.__kwdefaults__=function.__kwdefaults__
    return result


def model_contract(model_id,model_config):
    base=json.loads((ROOT/'configs/p1b_execution_research.json').read_text(encoding='utf-8'))
    if model_id=='M0':
        if model_config:raise ValueError('M0 identity cannot accept engineering dynamics')
        return base,{}
    if model_id!=MODEL_ID:raise ValueError('Unknown versioned engineering model')
    dynamics=validate_dynamics(model_config)
    base.update(name=base['name']+'_v14_finite_gt',
        gt_start_delay_minutes=dynamics['gt_start_delay_minutes'],
        provenance='Explicit v14 research dynamics; see docs/v14/SOURCES_AND_ASSUMPTIONS.md')
    return base,dynamics


def validate_action(action):
    return engineering_validate(action) if isinstance(action,dict) and 'engineering_joint' in action else old_validate(action)


class EngineeringCommandCore(CoordinatedTransitionCore):
    def execution_context(self):
        powers=np.asarray(self.state.gt_prev,float)
        clocks=getattr(self,'gt_clocks',None)
        can_start=[True]*3;can_stop=[True]*3
        if clocks is not None:
            can_start=[clocks.earliest_start(i,self.time)<=self.time+1e-9 for i in range(3)]
            can_stop=[clocks.can_stop(i,self.time) for i in range(3)]
            for i in range(3):
                latch=self.interlock_latched.get(f'GT_{i+1}')
                if latch is not None:
                    can_start[i]=bool(can_start[i] and self.env.engineering_config['interlock_rearm_enabled']
                        and self.time>=latch['reset_eligible_minute']-1e-9)
        critical=np.asarray(self.segments[-1]['unserved_kW'],float)[:2] if self.segments else np.zeros((2,3))
        return dict(actual_gt_kW=powers.tolist(),gt_running=(powers>1e-9).tolist(),
            gt_can_start=can_start,gt_can_stop=can_stop,
            gt_start_pending=[i in self.gt_start_pending for i in range(3)],
            arrived_grid_import_limit_kW=float(self.executed['grid_import_kW']),
            arrived_dispatch=copy.deepcopy(self.executed),
            actual_disconnected_kW=np.asarray(self.state.disconnected_loads).tolist(),
            observed_critical_unserved_kW=critical.tolist())

    def generate_command(self,action):
        if not isinstance(action,dict) or 'engineering_joint' not in action:return super().generate_command(action)
        request=engineering_validate(action)
        if self.failed:raise RuntimeError('Previous physical error retained')
        _,row,loads,availability=self.data_at(self.time)
        joint=request['engineering_joint'];began=time.perf_counter()
        try:
            result=planner.solve_joint(row,self.env.plant,loads,availability,self.state,self.env.dt_h,
                float(self.env.scenario.get('grid_limit_kW',self.env.config['grid_import_limit_kW'])),
                self.diesel_available(self.time,availability),float(self.env.config['gt_ramp_kW_per_hour']),
                float(self.env.config['solver_time_limit_sec']),self.env.config,
                execution_contract=self.env.execution_contract,joint_options=joint['options'],
                risk_loads=joint.get('risk_loads_kW'),retention_eligible=False,
                engineering_options=joint['engineering_options'],execution_context=self.execution_context())
        except Exception as exc:self._record_error(exc,loads,availability,self.env.dt_h,'engineering_command_generation',request,began)
        self.solver_calls.append(dict(time_minute=self.time,phase='engineering_command_generation',dt_h=self.env.dt_h,
            elapsed_seconds=time.perf_counter()-began,status='success',
            solver_backend_layers=copy.deepcopy(result.get('solver_backend_layers',[]))))
        delayed,release=self.delayed_at(self.time);self.sequence+=1
        diagnostic=copy.deepcopy(result['joint_diagnostic'])
        diagnostic.setdefault('planned_unserved_kW',np.asarray(result['lost']).tolist())
        contacts=np.asarray(result.get('command_disconnected',result['lost']))
        command=dict(id=self.sequence,generated_minute=self.time,arrival_minute=release if delayed else self.time,
            request=request,dispatch=copy.deepcopy(result['action']),disconnected=contacts.tolist(),
            reference_cooling_kW=float(loads[:,2].sum()),weight=0.,optimizer_warning=result.get('optimizer_warning',''),
            joint_dispatch=clean(dict(planning_interface='engineering_model.ordinary_joint_command.v14',
                planned_current_action=result['planned_current_action'],diagnostic=diagnostic,planning_is_not_execution=True)))
        self.pending_commands.append(command);self.log('command_generated',command=command)
        return command


class DynamicEngineeringCore(EngineeringDynamicsMixin,EngineeringCommandCore):
    pass


class EngineeringHospitalIESEnv(SelectiveHospitalIESEnv):
    def __init__(self,root,*,allowed_hours,sealed_days,model_id='M0',model_config=None):
        contract,extra=model_contract(model_id,model_config or {})
        self.engineering_config=extra
        super().__init__(root,execution_contract=contract,allowed_hours=allowed_hours,sealed_days=sealed_days,model_id='M0')
        self.model_id=model_id

    def reset(self,*,scenario,seed=None):
        first=int(scenario['start_hour'])
        last=first+math.ceil(int(scenario['duration_steps'])*float(scenario.get('step_minutes',15.))/60.)
        required=set(range(first,last+1))
        if not required<=self.allowed_hours or any((h-1)//24 in self.sealed_days for h in required):
            raise ValueError('Scenario support outside registration or intersects final12')
        core_type=EngineeringCommandCore if self.model_id=='M0' else DynamicEngineeringCore
        self.planning_queries=[]
        return isolated(NativeEnvironment.reset,dict(NativeEnvironment.reset.__globals__,TransitionCore=core_type))(
            self,scenario=scenario,seed=seed)


def decision_snapshot(env):
    value=make_snapshot(env,native_snapshot(env))
    observation=env.core.observe()
    if 'engineering_execution' in observation:value['engineering_execution']=copy.deepcopy(observation['engineering_execution'])
    value['engineering_execution_model_id']=env.model_id
    return value


class EngineeringPolicyEnvironment(native_episode.PolicyEnvironment):
    step=isolated(native_episode.PolicyEnvironment.step,dict(native_episode.PolicyEnvironment.step.__globals__,
        decision_snapshot=decision_snapshot,validate_action=validate_action))


def run_episode(env,scenario,policy,seed,manifest):
    exporter=isolated(dataset_export.run_trajectory,dict(dataset_export.run_trajectory.__globals__,decision_snapshot=decision_snapshot))
    function=isolated(native_episode.run_episode,dict(native_episode.run_episode.__globals__,
        PolicyEnvironment=EngineeringPolicyEnvironment,run_trajectory=exporter))
    payload=function(env,scenario,policy,seed,manifest)
    payload.update(execution_model_id=env.model_id,model_config=copy.deepcopy(env.engineering_config),
        engineering_model_contract_id=manifest['engineering_model_contract_id'],
        model_config_signature=manifest['model_config_signature'],input_access_receipt=env.input_access_receipt)
    return payload
