"""M18 persistent permissions and common causal transfer/contact execution.

The inherited M16 energy, rotor, fuel and protection loop is unchanged. Its
``executed`` mapping remains an effective actuator mapping; arrived permissions
are a separate ledger and never count as delivered energy.
"""
from __future__ import annotations

import copy
import math
import time

import numpy as np

from hospital_ies.execution import ModelConstraintInfeasible, maximum_auxiliary_sink
from hospital_ies.state_transition import EPS
from tools.operating_response.physical_balance import solve_physical
from tools.state_coordinated.environment import StateCoordinatedCore


MODEL_ID = 'M18_execution_recovery'
DYNAMICS_CONTRACT_ID = 'hospital_ies.execution_recovery.v18.v1'
LIMITS_SCHEMA = 'hospital_ies.execution_limits.v18.v2'
DEFAULT_RECOVERY = dict(grid_return_prepare_minutes=.25,
    grid_open_transfer_minutes=.25, local_contact_restore_delay_minutes=0.)
PERMISSION_PORTS = ('grid_import_kW', 'diesel_kW', 'boiler_steam_lbph',
    'absorption_cooling_kW', 'electric_chiller_cooling_kW')


def validate_recovery(value=None):
    if value is not None and (not isinstance(value, dict) or set(value)-set(DEFAULT_RECOVERY)):
        raise ValueError('Unknown M18 execution recovery configuration')
    result = dict(DEFAULT_RECOVERY, **copy.deepcopy(value or {}))
    for key, val in result.items():
        if isinstance(val, bool) or not isinstance(val, (int, float)) or not math.isfinite(val) or val < 0.:
            raise ValueError('Invalid finite recovery clock: '+key)
        result[key] = float(val)
    if result['grid_open_transfer_minutes'] <= 0.:
        raise ValueError('M18 open transfer requires a positive disconnected interval')
    return result


class ExecutionRecoveryCore(StateCoordinatedCore):
    def __init__(self, env, initial_action):
        self.arrived_dispatch = copy.deepcopy(initial_action)
        self.arrived_history = {None: copy.deepcopy(initial_action)}
        self.grid_bus_state = 'GRID'
        self.grid_return_started_minute = None
        self.grid_open_started_minute = None
        self.pending_contact_restore = None
        self.hardware_availability = None
        self.boundary_history = {}
        env.recovery_config = validate_recovery(getattr(env, 'recovery_config', {}))
        super().__init__(env, initial_action)
        self.log('persistent_permissions_initialized', arrived_dispatch=self.arrived_dispatch,
            replacement='latest arrived full command replaces; explicit zero revokes; no expiry',
            hardware_fault_cancels_permission=False, gt_restart_requires_ordinary_command=True)

    def _hardware_data_at(self, minute):
        return super().data_at(minute)

    def data_at(self, minute):
        h, row, loads, hardware = self._hardware_data_at(minute)
        effective = dict(hardware)
        # No timer is advanced by a data query, including diagnostic probes.
        if self.grid_bus_state != 'GRID':
            effective['GRID'] = 0.
        return h, row, loads, effective

    def _set_bus(self, value, **details):
        previous = self.grid_bus_state
        if value == previous:
            return
        self.grid_bus_state = value
        self.log('grid_bus_state_changed', before=previous, after=value,
            no_grid_diesel_parallel=True, **details)

    def _grid_return_ready(self, hardware):
        """Prove the destination path before opening a working DG connection.

        This is a present-state feasibility check, with no future fault clock,
        no new permissions and no new GT setpoint. The finite open gap itself
        remains unserved when held GT cannot bridge it.
        """
        _, row, total, _ = self._hardware_data_at(self.time)
        connected = np.maximum(0., total-self.state.disconnected_loads)
        candidate = copy.deepcopy(self.executed)
        candidate['grid_import_kW'] = min(float(self.arrived_dispatch['grid_import_kW']),
            float(self.env.scenario.get('grid_limit_kW', self.env.config['grid_import_limit_kW']))
            * float(hardware['GRID']))
        candidate['diesel_kW'] = 0.
        began = time.perf_counter()
        try:
            proof = solve_physical(row, self.env.plant, connected, hardware, self.state,
                self.env.dt_h, float(self.env.scenario.get('grid_limit_kW', self.env.config['grid_import_limit_kW'])),
                False, float(self.env.config['gt_ramp_kW_per_hour']),
                float(self.env.config['solver_time_limit_sec']), self.env.config,
                execution_action=candidate, execution_contract=self.env.operating_execution_contract)
        except ModelConstraintInfeasible as exc:
            self.log('grid_return_not_ready', reason='destination_held_port_balance_infeasible', message=str(exc))
            self.solver_calls.append(dict(time_minute=self.time, phase='grid_return_readiness_probe',
                status='candidate_infeasible', elapsed_seconds=time.perf_counter()-began, message=str(exc)))
            return False
        except Exception as exc:
            self._record_error(exc, connected, hardware, self.env.dt_h,
                'grid_return_readiness_probe', self.active_command, began)
        previous_served = total[:2].copy()
        if self.segments:
            last = self.segments[-1]
            previous_served = np.maximum(0., np.asarray(last['loads_kW'], float)[:2]
                - np.asarray(last['unserved_kW'], float)[:2])
        allowed = np.maximum(0., total[:2]-np.minimum(total[:2], previous_served))
        deficit = np.asarray(proof['lost'], float)[:2]+self.state.disconnected_loads[:2]
        ready = not np.any(deficit > allowed+1e-4)
        self.solver_calls.append(dict(time_minute=self.time, phase='grid_return_readiness_probe',
            status='success', elapsed_seconds=time.perf_counter()-began,
            solver_backend_layers=copy.deepcopy(proof.get('solver_backend_layers', []))))
        self.log('grid_return_readiness_proved' if ready else 'grid_return_not_ready',
            reason='current_destination_path_preserves_measured_critical_service' if ready
                else 'destination_path_would_worsen_measured_critical_service',
            candidate_grid_cap_kW=candidate['grid_import_kW'], candidate_critical_deficit_kW=deficit,
            allowed_critical_deficit_kW=allowed, actual_candidate_action=proof['action'],
            open_gap_is_not_bridged_by_permission=True, energy_balance_residuals=proof['energy_balance_residuals'])
        return ready

    def _update_support_states(self, f):
        hardware = self._hardware_data_at(self.time)[-1]
        self.hardware_availability = dict(hardware)
        cfg = self.env.recovery_config
        healthy = hardware['GRID'] >= 1.-EPS
        permit = float(self.arrived_dispatch['grid_import_kW']) > EPS
        if not healthy:
            self.grid_return_started_minute = self.grid_open_started_minute = None
            self._set_bus('ISLAND', reason='observed_grid_hardware_unavailable')
        elif self.grid_bus_state in ('ISLAND', 'RETURN_PREP'):
            if not permit:
                self.grid_return_started_minute = None
                self._set_bus('ISLAND', reason='no_arrived_grid_permission')
            elif self.grid_bus_state == 'ISLAND' and self._grid_return_ready(hardware):
                self.grid_return_started_minute = self.time
                self._set_bus('RETURN_PREP', reason='healthy_grid_and_arrived_permission',
                    ready_minute=self.time+cfg['grid_return_prepare_minutes'])
            if (self.grid_bus_state == 'RETURN_PREP'
                and self.time >= self.grid_return_started_minute+cfg['grid_return_prepare_minutes']-EPS):
                if self._grid_return_ready(hardware):
                    self.grid_open_started_minute = self.time
                    self._set_bus('TRANSFER_OPEN', reason='prepared_open_transition',
                        grid_close_minute=self.time+cfg['grid_open_transfer_minutes'])
                else:
                    self.grid_return_started_minute = None
                    self._set_bus('ISLAND', reason='destination_readiness_lost_before_opening')
        if self.grid_bus_state == 'TRANSFER_OPEN':
            if not permit:
                # A command revocation cannot close an unpermitted grid path.
                self.grid_return_started_minute = self.grid_open_started_minute = None
                self._set_bus('ISLAND', reason='arrived_grid_permission_revoked')
            elif self.time >= self.grid_open_started_minute+cfg['grid_open_transfer_minutes']-EPS:
                self._set_bus('GRID', reason='finite_open_transfer_completed')
                self.grid_return_started_minute = self.grid_open_started_minute = None
        f['GRID'] = float(hardware['GRID']) if self.grid_bus_state == 'GRID' else 0.

        s = self.state
        if s.diesel_energy_remaining_kWh <= EPS and not self.diesel_rotor.fuel_exhausted:
            self._exhaust_diesel({'boundary_observation': True})
        before = s.diesel_status
        s.topology_mode = self.env.execution_contract['diesel_mode']
        if self.diesel_rotor.fuel_exhausted or s.diesel_energy_remaining_kWh <= EPS:
            s.diesel_energy_remaining_kWh = 0.
            status = 'EMPTY'
        elif hardware['DIESEL'] <= EPS:
            status = 'FAILED'
        elif self.grid_bus_state == 'GRID':
            status = 'OFF'
        elif self.grid_bus_state == 'TRANSFER_OPEN':
            # The engine remains READY and consumes idle shaft work through
            # inherited completed-startup accounting; its breaker is open.
            status = 'READY'
        else:
            if s.diesel_start_minute is None:
                s.diesel_start_minute = self.time
            age = self.time-s.diesel_start_minute
            delay = float(self.env.config['diesel_start_delay_minutes'])
            status = ('STARTING' if age < delay-EPS else
                'READY' if age < delay+self.env.execution_contract['ats_transfer_minutes']-EPS
                else 'CONNECTED')
        if status in ('EMPTY', 'FAILED', 'OFF'):
            s.diesel_start_minute = None
        s.diesel_status = status
        if status != before:
            self.log('diesel_state_changed', before=before, after=status,
                mode=s.topology_mode, grid_bus_state=self.grid_bus_state,
                allowed_priorities=self.env.execution_contract['diesel_allowed_priorities'])

    def diesel_available(self, t, f):
        return bool(self.grid_bus_state in ('ISLAND', 'RETURN_PREP')
            and self.state.diesel_status == 'CONNECTED'
            and f['DIESEL'] > EPS and self.state.diesel_energy_remaining_kWh > EPS
            and not self.diesel_rotor.fuel_exhausted)

    def _clamp(self, dispatch, f, normal_ramp=False):
        request = copy.deepcopy(dispatch)
        for key in PERMISSION_PORTS:
            if key in self.arrived_dispatch:
                request[key] = copy.deepcopy(self.arrived_dispatch[key])
        result = super()._clamp(request, f, normal_ramp=normal_ramp)
        if self.grid_bus_state != 'GRID':
            result['grid_import_kW'] = 0.
        if not self.diesel_available(self.time, f):
            result['diesel_kW'] = 0.
        return result

    def boundary(self):
        due = [c for c in self.pending_commands if c['arrival_minute'] <= self.time+EPS]
        if due:
            latest = max(due, key=lambda c: (c['generated_minute'], c['id']))
            before = copy.deepcopy(self.arrived_dispatch)
            self.arrived_dispatch = copy.deepcopy(latest['dispatch'])
            self.arrived_history[latest['id']] = copy.deepcopy(self.arrived_dispatch)
            self.log('arrived_permissions_replaced', command_id=latest['id'],
                before=before, after=self.arrived_dispatch, expiry=None,
                explicit_zero_revokes=True, activation_requires_current_physical_path=True)
        super().boundary()
        self.boundary_history[self.time] = dict(
            hardware_availability=copy.deepcopy(self._hardware_data_at(self.time)[-1]),
            execution_limits=self.execution_limits())

    def _next_boundary(self, end):
        candidates = [super()._next_boundary(end)]
        cfg = self.env.recovery_config
        if self.grid_bus_state == 'RETURN_PREP':
            candidates.append(self.grid_return_started_minute+cfg['grid_return_prepare_minutes'])
        if self.grid_bus_state == 'TRANSFER_OPEN':
            candidates.append(self.grid_open_started_minute+cfg['grid_open_transfer_minutes'])
        if self.pending_contact_restore is not None:
            candidates.append(self.pending_contact_restore['arrival_minute'])
        return min(x for x in candidates if x > self.time+EPS)

    def execution_limits(self):
        hardware = self._hardware_data_at(self.time)[-1]
        remaining = None
        cfg = self.env.recovery_config
        if self.grid_bus_state == 'RETURN_PREP':
            remaining = max(0., self.grid_return_started_minute+cfg['grid_return_prepare_minutes']-self.time)
        elif self.grid_bus_state == 'TRANSFER_OPEN':
            remaining = max(0., self.grid_open_started_minute+cfg['grid_open_transfer_minutes']-self.time)
        return dict(schema=LIMITS_SCHEMA, hardware_grid_available_fraction=float(hardware['GRID']),
            grid_bus_state=self.grid_bus_state,
            effective_dispatch=copy.deepcopy(self.executed),
            effective_grid_import_limit_kW=float(self.executed['grid_import_kW']),
            effective_diesel_limit_kW=float(self.executed['diesel_kW']),
            diesel_conditionally_permitted=bool(hardware['DIESEL'] > EPS
                and self.state.diesel_energy_remaining_kWh > EPS and not self.diesel_rotor.fuel_exhausted),
            grid_transfer_remaining_minutes=remaining,
            gt_protection_reference_kW=float(self.env.plant.gt_rated_kW),
            gt_trip_multiple=float(self.env.config['gt_protection_trip_multiple']),
            gt_hold_minutes=float(self.env.config['gt_overload_hold_minutes']),
            gt_overload_minutes=np.asarray(self.state.overload_minutes, float).tolist())

    def observe(self):
        value = super().observe()
        value['arrived_grid_import_limit_kW'] = float(self.arrived_dispatch['grid_import_kW'])
        value['diesel_local_response']['model_id'] = MODEL_ID
        value['diesel_local_response']['arrived_ceiling_kW'] = float(self.arrived_dispatch['diesel_kW'])
        value['execution_recovery'] = dict(contract_id=DYNAMICS_CONTRACT_ID,
            arrived_dispatch=copy.deepcopy(self.arrived_dispatch), effective_dispatch=copy.deepcopy(self.executed),
            hardware_availability=copy.deepcopy(self._hardware_data_at(self.time)[-1]),
            execution_limits=self.execution_limits())
        return value

    def readiness_context(self):
        value = super().readiness_context()
        value['arrived_dispatch'] = copy.deepcopy(self.arrived_dispatch)
        value['diesel_arrived_cap_kW'] = float(self.arrived_dispatch['diesel_kW'])
        return value

    def execution_context(self):
        value = super().execution_context()
        value['arrived_dispatch'] = copy.deepcopy(self.arrived_dispatch)
        value['arrived_grid_import_limit_kW'] = float(self.arrived_dispatch['grid_import_kW'])
        return value

    def _restore_real_contacts(self, connected, f, row):
        total = self._hardware_data_at(self.time)[2]
        cuts = self.state.disconnected_loads
        fixed_supply = float(sum(self.executed['gt_electric_kW']))
        if self.env.execution_contract['grid_import_mode'] == 'fixed':
            fixed_supply += float(self.executed['grid_import_kW'])
        if float(cuts[:, 0].sum()) <= EPS:
            self.pending_contact_restore = None
            return False
        sink = self._electric_sink_upper(connected, f, row)
        auxiliary = sink['maximum_converter_electric_kW']
        required = max(0., fixed_supply-sink['upper_kW'])
        if required <= 1e-4:
            self.pending_contact_restore = None
            return False
        available = np.minimum(cuts[:, 0], total[:, 0])
        if float(available.sum())+1e-4 < required:
            self.log('local_real_load_restore_rejected', reason='insufficient_existing_disconnected_electric_load',
                required_kW=required, available_kW=available.tolist(),electric_sink_bound=sink)
            return False
        delay = self.env.recovery_config['local_contact_restore_delay_minutes']
        if delay > 0.:
            if self.pending_contact_restore is None:
                self.pending_contact_restore = dict(arrival_minute=self.time+delay)
                self.log('local_real_load_restore_requested', **self.pending_contact_restore,
                    required_kW=required, no_energy_sink_during_delay=True)
            if self.time < self.pending_contact_restore['arrival_minute']-EPS:
                return False
        restored = np.zeros(4)
        remainder = required
        for i in range(4):
            restored[i] = min(float(available[i]), remainder)
            remainder -= restored[i]
        candidate_cuts = cuts.copy()
        candidate_cuts[:, 0] -= restored
        candidate_connected = np.maximum(0., total-candidate_cuts)
        began = time.perf_counter()
        try:
            proof = solve_physical(row, self.env.plant, candidate_connected, f, self.state,
                self.env.dt_h, float(self.env.scenario.get('grid_limit_kW', self.env.config['grid_import_limit_kW'])),
                self.diesel_available(self.time, f), float(self.env.config['gt_ramp_kW_per_hour']),
                float(self.env.config['solver_time_limit_sec']), self._physical_solver_config(self.env.dt_h,f),
                execution_action=self.executed, execution_contract=self.env.operating_execution_contract)
        except ModelConstraintInfeasible as exc:
            self.solver_calls.append(dict(time_minute=self.time, phase='real_contact_restore_probe',
                status='candidate_infeasible', elapsed_seconds=time.perf_counter()-began, message=str(exc)))
            self.log('local_real_load_restore_rejected', reason='exact_held_port_proof_infeasible', message=str(exc))
            return False
        except Exception as exc:
            self._record_error(exc, candidate_connected, f, self.env.dt_h,
                'real_contact_restore_probe', self.active_command, began)
        self.solver_calls.append(dict(time_minute=self.time, phase='real_contact_restore_probe',
            status='success', elapsed_seconds=time.perf_counter()-began,
            solver_backend_layers=copy.deepcopy(proof.get('solver_backend_layers', []))))
        if float(np.asarray(proof['lost'])[:, 0].sum()) > 1e-4:
            self.log('local_real_load_restore_rejected', reason='restored_electric_contact_not_fully_serviceable',
                candidate_restored_kW=restored.tolist(), proof_unserved_kW=proof['lost'],electric_sink_bound=sink)
            return False
        # Preserve already delivered critical heat/cold. If there is no
        # completed meter yet, accept only a fully served critical h/c proof.
        # A demand change is handled by preserving min(previous delivery,
        # present demand), rather than comparing raw ENS from unequal loads.
        previous_served = total[:2, 1:].copy()
        baseline_kind = 'no_completed_meter_require_full_current_critical_heat_cold'
        if self.segments:
            last = self.segments[-1]
            previous_served = np.maximum(0., np.asarray(last['loads_kW'], float)[:2, 1:]
                - np.asarray(last['unserved_kW'], float)[:2, 1:])
            baseline_kind = 'last_completed_actual_heat_cold_delivery_capped_by_current_demand'
        allowed_deficit = np.maximum(0., total[:2, 1:]-np.minimum(total[:2, 1:], previous_served))
        candidate_deficit = np.asarray(proof['lost'], float)[:2, 1:]+candidate_cuts[:2, 1:]
        if np.any(candidate_deficit > allowed_deficit+1e-4):
            self.log('local_real_load_restore_rejected', reason='critical_heat_cold_service_would_worsen',
                baseline_kind=baseline_kind, allowed_critical_heat_cold_deficit_kW=allowed_deficit,
                candidate_critical_heat_cold_deficit_kW=candidate_deficit)
            return False
        before = cuts.copy()
        self.state.disconnected_loads = candidate_cuts
        self.automatic_disconnected = np.minimum(self.automatic_disconnected, candidate_cuts)
        self.disconnected_fraction = np.divide(candidate_cuts, total,
            out=np.zeros_like(total), where=total > EPS)
        self.automatic_fraction = np.divide(self.automatic_disconnected, total,
            out=np.zeros_like(total), where=total > EPS)
        # The inherited loop uses this very array immediately in its solve.
        connected[:] = candidate_connected
        self.pending_contact_restore = None
        self.log('local_real_load_restored', restored_kW=restored.tolist(), before_disconnected_kW=before,
            after_disconnected_kW=candidate_cuts, priority_order=['P0', 'P1', 'P2', 'P3'],
            local_contact_delay_minutes=delay, zero_delay_aggregate_contact_assumption=delay == 0.,
            fixed_source_kW=fixed_supply, maximum_real_auxiliary_kW=auxiliary,electric_sink_bound=sink,
            proof_actual_action=proof['action'], proof_unserved_kW=proof['lost'],
            critical_heat_cold_baseline=baseline_kind,
            allowed_critical_heat_cold_deficit_kW=allowed_deficit,
            energy_balance_residuals=proof['energy_balance_residuals'],
            steam_balance_residual_lbph=proof['steam_balance_residual_lbph'])
        return True

    def _surplus_interlock(self, loads, f, row):
        self._restore_real_contacts(loads, f, row)
        super()._surplus_interlock(loads, f, row)

    def advance(self, end):
        first = len(self.segments)
        try:
            return super().advance(end)
        finally:
            # A real rotor/solver exception may follow a committed positive
            # prefix. Decorate that saved prefix before propagating the same
            # exception, without making a replacement interval or tail.
            for segment in self.segments[first:]:
                if segment['end_minute'] <= segment['start_minute']:
                    continue
                segment['effective_source_setpoints'] = copy.deepcopy(segment['arrived_source_setpoints'])
                segment['arrived_source_setpoints_semantics'] = (
                    'legacy alias of effective_source_setpoints; not persistent permissions')
                segment['arrived_source_permissions'] = copy.deepcopy(
                    self.arrived_history[segment['command_id']])
                segment['execution_model_id'] = MODEL_ID
                stamp = max(t for t in self.boundary_history if t <= segment['start_minute']+EPS)
                segment.update(copy.deepcopy(self.boundary_history[stamp]))
