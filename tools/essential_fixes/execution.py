"""V16 boundary floor regulation and an ordinary diesel depletion event.

The correction uses the same minute-scale boundary setpoint abstraction as an
ordinary arrived command. It is bounded by that timestamp's original ramp
anchor/window, and a physical feasibility probe can only reduce existing
import/converter delivery. It is not a simulated seconds-scale ramp.
"""
from __future__ import annotations

from dataclasses import asdict
import copy
import time as clock
import numpy as np

from hospital_ies.execution import ModelConstraintInfeasible
from hospital_ies.model import gt_level_properties
from hospital_ies.state_transition import clean, event_window, EPS
from hospital_ies.resilience import update_thermal, p0_failure_classification
from tools.operating_response.execution import LocalResponseMixin, LocalResponseDomainError
from tools.operating_response.physical_balance import solve_physical
from .diesel import DieselRotor

MODEL_ID = 'M16_essential'
DYNAMICS_CONTRACT_ID = 'hospital_ies.essential_engineering.v16.v1'


class EssentialResponseMixin(LocalResponseMixin):
    def __init__(self, env, initial_action):
        self.diesel_rotor = DieselRotor(rated_kW=env.plant.emergency_diesel_kW,
                                        config=env.local_response_config)
        # The v15 initializer creates its own rotor; preserve the bounded
        # execution inheritance but initialize the explicitly versioned rotor.
        super(LocalResponseMixin, self).__init__(env, initial_action)
        self.log('local_diesel_model_initialized', model_id=MODEL_ID,
                 port_semantics='arrived enable/ceiling; Pe differs from Pm',
                 rotor=self.diesel_rotor.as_dict(),
                 fuel_semantics='shaft-work-equivalent kWh; common initial budget; not liters',
                 fuel_exhaustion='cut shaft input, disconnect, retain rotor energy as coast losses')

    def observe(self):
        value = super().observe()
        value['diesel_local_response']['model_id'] = MODEL_ID
        value['gt_floor_local_correction'] = dict(
            **self.env.gt_floor_correction_config,
            abstraction='shared ordinary boundary ramp window; exact held-port feasibility')
        return value

    def diesel_available(self, t, f):
        return not self.diesel_rotor.fuel_exhausted and super().diesel_available(t, f)

    def _update_support_states(self, f):
        if self.state.diesel_energy_remaining_kWh <= EPS and not self.diesel_rotor.fuel_exhausted:
            self._exhaust_diesel({'boundary_observation': True})
        if self.diesel_rotor.fuel_exhausted:
            # No refuelling process is part of this execution model. A stale
            # command or an artificial later budget increase cannot restart it.
            self.state.diesel_energy_remaining_kWh = 0.
        super()._update_support_states(f)
        if self.diesel_rotor.fuel_exhausted:
            self.executed['diesel_kW'] = 0.
            self.state.last_diesel_kW = 0.

    def _correct_gt_floor(self, loads, f, row):
        if hasattr(self.env,'closure_config'):
            return self._correct_gt_floor_subsets(loads,f,row)
        if not self.env.gt_floor_correction_config['enabled']:
            return False
        floor = .4*float(gt_level_properties(self.env.plant, float(row.outdoor_temperature_C))[0][-1])
        before = copy.deepcopy(self.executed)
        powers = np.asarray(before['gt_electric_kW'], float)
        floors = floor*np.array([f[f'GT_{i+1}'] for i in range(3)])
        indices = [i for i in range(3) if EPS < powers[i] < floors[i]-1e-4
                   and f[f'GT_{i+1}'] > EPS
                   and f'GT_{i+1}' not in self.interlock_latched
                   and f'GT_{i+1}' not in self.state.extra_faulted
                   and i not in self.gt_start_pending]
        if not indices:
            return False
        request = copy.deepcopy(before)
        for i in indices:
            request['gt_electric_kW'][i] = float(floors[i])
        detail = dict(devices=[f'GT_{i+1}' for i in indices], before=before,
                      requested_floor_kW=floors.tolist(), boundary_execution_abstraction=True,
                      no_new_command_permission=True, no_boiler_command_increase=True)
        # Do not disguise the still-unresolved joint mechanical path as a
        # static diesel capability. This branch remains an explicit negative.
        if self.diesel_available(self.time, f) and before['diesel_kW'] > EPS:
            self.log('gt_floor_local_correction_rejected',
                     reason='diesel_coupled_boundary_requires_resolved_path', **detail)
            return False
        # _clamp may update its ramp ledger and startup dictionary. A rejected
        # speculative candidate must not consume budget, clocks or event IDs.
        trial = copy.copy(self)
        trial.events = []
        trial.gt_start_pending = copy.deepcopy(self.gt_start_pending)
        trial.interlock_latched = copy.deepcopy(self.interlock_latched)
        trial.ramp_anchor = None if self.ramp_anchor is None else self.ramp_anchor.copy()
        candidate = trial._clamp(request, f, normal_ramp=True)
        if any(candidate['gt_electric_kW'][i] < floors[i]-1e-4 for i in indices):
            self.log('gt_floor_local_correction_rejected',
                     reason='shared_ramp_window_cannot_reach_floor', candidate_dispatch=candidate,
                     ramp_time=self.ramp_time, ramp_anchor=self.ramp_anchor,
                     ramp_budget_kW=self.ramp_budget, **detail)
            return False
        began = clock.perf_counter()
        dt_h = self.env.dt_h
        try:
            proof = solve_physical(row, self.env.plant, loads, f, self.state, dt_h,
                float(self.env.scenario.get('grid_limit_kW', self.env.config['grid_import_limit_kW'])),
                self.diesel_available(self.time, f), float(self.env.config['gt_ramp_kW_per_hour']),
                float(self.env.config['solver_time_limit_sec']), self.env.config,
                execution_action=candidate, execution_contract=self.env.operating_execution_contract)
        except ModelConstraintInfeasible as exc:
            self.solver_calls.append(dict(time_minute=self.time, phase='gt_floor_correction_probe',
                dt_h=dt_h, status='candidate_infeasible', elapsed_seconds=clock.perf_counter()-began,
                exception_type=type(exc).__name__, message=str(exc)))
            self.log('gt_floor_local_correction_rejected', reason='held_ports_cannot_balance_candidate',
                     candidate_dispatch=candidate, error_type=type(exc).__name__, message=str(exc), **detail)
            return False
        except Exception as exc:
            self._record_error(exc, loads, f, dt_h, 'gt_floor_correction_probe', request, began)
        self.solver_calls.append(dict(time_minute=self.time, phase='gt_floor_correction_probe',
            dt_h=dt_h, status='success', elapsed_seconds=clock.perf_counter()-began,
            solver_backend_layers=copy.deepcopy(proof.get('solver_backend_layers', []))))
        self.ramp_time = trial.ramp_time
        self.ramp_anchor = trial.ramp_anchor
        self.ramp_budget = trial.ramp_budget
        self.executed = candidate
        self._transition_reason = 'local_floor_boundary_regulation'
        self._commit_sources(candidate)
        self.log('gt_floor_local_correction', after=candidate,
            feasible_actual_action=proof['action'], energy_balance_residuals=proof['energy_balance_residuals'],
            steam_balance_residual_lbph=proof['steam_balance_residual_lbph'],
            probe_unserved_kW=proof['lost'], ramp_time=self.ramp_time,
            ramp_anchor_kW=self.ramp_anchor, ramp_budget_kW=self.ramp_budget,
            continuous_transient_simulated=False, **detail)
        return True

    def _correct_gt_floor_subsets(self,loads,f,row):
        """Transactional M22 held-port subsets under the existing floor trip.

        Surviving units use the original ramp anchor/window. Units already
        below their legal floor may be isolated by the existing instantaneous
        interlock, with no new start or converter permission. Eligible ordinary
        dwell clocks are retained; no failed probe commits their state.
        """
        from itertools import combinations
        if not self.env.gt_floor_correction_config['enabled']:return False
        floor=.4*float(gt_level_properties(self.env.plant,float(row.outdoor_temperature_C))[0][-1])
        before=copy.deepcopy(self.executed);powers=np.asarray(before['gt_electric_kW'],float)
        floors=floor*np.array([f[f'GT_{i+1}'] for i in range(3)])
        indices=[i for i in range(3) if EPS<powers[i]<floors[i]-1e-4 and f[f'GT_{i+1}']>EPS
            and f'GT_{i+1}' not in self.interlock_latched and f'GT_{i+1}' not in self.state.extra_faulted and i not in self.gt_start_pending]
        if not indices:return False
        if self.diesel_available(self.time,f) and before['diesel_kW']>EPS:
            self.log('gt_floor_local_correction_rejected',reason='diesel_coupled_boundary_requires_resolved_path',before=before)
            return False
        feasible=[];attempts=[]
        for count in range(len(indices),0,-1):
            for keep in combinations(indices,count):
                dropped=[i for i in indices if i not in keep]
                if any(not self.gt_clocks.can_stop(i,self.time) for i in dropped):continue
                request=copy.deepcopy(before)
                for i in keep:request['gt_electric_kW'][i]=float(floors[i])
                trial=copy.copy(self);trial.events=[];trial.gt_start_pending=copy.deepcopy(self.gt_start_pending)
                trial.interlock_latched=copy.deepcopy(self.interlock_latched);trial.ramp_anchor=None if self.ramp_anchor is None else self.ramp_anchor.copy()
                candidate=trial._clamp(request,f,normal_ramp=True)
                if any(candidate['gt_electric_kW'][i]<floors[i]-1e-4 for i in keep):
                    attempts.append(dict(retained_indices=list(keep),reason='shared_ramp_window_cannot_reach_floor'));continue
                for i in dropped:candidate['gt_electric_kW'][i]=0.
                began=clock.perf_counter();dt_h=self.env.dt_h
                try:
                    proof=solve_physical(row,self.env.plant,loads,f,self.state,dt_h,
                        float(self.env.scenario.get('grid_limit_kW',self.env.config['grid_import_limit_kW'])),
                        self.diesel_available(self.time,f),float(self.env.config['gt_ramp_kW_per_hour']),float(self.env.config['solver_time_limit_sec']),
                        self._physical_solver_config(dt_h,f),execution_action=candidate,execution_contract=self.env.operating_execution_contract)
                except ModelConstraintInfeasible as exc:
                    attempts.append(dict(retained_indices=list(keep),reason='held_ports_cannot_balance_candidate',message=str(exc)))
                    self.solver_calls.append(dict(time_minute=self.time,phase='gt_floor_subset_probe',status='candidate_infeasible',message=str(exc),elapsed_seconds=clock.perf_counter()-began));continue
                except Exception as exc:self._record_error(exc,loads,f,dt_h,'gt_floor_subset_probe',request,began)
                self.solver_calls.append(dict(time_minute=self.time,phase='gt_floor_subset_probe',status='success',elapsed_seconds=clock.perf_counter()-began,solver_backend_layers=copy.deepcopy(proof.get('solver_backend_layers',[]))))
                attempts.append(dict(retained_indices=list(keep),reason='feasible',unserved_kW=proof['lost']))
                score=tuple(round(float((np.asarray(proof['lost'])[g]/np.maximum(1.,loads.sum(axis=0))).sum()),8) for g in range(4))+(len(dropped),proof['cost_USD_step'])
                feasible.append((score,candidate,proof,trial,dropped))
        if not feasible:
            self.log('gt_floor_local_correction_rejected',reason='no_registered_held_subset_feasible',subset_attempts=attempts,before=before)
            return False
        _,candidate,proof,trial,dropped=min(feasible,key=lambda item:item[0])
        for i in dropped:self._latch_gt(i,f,'minimum_output_interlock',actual_kW=float(powers[i]),minimum_kW=float(floors[i]),subset_isolation=True)
        self.ramp_time=trial.ramp_time;self.ramp_anchor=trial.ramp_anchor;self.ramp_budget=trial.ramp_budget
        self.executed=candidate;self._transition_reason='local_floor_boundary_regulation';self._commit_sources(candidate)
        self.log('gt_floor_local_correction',before=before,after=candidate,feasible_actual_action=proof['action'],
            subset_attempts=attempts,dropped_indices=dropped,no_new_command_permission=True,no_boiler_command_increase=True,
            energy_balance_residuals=proof['energy_balance_residuals'],steam_balance_residual_lbph=proof['steam_balance_residual_lbph'],
            boundary_execution_abstraction=True,continuous_transient_simulated=False)
        return True

    def _surplus_interlock(self, loads, f, row):
        self._correct_gt_floor(loads, f, row)
        super()._surplus_interlock(loads, f, row)

    def _exhaust_diesel(self, receipt):
        if self.diesel_rotor.fuel_exhausted:
            return
        remaining = float(self.state.diesel_energy_remaining_kWh)
        # Bisection's safe prefix lies below the crossing. Do not invent shaft
        # work to close its tiny numerical remainder; account for it explicitly.
        if remaining > 1e-9:
            raise RuntimeError('Diesel exhaustion boundary has unresolved positive budget')
        shutdown = self.diesel_rotor.exhaust_fuel()
        self.state.diesel_energy_remaining_kWh = 0.
        before_status = self.state.diesel_status
        self.state.diesel_status = 'EMPTY'
        self.state.diesel_start_minute = None
        self.executed['diesel_kW'] = 0.
        self.state.last_diesel_kW = 0.
        self.log('diesel_fuel_exhausted', budget_semantics='shaft_work_equivalent_kWh',
            budget_roundoff_remainder_kWh=remaining, remaining_budget_kWh=0.,
            last_valid_interval_receipt=receipt, shutdown_receipt=shutdown,
            ordinary_commands_continue=True, rotor_energy_retained=True,
            refuelling_modeled=False)
        self.log('diesel_state_changed', before=before_status, after='EMPTY',
                 mode=self.env.execution_contract['diesel_mode'],
                 allowed_priorities=self.env.execution_contract['diesel_allowed_priorities'])


    def _limit_by_diesel(self, seconds, electrical_kW, connected):
        rotor, receipt = self.preview_diesel(seconds, electrical_kW, connected=connected)
        safe = float(receipt['actual_seconds'])
        fuel = float(self.state.diesel_energy_remaining_kWh)
        if float(receipt['mechanical_work_kWh']) > fuel:
            lo, hi = 0., safe
            for _ in range(60):
                mid = (lo+hi)/2.
                _, probe = self.preview_diesel(mid, electrical_kW, connected=connected)
                if float(probe['mechanical_work_kWh']) <= fuel:
                    lo = mid
                else:
                    hi = mid
            safe = lo
            rotor, receipt = self.preview_diesel(safe, electrical_kW, connected=connected)
            receipt['fuel_budget_exhausted'] = True
        elif fuel > 0. and fuel-float(receipt['mechanical_work_kWh']) <= 1e-12:
            receipt['fuel_budget_exhausted'] = True
        return safe, rotor, receipt

    # Bounded fork of M15 advance: only fuel exhaustion becomes a device event.
    def advance(self,end):
        e=self.env;start_segment=len(self.segments)
        while self.time<end-EPS:
            self.boundary()
            if getattr(e,'hrsg_thermal_config',{}).get('warmup_delay_minutes',0.)>0.:
                thermal=self.hrsg_thermal_state(self.time)
                key=[(d['GT_running'],d['last_actual_on_minute'],d['effective_availability'],d['reason']) for d in thermal['devices']]
                if key!=getattr(self,'_last_hrsg_thermal_key',None):
                    self.log('hrsg_thermal_readiness_changed',research_model_sensitivity=True,
                        exogenous_fault=False,true_protection=False,thermal=thermal)
                    self._last_hrsg_thermal_key=key
            h,row,loads,f=self.data_at(self.time)
            self.state.disconnected_loads=np.minimum(loads,self.state.disconnected_loads)
            connected=np.maximum(0.,loads-self.state.disconnected_loads)
            self._surplus_interlock(connected,f,row)
            next_time=min(end,self._next_boundary(end))
            pre_state=clean(asdict(self.state))
            cmd=self.active_command or {}
            interval_action,source_start,source_end=self._local_interval_action(next_time,f,row)
            fixed_args=dict(execution_action=interval_action,tracking_targets=cmd.get('request',{}).get('targets',{}),
                            security_gt_penalty_USD_per_kWh=cmd.get('weight',0.))
            sol=self._solve(connected,f,(next_time-self.time)/60,'fixed_execution',cmd,**fixed_args)
            self._automatic_shed(loads,sol)
            changed=np.maximum(0.,loads-self.state.disconnected_loads)
            if not np.array_equal(changed,connected):
                connected=changed
                sol=self._solve(connected,f,(next_time-self.time)/60,'after_actual_shedding',cmd,**fixed_args)
            self._request_local_down(sol)
            revised=min(next_time,self._next_boundary(end))
            if revised<next_time-EPS:
                next_time=revised
                interval_action,source_start,source_end=self._local_interval_action(next_time,f,row)
                fixed_args['execution_action']=interval_action
                sol=self._solve(connected,f,(next_time-self.time)/60,'local_event_split',cmd,**fixed_args)
            try:self.validate_electrical_path(source_start,source_end,self.diesel_available(self.time,f),interval_action['diesel_kW'])
            except LocalResponseDomainError as exc:
                self._record_error(exc,connected,f,0.,'diesel_terminal_path_domain',cmd,clock.perf_counter())
            # Trial only: physical/protection probes must not advance mechanics.
            local_domain_boundary=None
            for local_attempt in range(8):
                span=(next_time-self.time)*60.
                safe,_,local_receipt=self._limit_by_diesel(span,sol['action']['diesel_kW'],
                    self.diesel_available(self.time,f))
                if local_receipt['domain_exceeded'] or local_receipt.get('fuel_budget_exhausted'):
                    boundary=dict(minute=self.time+safe/60.,
                        domain_exceeded=bool(local_receipt['domain_exceeded']),
                        fuel_budget_exhausted=bool(local_receipt.get('fuel_budget_exhausted')))
                    # A short-span re-solve can round the same valid endpoint
                    # to the identical clock value. Preserve both observed
                    # causes at that exact timestamp. A distinct earlier fuel
                    # boundary replaces the later domain event as before.
                    if (local_domain_boundary is not None
                        and local_domain_boundary['minute']==boundary['minute']):
                        boundary['domain_exceeded'] |= local_domain_boundary['domain_exceeded']
                        boundary['fuel_budget_exhausted'] |= local_domain_boundary['fuel_budget_exhausted']
                    local_domain_boundary=boundary
                if safe>=span-1e-7:break
                if (safe<=1e-8 and local_receipt.get('fuel_budget_exhausted')
                    and not local_receipt['domain_exceeded']
                    and self.state.diesel_energy_remaining_kWh<=1e-9):
                    self._exhaust_diesel(local_receipt)
                    self.advance(end)
                    return self.segments[start_segment:]
                # A positive coincident fuel/speed prefix still has real work
                # to settle. Commit it, then retain the domain unknown below.
                # A zero-duration invalid state cannot be relabeled EMPTY.
                if safe<=1e-8 and (safe<=0.
                                   or not local_receipt.get('fuel_budget_exhausted')):
                    self.log('local_diesel_domain_exceeded',receipt=local_receipt)
                    self._record_error(LocalResponseDomainError('No positive valid local diesel interval'),
                        connected,f,0.,'diesel_local_dynamic_domain',cmd,clock.perf_counter())
                next_time=self.time+safe/60.
                interval_action,source_start,source_end=self._local_interval_action(next_time,f,row)
                fixed_args['execution_action']=interval_action
                sol=self._solve(connected,f,(next_time-self.time)/60,'diesel_domain_split',cmd,**fixed_args)
            else:raise RuntimeError('Finite local diesel split did not converge')
            # Actual converter delivery may be limited by fuel/steam/capacity.
            # Fixed electrical source constraints prevent a new dispatch.
            self._commit_sources(sol['action'],actual=True)
            self.state.auxiliary_request_kW=self.aux(sol['action'])
            enabled=e.scenario.get('enable_propagation',False)
            event_start,_=event_window(e.scenario,'fault' if e.scenario['stage'] in (1,3) else 'shock')
            stress={};debug={};new=[]
            if enabled and self.time>=event_start-EPS:
                crossing=self._ramp_threshold_boundary(connected,f,row,next_time,cmd,source_start,source_end)
                if crossing<next_time-1e-8:
                    next_time=crossing
                    interval_action,source_start,source_end=self._local_interval_action(next_time,f,row)
                    fixed_args['execution_action']=interval_action
                    sol=self._solve(connected,f,(next_time-self.time)/60,'protection_threshold_split',cmd,**fixed_args)
                    self._commit_sources(sol['action'],actual=True);self.state.auxiliary_request_kW=self.aux(sol['action'])
                # Zero-duration inspection cannot consume protection time.
                _,stress,debug=self._protection(connected,0.)
                threshold=float(e.config['gt_protection_trip_multiple'])
                hold=float(e.config['gt_overload_hold_minutes'])
                remaining=[max(0.,hold-self.state.overload_minutes[int(dev[-1])-1])
                    for dev,ratio in stress.items() if ratio>threshold+1e-9]
                if remaining and self.time+min(remaining)<next_time-EPS:
                    next_time=self.time+min(remaining)
                    if next_time<=self.time+EPS:raise RuntimeError('Uncommitted protection event at boundary')
                    interval_action,source_start,source_end=self._local_interval_action(next_time,f,row)
                    fixed_args['execution_action']=interval_action
                    sol=self._solve(connected,f,(next_time-self.time)/60,'fixed_execution',cmd,**fixed_args)
                    self._commit_sources(sol['action'],actual=True)
                    self.state.auxiliary_request_kW=self.aux(sol['action'])
                new,stress,debug=self._protection(connected,next_time-self.time)
            dt=(next_time-self.time)/60
            terminal=None
            if np.any(source_start-source_end>EPS):
                endpoint=copy.deepcopy(self.executed);endpoint['gt_electric_kW']=source_end.tolist()
                terminal=self._solve(connected,f,dt,'local_down_endpoint',cmd,execution_action=endpoint,
                    tracking_targets=cmd.get('request',{}).get('targets',{}),security_gt_penalty_USD_per_kWh=cmd.get('weight',0.))
            lost=sol['lost']+self.state.disconnected_loads
            # Commit only the final, event-split actual interval. Original
            # electrical service/cost definitions are retained separately.
            safe,rotor,local_receipt=self._limit_by_diesel(dt*3600.,sol['action']['diesel_kW'],
                self.diesel_available(self.time,f))
            if safe<dt*3600.-1e-6:
                raise RuntimeError('Uncommitted local dynamic interval after protection split')
            # Re-previewing exactly the valid prefix may no longer report the
            # attempted crossing. Preserve its cause only if the final physical
            # interval actually reaches it (a prior protection split does not).
            if local_domain_boundary is not None and abs(next_time-local_domain_boundary['minute'])<=1e-8:
                local_receipt['domain_exceeded']|=local_domain_boundary['domain_exceeded']
                if local_domain_boundary['fuel_budget_exhausted']:
                    local_receipt['fuel_budget_exhausted']=True
            self.diesel_rotor=rotor
            debit=float(local_receipt['mechanical_work_kWh'])
            self.state.diesel_energy_remaining_kWh=max(0.,self.state.diesel_energy_remaining_kWh-debit)
            local_receipt['fuel_budget_debit_kWh_equivalent']=debit
            local_receipt['arrived_ceiling_kW']=float(interval_action['diesel_kW'])
            local_receipt['actual_terminal_kW']=float(sol['action']['diesel_kW'])
            local_receipt['original_electric_fee_USD']=.30*float(local_receipt['electrical_work_kWh'])
            local_receipt['shaft_equivalent_fee_USD']=.30*debit
            params,byhour=e.thermal_cache
            temp,viol=update_thermal(self.state,params,byhour[h],lost,loads,dt,float(e.config['thermal_setpoint_margin_C']),attribution=self.thermal_attribution(self.time))
            cls=p0_failure_classification(lost,loads,viol['P0'],e.config,dt,e.cumulative_p0)
            e.cumulative_p0=cls['cumulative_effective_energy_kWh']
            weight=self.active_command['weight'] if self.active_command else 0.
            seg=dict(start_minute=self.time,end_minute=next_time,step=e.steps_done,hour_of_year=h,
                command_id=self.active_command['id'] if self.active_command else None,
                execution_mode='finite_local_down' if np.any(source_start-source_end>EPS) else 'held_executed_setpoints',availability=f,pre_state=pre_state,
                source_start_gt_kW=source_start,source_end_gt_kW=source_end,
                arrived_source_setpoints=copy.deepcopy(self.executed),execution_contract=e.execution_contract['name'],
                auxiliary_electric_kW=self.aux(sol['action']),thermal_attribution=self.thermal_attribution(self.time),
                loads_kW=loads,connected_loads_kW=connected,disconnected_loads_kW=self.state.disconnected_loads.copy(),
                unserved_kW=lost,action=sol['action'],cost_USD_step=sol['cost_USD_step'],
                tracking_penalty_USD_step=sol['tracking_penalty_USD_step'],
                security_objective_penalty_USD_step=weight*sum(sol['action']['gt_electric_kW'])*dt,
                energy_balance_residuals=sol['energy_balance_residuals'],steam_balance_residual_lbph=sol['steam_balance_residual_lbph'],
                protection_stress=stress,protection_debug=debug,post_state=clean(asdict(self.state)),
                temperature=temp,violation=viol,classification=cls,optimizer_warning=sol.get('optimizer_warning',''))
            seg['diesel_local_response']=clean(local_receipt)
            self.segments.append(clean(seg))
            self.executed['gt_electric_kW']=source_end.tolist()
            self.state.gt_prev=source_end.copy()
            if terminal is not None:
                self._commit_sources(terminal['action'],actual=True)
                self.state.auxiliary_request_kW=self.aux(terminal['action'])
                self.segments[-1]['terminal_action']=clean(terminal['action'])
            if self.local_down is not None and np.max(np.abs(source_end-np.array(self.local_down['target'])))<=1e-6:
                self.events.append(clean(dict(event_id=len(self.events)+1,kind='local_down_completed',
                    time_minute=next_time,target=self.local_down['target'],completed_minute=next_time)))
                self.local_down=None
            self.segments[-1]['post_state']=clean(asdict(self.state))
            self.time=next_time
            if hasattr(self,'_after_actual_segment'):
                self._after_actual_segment(self.segments[-1],sol)
            if local_receipt.get('fuel_budget_exhausted'):
                self._exhaust_diesel(local_receipt)
                self.segments[-1]['post_state']=clean(asdict(self.state))
            if local_receipt['domain_exceeded']:
                self.log('local_diesel_domain_exceeded',receipt=local_receipt,
                    reason='speed validity domain; not an ordinary depletion event')
                self._record_error(LocalResponseDomainError('Finite diesel response left registered validity domain'),
                    connected,f,0.,'diesel_local_dynamic_domain',cmd,clock.perf_counter())
            for device in new:
                if device in self.state.extra_faulted:continue
                self.state.extra_faulted.add(device)
                ev=dict(device=device,start_minute=self.time-float(e.config['gt_overload_hold_minutes']),
                        trip_time_minute=self.time,step=e.steps_done,stress_ratio=stress[device],
                        hold_minutes=float(e.config['gt_overload_hold_minutes']),
                        protection_reference_kW=float(e.plant.gt_rated_kW),
                        trigger='continuous_residual_stress_surrogate',initiating_faults=copy.deepcopy(e.scenario.get('faults',{})))
                e.trip_events.append(ev);self.log('protection_trip',**ev)
            # Next boundary applies and logs forced setpoint changes. Do not
            # mutate output silently here before the boundary handler sees it.
        self.boundary()
        return self.segments[start_segment:]
