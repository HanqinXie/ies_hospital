"""M24 explicit entry, using the existing core and one bounded mode-search hook."""
from __future__ import annotations
import copy
import math
import time
from dataclasses import replace
from hospital_ies.execution import ModelConstraintInfeasible,SolverExecutionError
from tools.model_correctness_v23 import environment as historical
from tools.model_correctness_v23 import MODEL_ID as HISTORICAL_MODEL_ID
from . import MODEL_ID,CONTRACT_ID

DEFAULT_TOWER_REJECTION_KW=4220.223408
validate_scenario=historical.validate_scenario
validate_events=historical.validate_events


class EngineeringCore(historical.EngineeringCore):
    def _search_cold_modes(self,*args,**kwargs):
        from .cold_modes import search
        return search(self,*args,**kwargs)

    def _select_current_ports(self,loads,f):
        if not self.env.engineering_completion_config['cold_mode_search']:return super()._select_current_ports(loads,f)
        connected_loads=tuple(tuple(float(value) for value in group) for group in loads)
        key=(self.time,repr(sorted(f.items())),repr(self.executed),self.ups.energy_kWh,self.cold_store.energy_kWh,self.station.boiler_ready,repr(self.gt_start_pending),connected_loads)
        if key==self._m22_selection_key:return
        began=time.perf_counter()
        solve=self._storage_solver(loads,f,self.env.dt_h,execution_action=self.executed)
        try:
            baseline_failure=None
            try:baseline=solve(self._base_ports())
            except (ModelConstraintInfeasible,SolverExecutionError) as exc:baseline=None;baseline_failure=exc
            result,ports,selection=self._search_cold_modes(solve,baseline,loads,f,self.env.dt_h,baseline_failure=baseline_failure)
            self._m22_ports=copy.deepcopy(ports)
            self.solver_calls.append(dict(time_minute=self.time,phase='m24_current_port_selection',status='success',
                inventory_committed=False,elapsed_seconds=time.perf_counter()-began,m24_cold_mode_selection=selection,
                solver_backend_layers=result['solver_backend_layers']))
        except ModelConstraintInfeasible as exc:
            self._m22_ports=self._base_ports()
            self.solver_calls.append(dict(time_minute=self.time,phase='m24_current_port_selection',status='candidate_infeasible',
                message=str(exc),current_only=True,inventory_committed=False,elapsed_seconds=time.perf_counter()-began,
                solver_backend_layers=copy.deepcopy(getattr(exc,'solver_backend_attempts',[]))))
        except Exception as exc:self._record_error(exc,loads,f,self.env.dt_h,'m24_current_port_selection_solver_error',self.active_command,began)
        self._m22_selection_key=key

    def _after_actual_segment(self,segment,result):
        super()._after_actual_segment(segment,result)
        segment.update(execution_model_id=MODEL_ID,engineering_completion_contract_id=CONTRACT_ID,
            m24_cold_mode_selection=copy.deepcopy(result.get('m22_mode_selection')),
            m24_tower_capacity=copy.deepcopy(self.env.tower_capacity_metadata))

    def observe(self):
        value=super().observe();value['sci_model_id']=MODEL_ID
        value['engineering_completion_v24']=dict(cold_mode_search=self.env.engineering_completion_config['cold_mode_search'],
            observation_time_minute=self.time,available_time_minute=self.time,contract_id=CONTRACT_ID,
            tower_capacity=copy.deepcopy(self.env.tower_capacity_metadata))
        return value


class OperatingHospitalIESEnv(historical.OperatingHospitalIESEnv):
    engineering_core_type=EngineeringCore
    study_contract_id=CONTRACT_ID
    def __init__(self,root,*,model_id=MODEL_ID,model_config=None,**kwargs):
        if model_id!=MODEL_ID:raise ValueError('Explicit M24 model identity required')
        config=copy.deepcopy(model_config or {});options=config.pop('engineering_completion_v24',{})
        if not isinstance(options,dict) or set(options)-{'cold_mode_search','tower_rejection_capacity_kW'}:raise ValueError('Unknown M24 engineering configuration')
        self.engineering_completion_config=dict({'cold_mode_search':True,'tower_rejection_capacity_kW':DEFAULT_TOWER_REJECTION_KW},**options)
        if type(self.engineering_completion_config['cold_mode_search']) is not bool:raise ValueError('M24 cold mode switch must be boolean')
        correctness=historical.correctness_config(config.get('model_correctness_v23',{}))
        if self.engineering_completion_config['cold_mode_search'] and not correctness['boiler_domain_search']:
            raise ValueError('M24 cold mode search requires model_correctness_v23.boiler_domain_search for the original objective hierarchy')
        capacity=self.engineering_completion_config['tower_rejection_capacity_kW']
        if isinstance(capacity,bool) or not isinstance(capacity,(int,float)) or not math.isfinite(capacity) or capacity<0.:raise ValueError('M24 tower rejection capacity requires finite nonnegative kW')
        super().__init__(root,model_id=HISTORICAL_MODEL_ID,model_config=config,**kwargs)
        self.model_id=MODEL_ID
        self.plant=replace(self.plant,cooling_tower_rejection_kW=float(capacity))
        self.tower_capacity_metadata=dict(capacity_kW=float(capacity),basis='explicit_research_kW_assumption',
            default_capacity_kW=DEFAULT_TOWER_REJECTION_KW,independent_of_electric_chiller_scaling=True,
            source='configs/plant_configs.json: four nominal 1200-ton towers; legacy exploratory 0.25 x 3.51685284 retained as explicit kW',
            nominal_tower_ton_definition_verified=False,design_wet_bulb_flow_range_verified=False,
            static_rejection_proxy='EC cooling plus unchanged all-in electric coefficient; not separated compressor water-side heat')
        self.config['_engineering_completion_v24']=copy.deepcopy(self.engineering_completion_config)
        self.operating_model_config['engineering_completion_v24']=copy.deepcopy(self.engineering_completion_config)
        self.operating_execution_contract['engineering_completion_contract_id']=CONTRACT_ID


def decision_snapshot(env):
    value=historical.decision_snapshot(env);value['engineering_execution_model_id']=MODEL_ID
    value['engineering_completion_v24']=copy.deepcopy(env.core.observe()['engineering_completion_v24'])
    return value


class PolicyEnvironment(historical.PolicyEnvironment):
    step=historical.isolated(historical.PolicyEnvironment.step,dict(historical.PolicyEnvironment.step.__globals__,decision_snapshot=decision_snapshot))


OperatingHospitalIESEnv.episode_snapshot_function=staticmethod(decision_snapshot)
OperatingHospitalIESEnv.policy_environment_type=PolicyEnvironment
run_episode=historical.run_episode
