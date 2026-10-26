"""Opt-in causal finite-shaft return preparation on the existing transfer core.

The deadline uses only current measured shaft demand and remaining shaft work.
It is a current-power-hold estimate, not a prediction of real future exhaustion.
The physical OPEN, startup clocks and energy settlement remain inherited.
"""
from __future__ import annotations

from hospital_ies.state_transition import EPS
from tools.execution_recovery.execution import ExecutionRecoveryCore as PermissionCore


class FiniteSourceReturnMixin:
    def _finite_return_enabled(self):
        return getattr(self.env,'model_correction_config',{}).get('finite_source_return',True)

    def _current_shaft_rate_kW(self):
        rate=max(0.,float(self.diesel_rotor.mechanical_kW))
        if self.segments:
            last=self.segments[-1]
            span=float(last.get('end_minute',0.))-float(last.get('start_minute',0.))
            work=float(last.get('diesel_local_response',{}).get('mechanical_work_kWh',0.))
            if span>EPS:rate=max(rate,work*60./span)
        return rate

    def _finite_return_deadline(self):
        started=self.bridge_wait_started_minute
        if started is None:return None
        bounded=started+self.env.closeout_config['grid_bridge_wait_minutes']
        rate=self._current_shaft_rate_kW()
        if rate<=EPS:return bounded
        cfg=self.env.recovery_config
        support=max(0.,float(self.state.diesel_energy_remaining_kWh))/rate*60.
        preparation=cfg['grid_return_prepare_minutes']+cfg['grid_open_transfer_minutes']
        return min(bounded,self.time+max(0.,support-preparation))

    def _destination_return_ready(self,hardware):
        # M22 compiles current storage/station ports for inherited pure probes.
        method=lambda current:PermissionCore._grid_return_ready(self,current)
        return self._current_probe(method,hardware) if hasattr(self,'_current_probe') else method(hardware)

    def _grid_return_ready(self,hardware):
        if not self._finite_return_enabled():return super()._grid_return_ready(hardware)
        if hardware['GRID']<1.-EPS or float(self.arrived_dispatch['grid_import_kW'])<=EPS:return False
        if not self._destination_return_ready(hardware):return False
        if self.grid_bus_state=='RETURN_PREP':
            # Completion rechecks the same destination eligibility, without
            # reapplying the earlier decision to keep waiting on a live DG.
            self.log('m25_return_preparation_requalified',current_destination_proved=True,
                arrived_grid_permission=True,future_state_credited=False)
            return True
        if self._critical_preservation_probe(hardware,diesel=False):
            self.bridge_wait_started_minute=None;self.bridge_wait_fallback_reason=None
            return True
        working=(hardware['DIESEL']>EPS and self.state.diesel_status=='CONNECTED'
            and self.state.diesel_energy_remaining_kWh>EPS and not self.diesel_rotor.fuel_exhausted
            and float(self.arrived_dispatch['diesel_kW'])>EPS)
        if working:working=self._critical_preservation_probe(hardware,diesel=True)
        if self.bridge_wait_started_minute is None:self.bridge_wait_started_minute=self.time
        deadline=self._finite_return_deadline()
        age=self.time-self.bridge_wait_started_minute
        due=deadline is not None and self.time>=deadline-EPS
        if not working or due:
            reason=('current_diesel_support_unavailable' if not working else
                'bounded_wait_expired' if age>=self.env.closeout_config['grid_bridge_wait_minutes']-EPS
                else 'finite_shaft_support_deadline')
            self.bridge_wait_fallback_reason=reason
            self.log('grid_open_bridge_fallback',reason=reason,wait_age_minutes=age,
                wait_limit_minutes=self.env.closeout_config['grid_bridge_wait_minutes'],
                finite_open_gap_accepted=True,current_shaft_rate_kW=self._current_shaft_rate_kW(),
                remaining_shaft_work_kWh=float(self.state.diesel_energy_remaining_kWh),
                causal_deadline_minute=deadline,prepare_plus_open_minutes=sum(self.env.recovery_config[k]
                    for k in ('grid_return_prepare_minutes','grid_open_transfer_minutes')),
                estimate='max(current_rotor_shaft,last_actual_interval_mean_shaft)_power_hold',
                future_exhaustion_or_load_read=False)
            return True
        self.log('grid_open_bridge_wait',reason='current_DG_support_before_causal_deadline',
            wait_started_minute=self.bridge_wait_started_minute,deadline_minute=deadline,
            current_shaft_rate_kW=self._current_shaft_rate_kW(),
            remaining_shaft_work_kWh=float(self.state.diesel_energy_remaining_kWh),pending_GT_is_supply=False)
        return False

    def _next_boundary(self,end):
        candidate=super()._next_boundary(end)
        if self._finite_return_enabled() and self.grid_bus_state=='ISLAND' and self.bridge_wait_started_minute is not None:
            hardware=self._hardware_data_at(self.time)[-1]
            if hardware['GRID']>=1.-EPS and float(self.arrived_dispatch['grid_import_kW'])>EPS:
                deadline=self._finite_return_deadline()
                if deadline is not None and deadline>self.time+EPS:candidate=min(candidate,deadline)
        return candidate
