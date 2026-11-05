"""Explicit V16 environment; immutable M14/M15 reference branches remain callable."""
from __future__ import annotations
import copy
import math
from hospital_ies.control_env import HospitalIESEnv as NativeEnvironment
from tools.engineering_model.environment import isolated
from tools.engineering_model.execution import EngineeringDynamicsMixin
from tools.operating_response import environment as old
from .execution import EssentialResponseMixin, MODEL_ID, DYNAMICS_CONTRACT_ID


def model_contract(model_id, model_config):
    if model_id != MODEL_ID:
        return old.model_contract(model_id, model_config)
    if not isinstance(model_config, dict) or set(model_config)-{
            'gt_dynamics', 'diesel_local_response', 'hrsg_thermal_readiness', 'gt_floor_correction'}:
        raise ValueError('Unknown essential engineering configuration')
    correction = copy.deepcopy(model_config.get('gt_floor_correction', {'enabled': True}))
    if (not isinstance(correction, dict) or set(correction) != {'enabled'}
            or type(correction['enabled']) is not bool):
        raise ValueError('Floor correction only accepts explicit enabled boolean')
    contract, resolved = old.model_contract('M15_local_response',
        {k:v for k,v in model_config.items() if k != 'gt_floor_correction'})
    resolved['gt_floor_correction'] = correction
    contract.update(name=contract['name']+'_v16_essential',
        provenance='V16 shared boundary floor regulation and fuel depletion continuation; shaft-work equivalent budget')
    return contract, resolved


class EssentialOperatingCore(EssentialResponseMixin, EngineeringDynamicsMixin, old.OperatingCommandCore):
    pass


class OperatingHospitalIESEnv(old.OperatingHospitalIESEnv):
    def __init__(self, root, *, allowed_hours, sealed_days, model_id=MODEL_ID, model_config=None):
        if model_id != MODEL_ID:
            super().__init__(root, allowed_hours=allowed_hours, sealed_days=sealed_days,
                             model_id=model_id, model_config=model_config)
            return
        contract, resolved = model_contract(model_id, model_config or {})
        inherited = {k:v for k,v in resolved.items() if k != 'gt_floor_correction'}
        super().__init__(root, allowed_hours=allowed_hours, sealed_days=sealed_days,
                         model_id='M15_local_response', model_config=inherited)
        self.model_id = MODEL_ID
        self.operating_model_config = copy.deepcopy(resolved)
        self.gt_floor_correction_config = copy.deepcopy(resolved['gt_floor_correction'])
        self.operating_execution_contract = copy.deepcopy(contract)
        self.execution_contract = {k:v for k,v in contract.items() if k != 'diesel_dispatch_mode'}

    def reset(self, *, scenario, seed=None):
        if self.model_id != MODEL_ID:
            return super().reset(scenario=scenario, seed=seed)
        first = int(scenario['start_hour'])
        last = first+math.floor(scenario['duration_steps']*scenario.get('step_minutes', 15.)/60.)
        required = set(range(first, last+1))
        if not required <= self.allowed_hours or any((h-1)//24 in self.sealed_days for h in required):
            raise ValueError('Scenario support is not registered or crosses sealed backgrounds')
        self.planning_queries = []
        return isolated(NativeEnvironment.reset,
            dict(NativeEnvironment.reset.__globals__, TransitionCore=EssentialOperatingCore))(
                self, scenario=scenario, seed=seed)


EssentialHospitalIESEnv = OperatingHospitalIESEnv


def run_episode(env, scenario, policy, seed, manifest):
    from .data_design import execution_model_contract_id
    payload = old.run_episode(env, scenario, policy, seed, manifest)
    payload['engineering_model_contract_id'] = execution_model_contract_id(env.model_id)
    payload['study_contract_id'] = DYNAMICS_CONTRACT_ID
    if env.model_id == MODEL_ID:
        payload['fuel_budget_semantics'] = 'shaft_work_equivalent_kWh'
        payload['fuel_budget_is_liters'] = False
    return payload
