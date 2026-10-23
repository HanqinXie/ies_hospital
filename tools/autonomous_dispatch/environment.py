"""Selective-input environment; versioned planning, original M0 execution.

The original public reset/step forms and physical state transitions are reused.
M1 is explicit and differs only by the documented boundary-order mixin.
"""
from __future__ import annotations
import copy
import json
import math
from pathlib import Path
import time
from types import FunctionType
import numpy as np

from hospital_ies.control_env import HospitalIESEnv as OriginalEnvironment
from hospital_ies.execution import execution_contract as validate_execution_contract
from hospital_ies.model import load_plant
from hospital_ies.resilience import CARRIERS, thermal_data
from hospital_ies.state_transition import TransitionCore, clean
from .contracts import validate_action


class JointTransitionCore(TransitionCore):
    def data_at(self, t):
        hour = int(self.env.scenario['start_hour']) + int(math.floor((float(t) + 1e-9) / 60.))
        allowed = getattr(self.env, 'allowed_hours', None)
        if allowed is not None and hour not in allowed:
            raise ValueError('Physical data access outside the registered allowed support')
        return super().data_at(t)

    def _solve_joint(self, request, loads, availability):
        from .planner import solve_joint
        env = self.env
        _, row, _, _ = self.data_at(self.time)
        joint = request['joint']
        began = time.perf_counter()
        try:
            result = solve_joint(row, env.plant, loads, availability, self.state, env.dt_h,
                float(env.scenario.get('grid_limit_kW', env.config['grid_import_limit_kW'])),
                self.diesel_available(self.time, availability), float(env.config['gt_ramp_kW_per_hour']),
                float(env.config['solver_time_limit_sec']), env.config,
                execution_contract=env.execution_contract, joint_options=joint.get('options', {}),
                risk_loads=joint.get('risk_loads_kW'))
            self.solver_calls.append(dict(time_minute=self.time, phase='joint_command_generation', dt_h=env.dt_h,
                elapsed_seconds=time.perf_counter() - began, optimizer_warning=result.get('optimizer_warning', ''),
                status='success', solver_backend_layers=copy.deepcopy(result.get('solver_backend_layers', []))))
            return result
        except Exception as error:
            self._record_error(error, loads, availability, env.dt_h, 'joint_command_generation', request, began)

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
        command = dict(id=self.sequence, generated_minute=self.time,
            arrival_minute=float(release) if delayed else self.time, request=copy.deepcopy(action),
            dispatch=copy.deepcopy(result['action']), disconnected=np.asarray(result['lost']).tolist(),
            reference_cooling_kW=float(loads[:, 2].sum()), weight=0.,
            optimizer_warning=result.get('optimizer_warning', ''), joint_dispatch=clean({
                'planning_interface': 'joint_command.v1',
                'planned_current_action': result['planned_current_action'],
                'diagnostic': result['joint_diagnostic'],
                'planning_is_not_execution': True}))
        self.pending_commands.append(command)
        self.log('command_generated', command=command)
        return command


class SelectiveHospitalIESEnv(OriginalEnvironment):
    def __init__(self, root, config=None, thermal_cache=None, execution_contract=None, *,
                 allowed_hours, sealed_days, model_id='M0'):
        from .selective_data import selective_inputs
        if thermal_cache is not None:
            raise ValueError('Do not reuse a potentially unfiltered historical thermal cache')
        if model_id not in {'M0', 'M1_ordered_boundary'}:
            raise ValueError('Unknown versioned execution model')
        self.root = Path(root)
        self.execution_contract = validate_execution_contract(execution_contract)
        self.allowed_hours = frozenset(int(h) for h in allowed_hours)
        self.sealed_days = frozenset(int(d) for d in sealed_days)
        self.model_id = model_id
        self.hourly, self.zones, self.groups, self.input_access_receipt = selective_inputs(
            self.root, sorted(self.allowed_hours), sealed_days=sorted(self.sealed_days),
            release_receipt=getattr(self, 'sci_final_release_receipt', None))
        self.plant = load_plant(self.root / 'configs/plant_configs.json', 'doe_capacity_matched_exploratory')
        self.config = (json.loads((self.root / 'configs/resilience_assumptions.json').read_text(encoding='utf-8'))
                       if config is None else copy.deepcopy(config))
        self.thermal_cache = thermal_data(self.root, self.zones,
            float(self.config['mass_participation']), float(self.config['effective_U_W_m2K']))
        self.scenario = None; self.state = None; self.terminated = True
        self.history = []; self.zone_history = []; self.steps_done = 0
        self.cumulative_p0 = {k: 0. for k in CARRIERS}
        self.planning_queries = []

    def reset(self, *, scenario, seed=None):
        first = int(scenario['start_hour'])
        last = first + int(math.ceil(int(scenario['duration_steps']) * float(scenario.get('step_minutes', 15.)) / 60.))
        required = set(range(first, last + 1))
        if not required <= self.allowed_hours or any((h - 1) // 24 in self.sealed_days for h in required):
            raise ValueError('Scenario support is not authorized or overlaps the sealed final backgrounds')
        core_type = JointTransitionCore
        if self.model_id == 'M1_ordered_boundary':
            from .model_order import OrderedBoundaryMixin
            class OrderedJointTransitionCore(OrderedBoundaryMixin, JointTransitionCore):
                pass
            core_type = OrderedJointTransitionCore
        namespace = dict(OriginalEnvironment.reset.__globals__, TransitionCore=core_type)
        reset = FunctionType(OriginalEnvironment.reset.__code__, namespace,
                             OriginalEnvironment.reset.__name__, OriginalEnvironment.reset.__defaults__)
        reset.__kwdefaults__ = OriginalEnvironment.reset.__kwdefaults__
        self.planning_queries = []
        return reset(self, scenario=scenario, seed=seed)
