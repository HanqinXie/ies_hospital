"""M19 bounded current-state return coordination on the unchanged M18 core."""
from __future__ import annotations
import copy
import math
import time
import numpy as np
from hospital_ies.execution import ModelConstraintInfeasible
from hospital_ies.state_transition import EPS
from tools.operating_response.physical_balance import solve_physical
from tools.execution_recovery.execution import (ExecutionRecoveryCore as PreviousCore,
    validate_recovery, DEFAULT_RECOVERY, LIMITS_SCHEMA)

MODEL_ID='M19_model_closeout'
DYNAMICS_CONTRACT_ID='hospital_ies.model_closeout.v19.v1'
DEFAULT_CLOSEOUT=dict(grid_bridge_wait_minutes=30.)

def validate_closeout(value=None):
    if value is not None and (not isinstance(value,dict) or set(value)-set(DEFAULT_CLOSEOUT)):
        raise ValueError('Unknown M19 closeout configuration')
    out=dict(DEFAULT_CLOSEOUT,**copy.deepcopy(value or {}))
    for key,val in out.items():
        if isinstance(val,bool) or not isinstance(val,(int,float)) or not math.isfinite(val) or val<0.:
            raise ValueError('Invalid finite closeout clock: '+key)
        out[key]=float(val)
    return out

class ExecutionRecoveryCore(PreviousCore):
    def __init__(self,env,initial_action):
        self.bridge_wait_started_minute=None
        self.bridge_wait_fallback_reason=None
        env.closeout_config=validate_closeout(getattr(env,'closeout_config',{}))
        super().__init__(env,initial_action)

    def _critical_preservation_probe(self,hardware,*,diesel):
        _,row,total,_=self._hardware_data_at(self.time)
        connected=np.maximum(0.,total-self.state.disconnected_loads)
        candidate=copy.deepcopy(self.executed)
        candidate['grid_import_kW']=0.
        candidate['diesel_kW']=float(self.arrived_dispatch['diesel_kW']) if diesel else 0.
        availability=dict(hardware,GRID=0.)
        began=time.perf_counter()
        try:
            proof=solve_physical(row,self.env.plant,connected,availability,self.state,self.env.dt_h,
                float(self.env.scenario.get('grid_limit_kW',self.env.config['grid_import_limit_kW'])),
                bool(diesel),float(self.env.config['gt_ramp_kW_per_hour']),
                float(self.env.config['solver_time_limit_sec']),self.env.config,
                execution_action=candidate,execution_contract=self.env.operating_execution_contract)
        except ModelConstraintInfeasible as exc:
            self.solver_calls.append(dict(time_minute=self.time,phase='grid_open_bridge_probe',
                status='candidate_infeasible',elapsed_seconds=time.perf_counter()-began,message=str(exc)))
            return False
        except Exception as exc:
            self._record_error(exc,connected,availability,self.env.dt_h,
                'grid_open_bridge_probe',self.active_command,began)
        previous=total[:2].copy()
        if self.segments:
            last=self.segments[-1]
            previous=np.maximum(0.,np.asarray(last['loads_kW'])[:2]-np.asarray(last['unserved_kW'])[:2])
        allowed=np.maximum(0.,total[:2]-np.minimum(total[:2],previous))
        deficit=np.asarray(proof['lost'])[:2]+self.state.disconnected_loads[:2]
        ready=not np.any(deficit>allowed+1e-4)
        self.solver_calls.append(dict(time_minute=self.time,phase='grid_open_bridge_probe',
            diesel_enabled=bool(diesel),status='success',elapsed_seconds=time.perf_counter()-began,
            solver_backend_layers=copy.deepcopy(proof.get('solver_backend_layers',[]))))
        self.log('grid_open_bridge_probe',current_actual_GT_kW=np.asarray(self.state.gt_prev).tolist(),
            diesel_enabled=bool(diesel),preserves_measured_critical_service=bool(ready),
            candidate_critical_deficit_kW=deficit,allowed_critical_deficit_kW=allowed,
            actual_candidate_action=proof['action'],pending_GT_is_supply=False,
            energy_balance_residuals=proof['energy_balance_residuals'])
        return bool(ready)

    def _grid_return_ready(self,hardware):
        if not super()._grid_return_ready(hardware):return False
        if self._critical_preservation_probe(hardware,diesel=False):
            self.bridge_wait_started_minute=None
            self.bridge_wait_fallback_reason=None
            return True
        limit=self.env.closeout_config['grid_bridge_wait_minutes']
        age=0. if self.bridge_wait_started_minute is None else self.time-self.bridge_wait_started_minute
        working=(hardware['DIESEL']>EPS and self.state.diesel_status=='CONNECTED'
            and self.state.diesel_energy_remaining_kWh>EPS and not self.diesel_rotor.fuel_exhausted
            and float(self.arrived_dispatch['diesel_kW'])>EPS)
        if working:working=self._critical_preservation_probe(hardware,diesel=True)
        if not working or age>=limit-EPS:
            reason='bounded_wait_expired' if working else 'current_diesel_support_unavailable'
            self.bridge_wait_fallback_reason=reason
            self.log('grid_open_bridge_fallback',reason=reason,wait_age_minutes=age,
                wait_limit_minutes=limit,finite_open_gap_accepted=True)
            return True
        if self.bridge_wait_started_minute is None:self.bridge_wait_started_minute=self.time
        self.log('grid_open_bridge_wait',reason='no_current_running_GT_bridge_keep_working_DG',
            wait_started_minute=self.bridge_wait_started_minute,wait_limit_minutes=limit,
            deadline_minute=self.bridge_wait_started_minute+limit,pending_GT_is_supply=False)
        return False

    def _update_support_states(self,f):
        hardware=self._hardware_data_at(self.time)[-1]
        if hardware['GRID']<1.-EPS or float(self.arrived_dispatch['grid_import_kW'])<=EPS:
            self.bridge_wait_started_minute=None
            self.bridge_wait_fallback_reason=None
        super()._update_support_states(f)
        if self.grid_bus_state=='GRID':self.bridge_wait_started_minute=None

    def _next_boundary(self,end):
        candidate=super()._next_boundary(end)
        if self.bridge_wait_started_minute is not None:
            expiry=self.bridge_wait_started_minute+self.env.closeout_config['grid_bridge_wait_minutes']
            if expiry>self.time+EPS:candidate=min(candidate,expiry)
        return candidate

    def observe(self):
        value=super().observe()
        value['diesel_local_response']['model_id']=MODEL_ID
        value['execution_recovery']['contract_id']=DYNAMICS_CONTRACT_ID
        value['model_closeout']=dict(contract_id=DYNAMICS_CONTRACT_ID,
            grid_bridge_wait_started_minute=self.bridge_wait_started_minute,
            grid_bridge_wait_minutes=self.env.closeout_config['grid_bridge_wait_minutes'],
            grid_bridge_fallback_reason=self.bridge_wait_fallback_reason,
            critical_request_is_delivered_supply=False)
        return value

    def advance(self,end):
        first=len(self.segments)
        try:return super().advance(end)
        finally:
            for segment in self.segments[first:]:
                segment['execution_model_id']=MODEL_ID
                demand=np.asarray(segment['loads_kW'],float)
                unmet=np.asarray(segment['unserved_kW'],float)
                # Recover the segment's actual active cut, not the final state.
                if 'disconnected_loads_kW' not in segment:
                    segment['service_request_ledger']=dict(status='unknown_missing_actual_cut_ledger')
                    continue
                cuts=np.asarray(segment['disconnected_loads_kW'],float)
                if cuts.shape==demand.shape:
                    segment['service_request_ledger']=dict(original_demand_kW=demand.tolist(),
                        active_cut_kW=cuts.tolist(),connected_unserved_kW=np.maximum(0.,unmet-cuts).tolist(),
                        delivered_kW=(demand-unmet).tolist(),active_cut_source='segment_disconnected_ledger')

ModelCloseoutCore=ExecutionRecoveryCore
