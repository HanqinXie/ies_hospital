"""Explicit M25 operating entry; historical M24 defaults remain separate."""
from __future__ import annotations
import copy

from tools.engineering_completion_v24 import environment as historical
from tools.engineering_completion_v24 import MODEL_ID as HISTORICAL_MODEL_ID
from tools.model_correctness_v23.environment import isolated
from . import MODEL_ID,CONTRACT_ID
from .transfer import FiniteSourceReturnMixin
from .reserve import SustainedReserveMixin,operating_rule_config,reserve_status

validate_scenario=historical.validate_scenario
validate_events=historical.validate_events


class EngineeringCore(SustainedReserveMixin,FiniteSourceReturnMixin,historical.EngineeringCore):
    def observe(self):
        value=super().observe();value['sci_model_id']=MODEL_ID
        value['model_correction_v25']=dict(contract_id=CONTRACT_ID,
            observation_time_minute=self.time,available_time_minute=self.time,
            finite_source_return=self.env.model_correction_config['finite_source_return'],
            current_shaft_rate_kW=self._current_shaft_rate_kW(),
            causal_return_deadline_minute=self._finite_return_deadline(),
            actual_open_minutes=self.env.recovery_config['grid_open_transfer_minutes'])
        _,_,loads,f=self.data_at(self.time)
        value['m25_reserve']=reserve_status(self,loads,f)
        return value

    def _after_actual_segment(self,segment,result):
        super()._after_actual_segment(segment,result)
        segment.update(execution_model_id=MODEL_ID,model_correction_contract_id=CONTRACT_ID,
            m25_transfer=dict(finite_source_return=self.env.model_correction_config['finite_source_return'],
                current_shaft_rate_kW=self._current_shaft_rate_kW(),causal_deadline_minute=self._finite_return_deadline()),
            m25_reserve=copy.deepcopy(result.get('m25_reserve',dict(control_rule=copy.deepcopy(self.env.operating_rule)))))


class OperatingHospitalIESEnv(historical.OperatingHospitalIESEnv):
    engineering_core_type=EngineeringCore
    study_contract_id=CONTRACT_ID
    def __init__(self,root,*,model_id=MODEL_ID,model_config=None,operating_rule=None,**kwargs):
        if model_id!=MODEL_ID:raise ValueError('Explicit M25 model identity required')
        config=copy.deepcopy(model_config or {})
        options=config.pop('model_correction_v25',{})
        if not isinstance(options,dict) or set(options)-{'finite_source_return'}:raise ValueError('Unknown M25 model correction configuration')
        self.model_correction_config=dict({'finite_source_return':True},**options)
        self.operating_rule=operating_rule_config(operating_rule)
        self.control_rule=copy.deepcopy(self.operating_rule)
        self.control_rule_id=self.operating_rule['rule_id']
        if type(self.model_correction_config['finite_source_return']) is not bool:raise ValueError('M25 finite-source return switch must be boolean')
        super().__init__(root,model_id=HISTORICAL_MODEL_ID,model_config=config,**kwargs)
        self.model_id=MODEL_ID
        self.operating_model_config['model_correction_v25']=copy.deepcopy(self.model_correction_config)
        self.operating_execution_contract['model_correction_contract_id']=CONTRACT_ID


def decision_snapshot(env):
    value=historical.decision_snapshot(env);value['engineering_execution_model_id']=MODEL_ID
    value['model_correction_v25']=copy.deepcopy(env.core.observe()['model_correction_v25'])
    return value


class PolicyEnvironment(historical.PolicyEnvironment):
    step=isolated(historical.PolicyEnvironment.step,dict(historical.PolicyEnvironment.step.__globals__,decision_snapshot=decision_snapshot))


OperatingHospitalIESEnv.episode_snapshot_function=staticmethod(decision_snapshot)
OperatingHospitalIESEnv.policy_environment_type=PolicyEnvironment
run_episode=historical.run_episode
