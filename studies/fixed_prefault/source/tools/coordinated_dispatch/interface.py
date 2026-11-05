"""Additive coordinated requests on the unchanged selective M0 command queue."""
from __future__ import annotations
import copy
import time

import numpy as np
from hospital_ies.state_transition import clean
from tools.autonomous_dispatch import environment as original_env
from tools.autonomous_dispatch import episode as original_episode
from tools.autonomous_dispatch import standby
from tools.autonomous_dispatch.connections import retention_eligible
from tools.control_improvement.episode import BoundaryBoundPolicy
from . import planner

NUMERICAL_CONTRACT_ID = planner.NUMERICAL_CONTRACT_ID
STANDBY_CONTRACT_ID = planner.STANDBY_CONTRACT_ID
SERVICE_SWITCH_CONTRACT_ID = planner.SERVICE_SWITCH_CONTRACT_ID
PLANNING_INTERFACE = 'coordinated_dispatch.joint_command.v1'
ACTION_SCHEMA = 'coordinated_dispatch.legacy_or_joint_action.v1'


def validate_action(action):
    if not isinstance(action,dict) or 'coordinated_joint' not in action:
        return standby.validate_action(action)
    if set(action) != {'coordinated_joint'}:
        raise ValueError('Coordinated joint requests cannot be combined with another route')
    joint = action['coordinated_joint']
    if not isinstance(joint,dict) or set(joint)-{'options','risk_loads_kW'}:
        raise ValueError('Unsupported coordinated request fields')
    result = copy.deepcopy(action)
    result['coordinated_joint']['options'] = planner._options(joint.get('options',{}))
    if 'risk_loads_kW' in joint:
        loads = np.asarray(joint['risk_loads_kW'],float)
        if loads.shape != (4,3) or not np.isfinite(loads).all() or (loads < 0).any():
            raise ValueError('History envelope must be finite nonnegative 4x3 demand')
        result['coordinated_joint']['risk_loads_kW'] = loads.tolist()
    return result


def _require_contracts(manifest):
    expected = dict(execution_model_id='M0',numerical_contract_id=NUMERICAL_CONTRACT_ID,
        standby_contract_id=STANDBY_CONTRACT_ID,service_switch_contract_id=SERVICE_SWITCH_CONTRACT_ID,
        planning_interface_id=PLANNING_INTERFACE)
    if any(manifest.get(k) != v for k,v in expected.items()) or manifest.get('schemas',{}).get('action') != ACTION_SCHEMA:
        raise ValueError('Missing or unsupported coordinated M0/interface/action contract')


class CoordinatedTransitionCore(standby.StandbyTransitionCore):
    def _solve_coordinated(self, request, loads, availability):
        env = self.env; _,row,_,_ = self.data_at(self.time)
        joint = request['coordinated_joint']; began = time.perf_counter()
        try:
            result = planner.solve_joint(row,env.plant,loads,availability,self.state,env.dt_h,
                float(env.scenario.get('grid_limit_kW',env.config['grid_import_limit_kW'])),
                self.diesel_available(self.time,availability),float(env.config['gt_ramp_kW_per_hour']),
                float(env.config['solver_time_limit_sec']),env.config,
                execution_contract=env.execution_contract,joint_options=joint.get('options',{}),
                risk_loads=joint.get('risk_loads_kW'),retention_eligible=retention_eligible(self))
            self.solver_calls.append(dict(time_minute=self.time,phase='coordinated_command_generation',dt_h=env.dt_h,
                elapsed_seconds=time.perf_counter()-began,optimizer_warning=result.get('optimizer_warning',''),
                status='success',solver_backend_layers=copy.deepcopy(result.get('solver_backend_layers',[]))))
            return result
        except Exception as error:
            self._record_error(error,loads,availability,env.dt_h,'coordinated_command_generation',request,began)

    def generate_command(self, action):
        if not isinstance(action,dict) or 'coordinated_joint' not in action:
            return super().generate_command(action)
        action = validate_action(action)
        if self.failed:
            raise RuntimeError('Previous solver failure retained; reset before continuing')
        _,_,loads,availability = self.data_at(self.time)
        result = self._solve_coordinated(action,loads,availability)
        delayed,release = self.delayed_at(self.time)
        self.sequence += 1
        diagnostic = copy.deepcopy(result['joint_diagnostic'])
        diagnostic.setdefault('planned_unserved_kW',np.asarray(result['lost']).tolist())
        contacts = np.asarray(result.get('command_disconnected',result['lost']))
        diagnostic.setdefault('command_disconnected_kW',contacts.tolist())
        command = dict(id=self.sequence,generated_minute=self.time,
            arrival_minute=float(release) if delayed else self.time,request=copy.deepcopy(action),
            dispatch=copy.deepcopy(result['action']),disconnected=contacts.tolist(),
            reference_cooling_kW=float(loads[:,2].sum()),weight=0.,optimizer_warning=result.get('optimizer_warning',''),
            joint_dispatch=clean(dict(planning_interface=PLANNING_INTERFACE,
                planned_current_action=result['planned_current_action'],diagnostic=diagnostic,
                planning_is_not_execution=True)))
        self.pending_commands.append(command)
        self.log('command_generated',command=command)
        return command


class CoordinatedHospitalIESEnv(standby.StandbyHospitalIESEnv):
    def reset(self, *, scenario, seed=None):
        if self.model_id != 'M0':
            raise ValueError('Coordinated development requires the unchanged M0 execution model')
        function = original_env.SelectiveHospitalIESEnv.reset
        namespace = dict(function.__globals__,JointTransitionCore=CoordinatedTransitionCore)
        return original_episode._isolated(function,namespace)(self,scenario=scenario,seed=seed)


class CoordinatedPolicyEnvironment(standby.StandbyPolicyEnvironment):
    def step(self, ignored_exporter_request):
        function = original_episode.original.PolicyEnvironment.step
        namespace = dict(function.__globals__,validate_action=validate_action)
        return original_episode._isolated(function,namespace)(self,ignored_exporter_request)


def run_policy_episode(env, scenario, policy, seed, manifest, **options):
    _require_contracts(manifest)
    if env.model_id != 'M0':
        raise ValueError('Manifest/environment M0 execution mismatch')
    # Historical joint policies retain their old current-problem binding. The
    # new coordination policy has no bind_current and receives only snapshots.
    bound = BoundaryBoundPolicy(env,policy) if hasattr(policy,'bind_current') else policy
    function = original_episode.original.run_episode
    namespace = dict(function.__globals__,PolicyEnvironment=CoordinatedPolicyEnvironment)
    payload = original_episode._isolated(function,namespace)(env,scenario,bound,seed,manifest,**options)
    payload.update(planning_interface=PLANNING_INTERFACE,action_schema=ACTION_SCHEMA,
        execution_model_id=env.model_id,input_access_receipt=env.input_access_receipt,
        numerical_contract_id=NUMERICAL_CONTRACT_ID,standby_contract_id=STANDBY_CONTRACT_ID,
        service_switch_contract_id=SERVICE_SWITCH_CONTRACT_ID)
    return payload
