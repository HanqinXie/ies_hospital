"""One C planning route over unchanged M16 execution and ordinary commands.

The additive telemetry block is a completed-interval meter, not an actuator
permission or an instantaneous delivery assertion at a new event boundary.
"""
from __future__ import annotations

import copy
import math
from numbers import Real
from types import SimpleNamespace
import time

import numpy as np

from hospital_ies.resilience import validate_scenario as native_validate_scenario
from tools.engineering_model.environment import isolated
from tools.essential_fixes import environment as base
from tools.operating_response import environment as old
from . import planner, policy


MODEL_ID = base.MODEL_ID
STUDY_CONTRACT_ID = 'hospital_ies.state_coordinated_study.v17.v1'
TELEMETRY_SCHEMA = 'hospital_ies.completed_actual_supply.v17.v1'
model_contract = base.model_contract
ACTUAL_PORTS = (
    'gt_electric_kW', 'grid_import_kW', 'diesel_kW', 'boiler_steam_lbph',
    'hrsg_steam_lbph', 'absorption_cooling_kW', 'electric_chiller_cooling_kW',
    'tower_rejection_kW', 'gt_fuel_MMBtu_h',
)


def validate_scenario(scene):
    """Admit only an explicitly registered L4b propagation-off ablation.

    Native label validation requires propagation for every stage-4 scenario.
    Its complete validation is retained; only that label-level requirement is
    lifted for this versioned matched comparison. Actual faults and stage stay.
    """
    if (scene.get('v17_matched_propagation_ablation') is True
            and scene.get('stage') == 4 and scene.get('event_class') == 'L4b'
            and scene.get('enable_propagation') is False):
        candidate = copy.deepcopy(scene)
        candidate['enable_propagation'] = True
        checked = native_validate_scenario(candidate)
        checked['enable_propagation'] = False
        return checked
    return native_validate_scenario(scene)


def validate_action(action):
    if isinstance(action, dict) and 'state_coordinated_joint' in action:
        return policy.validate_action(action)
    return old.validate_action(action)


class StateCoordinatedCore(base.EssentialOperatingCore):
    """C changes one generated ordinary command, never physical advance."""

    def generate_command(self, action):
        if not isinstance(action, dict) or 'state_coordinated_joint' not in action:
            return super().generate_command(action)
        request = policy.validate_action(action)
        if self.failed:
            raise RuntimeError('Previous physical error retained')
        _, row, loads, f = self.data_at(self.time)
        joint = request['state_coordinated_joint']
        context = self.readiness_context()
        # Extras belong only to C. The inherited E/K1 strict ledger is unchanged.
        context['diesel_local_response'] = copy.deepcopy(self.observe()['diesel_local_response'])
        context['diesel_start_minute'] = self.state.diesel_start_minute
        began = time.perf_counter()
        try:
            result = planner.solve_joint(
                row, self.env.plant, loads, f, self.state, self.env.dt_h,
                float(self.env.scenario.get('grid_limit_kW', self.env.config['grid_import_limit_kW'])),
                self.diesel_available(self.time, f), float(self.env.config['gt_ramp_kW_per_hour']),
                float(self.env.config['solver_time_limit_sec']), self.env.config,
                execution_contract=self.env.execution_contract,
                joint_options=joint['options'], risk_loads=joint.get('risk_loads_kW'),
                coordination_options=joint['coordination'], readiness_context=context)
        except Exception as exc:
            self._record_error(exc, loads, f, self.env.dt_h,
                               'state_coordinated_command_generation', request, began)
        self.solver_calls.append(dict(
            time_minute=self.time, phase='state_coordinated_command_generation',
            dt_h=self.env.dt_h, elapsed_seconds=time.perf_counter()-began,
            status='success', solver_backend_layers=copy.deepcopy(result.get('solver_backend_layers', []))))
        # This is the inherited execution scheduler, not a controller-visible ACK.
        delayed, release = self.delayed_at(self.time)
        self.sequence += 1
        diagnostic = copy.deepcopy(result['joint_diagnostic'])
        diagnostic.setdefault('planned_unserved_kW', np.asarray(result['lost']).tolist())
        contacts = np.asarray(result.get('command_disconnected', result['lost']))
        command = dict(
            id=self.sequence, generated_minute=self.time,
            arrival_minute=release if delayed else self.time,
            request=request, dispatch=copy.deepcopy(result['action']),
            disconnected=contacts.tolist(), reference_cooling_kW=float(loads[:, 2].sum()),
            weight=0., optimizer_warning=result.get('optimizer_warning', ''),
            joint_dispatch=dict(planning_interface=policy.PLANNING_INTERFACE,
                planned_current_action=copy.deepcopy(result['planned_current_action']),
                diagnostic=diagnostic, readiness_context=context, planning_is_not_execution=True))
        self.pending_commands.append(command)
        self.log('command_generated', command=command)
        return command


class OperatingHospitalIESEnv(base.OperatingHospitalIESEnv):
    def reset(self, *, scenario, seed=None):
        native_reset = isolated(base.NativeEnvironment.reset,
            dict(base.NativeEnvironment.reset.__globals__, validate_scenario=validate_scenario))
        reset = isolated(base.OperatingHospitalIESEnv.reset,
            dict(base.OperatingHospitalIESEnv.reset.__globals__,
                 EssentialOperatingCore=StateCoordinatedCore,
                 NativeEnvironment=SimpleNamespace(reset=native_reset)))
        return reset(self, scenario=scenario, seed=seed)


StateCoordinatedHospitalIESEnv = OperatingHospitalIESEnv


def _clock(value, name):
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value) or value < 0.:
        raise ValueError('Invalid completed-supply clock: '+name)
    return float(value)


def actual_supply_telemetry(env):
    """Copy the last measured mean delivery without crediting held commands."""
    now = _clock(env.core.time, 'observation')
    value = dict(schema=TELEMETRY_SCHEMA, observation_time_minute=now,
        available_time_minute=now, valid_time_minute=None,
        interval_start_minute=None, interval_end_minute=None, staleness_minutes=None,
        sample_kind='no_completed_interval', command_id=None, actual_dispatch=None,
        measurement='ideal_completed_interval_observation',
        semantics='Last completed interval mean delivery; current availability and arrived limits remain separate')
    if not env.core.segments:
        return value
    last = env.core.segments[-1]
    start = _clock(last['start_minute'], 'interval start')
    end = _clock(last['end_minute'], 'interval end')
    if end <= start or end > now+1e-9:
        raise ValueError('Actual supply meter requires a completed positive interval')
    action = last['action']
    if not isinstance(action, dict) or any(key not in action for key in ACTUAL_PORTS):
        raise ValueError('Actual supply meter requires complete recorded source/converter ports')
    # Preserve the recorded numerical values, including any signed solver
    # residue. Metering neither clips a port nor changes physical accounting.
    for key in ACTUAL_PORTS:
        data = np.asarray(action[key])
        shape = (3,) if key in ('gt_electric_kW', 'electric_chiller_cooling_kW') else ()
        if data.shape != shape or data.dtype.kind not in 'fiu' or not np.isfinite(data).all():
            raise ValueError('Invalid recorded actual supply port: '+key)
    value.update(valid_time_minute=end, interval_start_minute=start,
        interval_end_minute=end, staleness_minutes=max(0., now-end),
        sample_kind='completed_interval_average', command_id=last.get('command_id'),
        actual_dispatch={key: copy.deepcopy(action[key]) for key in ACTUAL_PORTS})
    return value


def decision_snapshot(env):
    value = copy.deepcopy(old.decision_snapshot(env))
    value['actual_supply_telemetry'] = actual_supply_telemetry(env)
    return value


class StateCoordinatedPolicyEnvironment(old.OperatingPolicyEnvironment):
    step = isolated(old.native_episode.PolicyEnvironment.step,
        dict(old.native_episode.PolicyEnvironment.step.__globals__,
             decision_snapshot=decision_snapshot, validate_action=validate_action))


def run_episode(env, scenario, controller, seed, manifest):
    # Rebind both the policy-time and exporter-time snapshot locally. No module
    # global or legacy function is modified, including their physical metadata.
    operating_run = isolated(old.run_episode,
        dict(old.run_episode.__globals__, decision_snapshot=decision_snapshot,
             OperatingPolicyEnvironment=StateCoordinatedPolicyEnvironment))
    execute = isolated(base.run_episode,
        dict(base.run_episode.__globals__, old=SimpleNamespace(run_episode=operating_run)))
    payload = execute(env, scenario, controller, seed, manifest)
    payload['study_contract_id'] = manifest.get('study_contract_id', STUDY_CONTRACT_ID)
    payload['actual_supply_telemetry_schema'] = TELEMETRY_SCHEMA
    return payload
