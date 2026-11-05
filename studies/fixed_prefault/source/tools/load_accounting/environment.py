"""M20 data-accounting identity with unmodified M19 physical/command logic."""
from __future__ import annotations
import copy
import math

from hospital_ies.control_env import HospitalIESEnv as NativeEnvironment
from tools.engineering_model.environment import isolated
from tools.model_closeout import environment as inherited
from tools.operating_response import environment as operating
from tools.state_coordinated.environment import validate_scenario
from .execution import MODEL_ID, DYNAMICS_CONTRACT_ID, LOAD_ACCOUNTING_CONTRACT_ID, LoadAccountingCore

STUDY_CONTRACT_ID = 'hospital_ies.load_accounting_study.v20.v1'


def model_contract(model_id, model_config):
    if model_id != MODEL_ID:
        raise ValueError('M20 requires its explicit model identity')
    contract, resolved = inherited.model_contract('M19_model_closeout', model_config)
    contract = copy.deepcopy(contract)
    contract.update(name=contract['name']+'_v20_field_accounting',
        load_accounting_contract_id=LOAD_ACCOUNTING_CONTRACT_ID,
        provenance='V20 source-field accounting; M19 physical/queue/threshold implementation unchanged')
    return contract, resolved


class OperatingHospitalIESEnv(inherited.OperatingHospitalIESEnv):
    def __init__(self, root, *, allowed_hours, sealed_days, model_id=MODEL_ID, model_config=None):
        contract, resolved = model_contract(model_id, model_config or {})
        super().__init__(root, allowed_hours=allowed_hours, sealed_days=sealed_days,
                         model_id='M19_model_closeout', model_config=resolved)
        # The inherited initializer reaches the corrected build_zone_groups;
        # no legacy cache, raw rewrite or module-global monkeypatch is used.
        self.model_id = MODEL_ID
        self.operating_model_config = copy.deepcopy(resolved)
        self.operating_execution_contract = copy.deepcopy(contract)
        self.execution_contract = {k: copy.deepcopy(v) for k,v in contract.items()
            if k not in ('diesel_dispatch_mode', 'load_accounting_contract_id')}
        self.input_access_receipt = dict(self.input_access_receipt,
            load_accounting_contract_id=LOAD_ACCOUNTING_CONTRACT_ID,
            original_load_csv_bytes_unchanged=True, grouping_implementation='hospital_ies.resilience.build_zone_groups')

    def reset(self, *, scenario, seed=None):
        first = int(scenario['start_hour'])
        last = first + math.floor(scenario['duration_steps']*scenario.get('step_minutes',15.)/60.)
        required = set(range(first,last+1))
        if not required <= self.allowed_hours or any((h-1)//24 in self.sealed_days for h in required):
            raise ValueError('Scenario support is not registered or crosses sealed backgrounds')
        self.planning_queries = []
        return isolated(NativeEnvironment.reset, dict(NativeEnvironment.reset.__globals__,
            TransitionCore=LoadAccountingCore, validate_scenario=validate_scenario))(
                self, scenario=scenario, seed=seed)


def decision_snapshot(env):
    value = inherited.decision_snapshot(env)
    value['engineering_execution_model_id'] = MODEL_ID
    value['load_accounting'] = copy.deepcopy(env.core.observe()['load_accounting'])
    return value


validate_action = inherited.validate_action


class PolicyEnvironment(operating.OperatingPolicyEnvironment):
    step = isolated(operating.native_episode.PolicyEnvironment.step,
        dict(operating.native_episode.PolicyEnvironment.step.__globals__,
             decision_snapshot=decision_snapshot, validate_action=validate_action))


def run_episode(env, scenario, controller, seed, manifest):
    execute = isolated(operating.run_episode, dict(operating.run_episode.__globals__,
        decision_snapshot=decision_snapshot, OperatingPolicyEnvironment=PolicyEnvironment))
    payload = execute(env, scenario, controller, seed, manifest)
    payload.update(execution_model_id=MODEL_ID,
        engineering_model_contract_id=DYNAMICS_CONTRACT_ID,
        study_contract_id=STUDY_CONTRACT_ID,
        load_accounting_contract_id=LOAD_ACCOUNTING_CONTRACT_ID,
        fuel_budget_semantics='shaft_work_equivalent_kWh', fuel_budget_is_liters=False,
        permission_semantics='latest arrived full command persists; zero revokes; physical gates remain separate')
    return payload
