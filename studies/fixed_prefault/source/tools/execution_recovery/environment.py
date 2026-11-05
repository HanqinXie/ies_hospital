"""M18 environment and ordinary-command adapters, isolated from archived code."""
from __future__ import annotations

import copy
import importlib
import math
from types import SimpleNamespace

from hospital_ies.control_env import HospitalIESEnv as NativeEnvironment
from tools.engineering_model.environment import isolated, EngineeringCommandCore
from tools.essential_fixes import environment as essential
from tools.operating_response import environment as operating
from tools.state_coordinated import environment as previous
from .execution import (ExecutionRecoveryCore, MODEL_ID, DYNAMICS_CONTRACT_ID,
    validate_recovery)


STUDY_CONTRACT_ID = 'hospital_ies.execution_recovery_study.v18.v1'


def model_contract(model_id, model_config):
    if model_id != MODEL_ID:
        raise ValueError('M18 environment requires explicit M18_execution_recovery model')
    if not isinstance(model_config, dict):
        raise ValueError('M18 model configuration must be a mapping')
    native = {k: copy.deepcopy(v) for k, v in model_config.items() if k != 'execution_recovery'}
    contract, resolved = essential.model_contract('M16_essential', native)
    recovery = validate_recovery(model_config.get('execution_recovery', {}))
    resolved['execution_recovery'] = recovery
    contract.update(name=contract['name']+'_v18_persistent_permissions_open_transfer',
        provenance='M18 common persistent permission / effective capability separation; explicit research transfer/contact clocks')
    return contract, resolved


class RecoveryCommandCore(ExecutionRecoveryCore):
    def generate_command(self, action):
        routes = (
            ('state_coordinated_joint', 'planner', previous.StateCoordinatedCore.generate_command),
            ('operating_joint', 'operating_planner', operating.OperatingCommandCore.generate_command),
            ('engineering_joint', 'engineering_planner', EngineeringCommandCore.generate_command),
        )
        for key, module, function in routes:
            if isinstance(action, dict) and key in action:
                planner = importlib.import_module('tools.execution_recovery.'+module)
                def solve(*args, **kwargs):
                    kwargs['execution_limits'] = self.execution_limits()
                    return planner.solve_joint(*args, **kwargs)
                namespace = dict(function.__globals__, planner=SimpleNamespace(solve_joint=solve))
                # C18 may extend the policy option validator without changing
                # any archived module global or the ordinary queue interface.
                if key == 'state_coordinated_joint':
                    candidate = importlib.import_module('tools.execution_recovery.policy')
                    namespace['policy'] = candidate
                command = isolated(function, namespace)(self, action)
                command['execution_model_id'] = MODEL_ID
                return command
        return super().generate_command(action)


class OperatingHospitalIESEnv(essential.OperatingHospitalIESEnv):
    def __init__(self, root, *, allowed_hours, sealed_days, model_id=MODEL_ID, model_config=None):
        contract, resolved = model_contract(model_id, model_config or {})
        native = {k: copy.deepcopy(v) for k, v in resolved.items() if k != 'execution_recovery'}
        super().__init__(root, allowed_hours=allowed_hours, sealed_days=sealed_days,
            model_id='M16_essential', model_config=native)
        self.model_id = MODEL_ID
        self.operating_model_config = copy.deepcopy(resolved)
        self.recovery_config = copy.deepcopy(resolved['execution_recovery'])
        self.operating_execution_contract = copy.deepcopy(contract)
        self.execution_contract = {k: copy.deepcopy(v) for k, v in contract.items() if k != 'diesel_dispatch_mode'}

    def reset(self, *, scenario, seed=None):
        first = int(scenario['start_hour'])
        last = first+math.floor(scenario['duration_steps']*scenario.get('step_minutes', 15.)/60.)
        required = set(range(first, last+1))
        if not required <= self.allowed_hours or any((h-1)//24 in self.sealed_days for h in required):
            raise ValueError('Scenario support is not registered or crosses sealed backgrounds')
        self.planning_queries = []
        reset = isolated(NativeEnvironment.reset, dict(NativeEnvironment.reset.__globals__,
            TransitionCore=RecoveryCommandCore, validate_scenario=previous.validate_scenario))
        return reset(self, scenario=scenario, seed=seed)


ExecutionRecoveryHospitalIESEnv = OperatingHospitalIESEnv


def decision_snapshot(env):
    value = copy.deepcopy(previous.decision_snapshot(env))
    value['execution_recovery'] = copy.deepcopy(env.core.observe()['execution_recovery'])
    value['execution_limits'] = env.core.execution_limits()
    value['engineering_execution_model_id'] = MODEL_ID
    return value


def validate_action(action):
    if isinstance(action, dict) and 'state_coordinated_joint' in action:
        return importlib.import_module('tools.execution_recovery.policy').validate_action(action)
    return operating.validate_action(action)


class ExecutionRecoveryPolicyEnvironment(operating.OperatingPolicyEnvironment):
    step = isolated(operating.native_episode.PolicyEnvironment.step,
        dict(operating.native_episode.PolicyEnvironment.step.__globals__,
            decision_snapshot=decision_snapshot, validate_action=validate_action))


def run_episode(env, scenario, controller, seed, manifest):
    execute = isolated(operating.run_episode, dict(operating.run_episode.__globals__,
        decision_snapshot=decision_snapshot, OperatingPolicyEnvironment=ExecutionRecoveryPolicyEnvironment))
    payload = execute(env, scenario, controller, seed, manifest)
    payload.update(engineering_model_contract_id=DYNAMICS_CONTRACT_ID,
        study_contract_id=manifest.get('study_contract_id', STUDY_CONTRACT_ID),
        fuel_budget_semantics='shaft_work_equivalent_kWh', fuel_budget_is_liters=False,
        permission_semantics='latest arrived full command persists; zero revokes; physical gates remain separate')
    return payload
