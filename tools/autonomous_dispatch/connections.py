"""v2 separates unserved service from ordinary queued contact instructions.

The original core already restores contacts on command arrival. This module
adds no fast reconnection, diesel drop, capacity, sink or protection change.
"""
from __future__ import annotations
import copy
import time
from types import FunctionType
import numpy as np
from hospital_ies.state_transition import clean
from tools.control_improvement.episode import BoundaryBoundPolicy
from . import contracts as original_contract
from . import environment as original_env
from . import episode as original_episode

MODES = frozenset({'planned_shed', 'retain_when_no_gt'})


def validate_action(action):
    if not isinstance(action, dict) or 'joint' not in action:
        return original_contract.validate_action(action)
    reduced = copy.deepcopy(action)
    joint = reduced.get('joint')
    if not isinstance(joint, dict) or not isinstance(joint.get('options', {}), dict):
        raise ValueError('Malformed joint contact request')
    from .planner_connections import _options
    normalized = _options(joint.get('options', {}))
    joint['options'] = {k: v for k, v in normalized.items()
                        if k in {'variant', 'support_hours', 'risk_tolerance'}}
    original_contract.validate_action(reduced)
    return copy.deepcopy(action)


def retention_eligible(core):
    # These are current delivered/startup/known queued states, never a future script.
    if np.any(np.abs(np.asarray(core.state.gt_prev, float)) > 1e-9) or core.gt_start_pending:
        return False
    return not any(any(float(x) > 1e-9 for x in c['dispatch']['gt_electric_kW'])
                   for c in getattr(core, 'pending_commands', []))


class ConnectionDispatchPolicy(original_contract.JointDispatchPolicy):
    def __init__(self, config, policy_id):
        from .planner_connections import _options
        self.config = _options(config)
        self.policy_id = policy_id
        self.current_problem = None

    def decide(self, records):
        function = original_contract.JointDispatchPolicy.decide
        namespace = dict(function.__globals__, validate_action=validate_action)
        return original_episode._isolated(function, namespace)(self, records)


class ConnectionTransitionCore(original_env.JointTransitionCore):
    def _solve_joint(self, request, loads, availability):
        from .planner_connections import solve_joint
        env = self.env
        _, row, _, _ = self.data_at(self.time)
        joint = request['joint']; began = time.perf_counter()
        try:
            result = solve_joint(row, env.plant, loads, availability, self.state, env.dt_h,
                float(env.scenario.get('grid_limit_kW', env.config['grid_import_limit_kW'])),
                self.diesel_available(self.time, availability), float(env.config['gt_ramp_kW_per_hour']),
                float(env.config['solver_time_limit_sec']), env.config,
                execution_contract=env.execution_contract, joint_options=joint.get('options', {}),
                risk_loads=joint.get('risk_loads_kW'), retention_eligible=retention_eligible(self))
            self.solver_calls.append(dict(time_minute=self.time, phase='joint_contacts_command_generation', dt_h=env.dt_h,
                elapsed_seconds=time.perf_counter()-began, optimizer_warning=result.get('optimizer_warning', ''),
                status='success', solver_backend_layers=copy.deepcopy(result.get('solver_backend_layers', []))))
            return result
        except Exception as error:
            self._record_error(error, loads, availability, env.dt_h, 'joint_contacts_command_generation', request, began)

    def generate_command(self, action):
        validate_action(action)
        if 'joint' not in action:
            return super().generate_command(action)
        if self.failed:
            raise RuntimeError('Previous solver failure retained; reset before continuing')
        _, _, loads, availability = self.data_at(self.time)
        result = self._solve_joint(action, loads, availability)
        delayed, release = self.delayed_at(self.time)
        self.sequence += 1
        diagnostic = copy.deepcopy(result['joint_diagnostic'])
        diagnostic.setdefault('planned_unserved_kW', np.asarray(result['lost']).tolist())
        contacts = np.asarray(result.get('command_disconnected', result['lost']))
        diagnostic.setdefault('command_disconnected_kW', contacts.tolist())
        command = dict(id=self.sequence, generated_minute=self.time,
            arrival_minute=float(release) if delayed else self.time, request=copy.deepcopy(action),
            dispatch=copy.deepcopy(result['action']), disconnected=contacts.tolist(),
            reference_cooling_kW=float(loads[:, 2].sum()), weight=0., optimizer_warning=result.get('optimizer_warning', ''),
            joint_dispatch=clean(dict(planning_interface='joint_command.contacts.v2',
                planned_current_action=result['planned_current_action'], diagnostic=diagnostic,
                planning_is_not_execution=True)))
        self.pending_commands.append(command)
        self.log('command_generated', command=command)
        return command


class ConnectionHospitalIESEnv(original_env.SelectiveHospitalIESEnv):
    def reset(self, *, scenario, seed=None):
        function = original_env.SelectiveHospitalIESEnv.reset
        namespace = dict(function.__globals__, JointTransitionCore=ConnectionTransitionCore)
        return original_episode._isolated(function, namespace)(self, scenario=scenario, seed=seed)


class ConnectionPolicyEnvironment(original_episode.JointPolicyEnvironment):
    def step(self, ignored_exporter_request):
        function = original_episode.original.PolicyEnvironment.step
        namespace = dict(function.__globals__, validate_action=validate_action)
        return original_episode._isolated(function, namespace)(self, ignored_exporter_request)


def run_policy_episode(env, scenario, policy, seed, manifest, **options):
    bound = BoundaryBoundPolicy(env, policy) if hasattr(policy, 'bind_current') else policy
    function = original_episode.original.run_episode
    namespace = dict(function.__globals__, PolicyEnvironment=ConnectionPolicyEnvironment)
    payload = original_episode._isolated(function, namespace)(env, scenario, bound, seed, manifest, **options)
    payload.update(planning_interface='joint_command.contacts.v2', execution_model_id=env.model_id,
                   input_access_receipt=env.input_access_receipt)
    return payload
