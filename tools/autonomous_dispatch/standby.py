"""Soft standby request contract; original selective execution and queue retained."""
from __future__ import annotations
import copy
import time

from . import environment as original_env
from . import episode as original_episode
from . import margin, numerical_route, planner_standby

NUMERICAL_CONTRACT_ID = planner_standby.NUMERICAL_CONTRACT_ID
STANDBY_CONTRACT_ID = planner_standby.STANDBY_CONTRACT_ID
PLANNING_INTERFACE = 'joint_command.standby.v5'
ACTION_SCHEMA = 'autonomous_dispatch.legacy_or_joint_action.v5'


def validate_action(action):
    if not isinstance(action, dict) or 'joint' not in action:
        return margin.validate_action(action)
    canonical = copy.deepcopy(action); joint = canonical.get('joint')
    if not isinstance(joint, dict) or not isinstance(joint.get('options', {}), dict):
        raise ValueError('Malformed joint standby request')
    joint['options'] = planner_standby._options(joint.get('options', {}))
    reduced = copy.deepcopy(canonical)
    reduced['joint']['options'].pop('preserve_one_gt_standby')
    margin.validate_action(reduced)
    return canonical


def _require_contracts(manifest):
    numerical_route._require_numerical_contract(manifest)
    if (manifest.get('standby_contract_id') != STANDBY_CONTRACT_ID
            or manifest.get('planning_interface_id') != PLANNING_INTERFACE
            or manifest.get('schemas', {}).get('action') != ACTION_SCHEMA):
        raise ValueError('Missing or unsupported standby/interface/action contract')


class StandbyDispatchPolicy(margin.MarginDispatchPolicy):
    def __init__(self, config, policy_id):
        self.config = planner_standby._options(config)
        self.policy_id = policy_id
        self.current_problem = None

    def decide(self, records):
        function = margin.MarginDispatchPolicy.decide
        namespace = dict(function.__globals__, validate_action=validate_action, PLANNING_INTERFACE=PLANNING_INTERFACE)
        return original_episode._isolated(function, namespace)(self, records)


class StandbyTransitionCore(numerical_route.UnscaledTransitionCore):
    # Exact existing function code/closure: only interface globals are rebound.
    generate_command = original_episode._isolated(margin.MarginTransitionCore.generate_command,
        dict(margin.MarginTransitionCore.generate_command.__globals__,
             validate_action=validate_action, PLANNING_INTERFACE=PLANNING_INTERFACE))

    def _solve_joint(self, request, loads, availability):
        env = self.env
        _, row, _, _ = self.data_at(self.time)
        joint = request['joint']; began = time.perf_counter()
        try:
            result = planner_standby.solve_joint(row, env.plant, loads, availability, self.state, env.dt_h,
                float(env.scenario.get('grid_limit_kW', env.config['grid_import_limit_kW'])),
                self.diesel_available(self.time, availability), float(env.config['gt_ramp_kW_per_hour']),
                float(env.config['solver_time_limit_sec']), env.config,
                execution_contract=env.execution_contract, joint_options=joint.get('options', {}),
                risk_loads=joint.get('risk_loads_kW'), retention_eligible=margin.connections.retention_eligible(self))
            self.solver_calls.append(dict(time_minute=self.time, phase='joint_standby_command_generation', dt_h=env.dt_h,
                elapsed_seconds=time.perf_counter()-began, optimizer_warning=result.get('optimizer_warning', ''),
                status='success', solver_backend_layers=copy.deepcopy(result.get('solver_backend_layers', []))))
            return result
        except Exception as error:
            self._record_error(error, loads, availability, env.dt_h, 'joint_standby_command_generation', request, began)


class StandbyHospitalIESEnv(numerical_route.UnscaledHospitalIESEnv):
    def reset(self, *, scenario, seed=None):
        function = original_env.SelectiveHospitalIESEnv.reset
        namespace = dict(function.__globals__, JointTransitionCore=StandbyTransitionCore)
        return original_episode._isolated(function, namespace)(self, scenario=scenario, seed=seed)


class StandbyPolicyEnvironment(margin.MarginPolicyEnvironment):
    def step(self, ignored_exporter_request):
        function = original_episode.original.PolicyEnvironment.step
        namespace = dict(function.__globals__, validate_action=validate_action)
        return original_episode._isolated(function, namespace)(self, ignored_exporter_request)


def run_policy_episode(env, scenario, policy, seed, manifest, **options):
    _require_contracts(manifest)
    function = margin.run_policy_episode
    namespace = dict(function.__globals__, MarginPolicyEnvironment=StandbyPolicyEnvironment,
                     PLANNING_INTERFACE=PLANNING_INTERFACE)
    payload = original_episode._isolated(function, namespace)(env, scenario, policy, seed, manifest, **options)
    payload.update(numerical_contract_id=NUMERICAL_CONTRACT_ID, standby_contract_id=STANDBY_CONTRACT_ID)
    return payload
