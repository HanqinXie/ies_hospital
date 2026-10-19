"""Explicit original-row numerical route on the unchanged margin interface."""
from __future__ import annotations
import copy
import time

from . import environment as original_env
from . import episode as original_episode
from . import margin, numerics

NUMERICAL_CONTRACT_ID = numerics.NUMERICAL_CONTRACT_ID
PLANNING_INTERFACE = margin.PLANNING_INTERFACE
validate_action = margin.validate_action


def _require_numerical_contract(manifest):
    if manifest.get('numerical_contract_id') != NUMERICAL_CONTRACT_ID:
        raise ValueError('Missing or unsupported numerical contract for the unscaled route')


class UnscaledTransitionCore(margin.MarginTransitionCore):
    def _solve_joint(self, request, loads, availability):
        env = self.env
        _, row, _, _ = self.data_at(self.time)
        joint = request['joint']; began = time.perf_counter()
        try:
            result = numerics.solve_joint(row, env.plant, loads, availability, self.state, env.dt_h,
                float(env.scenario.get('grid_limit_kW', env.config['grid_import_limit_kW'])),
                self.diesel_available(self.time, availability), float(env.config['gt_ramp_kW_per_hour']),
                float(env.config['solver_time_limit_sec']), env.config,
                execution_contract=env.execution_contract, joint_options=joint.get('options', {}),
                risk_loads=joint.get('risk_loads_kW'), retention_eligible=margin.connections.retention_eligible(self))
            self.solver_calls.append(dict(time_minute=self.time, phase='joint_margin_command_generation', dt_h=env.dt_h,
                elapsed_seconds=time.perf_counter()-began, optimizer_warning=result.get('optimizer_warning', ''),
                status='success', solver_backend_layers=copy.deepcopy(result.get('solver_backend_layers', []))))
            return result
        except Exception as error:
            self._record_error(error, loads, availability, env.dt_h, 'joint_margin_command_generation', request, began)


class UnscaledHospitalIESEnv(margin.MarginHospitalIESEnv):
    def reset(self, *, scenario, seed=None):
        function = original_env.SelectiveHospitalIESEnv.reset
        namespace = dict(function.__globals__, JointTransitionCore=UnscaledTransitionCore)
        return original_episode._isolated(function, namespace)(self, scenario=scenario, seed=seed)


def run_policy_episode(env, scenario, policy, seed, manifest, **options):
    _require_numerical_contract(manifest)
    payload = margin.run_policy_episode(env, scenario, policy, seed, manifest, **options)
    payload['numerical_contract_id'] = NUMERICAL_CONTRACT_ID
    return payload
