"""Actual GT clocks and explicit M14 dynamics over the original energy accounting.

Minimum times/rearm are research parameters, not OEM or clinical assertions.
The original algebraic no-export action remains instantaneous: no surplus energy
is held for an invented relay delay. True residual-stress protection is retained.
"""
from __future__ import annotations
import copy
import math
import numpy as np
from hospital_ies.execution import maximum_auxiliary_sink
from hospital_ies.model import gt_level_properties

EPS=1e-9
MODEL_ID='M14_engineering'
DYNAMICS_CONTRACT_ID='hospital_ies.engineering_dynamics.v14.v1'
DEFAULT_DYNAMICS=dict(gt_start_delay_minutes=10.,minimum_up_minutes=60.,
    minimum_down_minutes=30.,interlock_rearm_wait_minutes=30.,interlock_rearm_enabled=True)


def _time(value):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<0:
        raise ValueError('Actual clock must be finite and nonnegative')
    return float(value)


def validate_dynamics(value):
    if not isinstance(value,dict) or set(value)-set(DEFAULT_DYNAMICS):
        raise ValueError('Unknown engineering dynamics parameter')
    result=dict(DEFAULT_DYNAMICS,**copy.deepcopy(value))
    for key in DEFAULT_DYNAMICS:
        if key.endswith('_minutes'):
            result[key]=_time(result[key])
            if result[key]>1440:raise ValueError('Research timing outside registered one-day range')
        elif type(result[key]) is not bool:raise ValueError('Rearm permission must be explicit boolean')
    return result


class GTClockLedger:
    """Actual edges only; a future diagnostic query never advances this ledger."""
    def __init__(self,initial_powers,*,time_minute=0.,minimum_up_minutes=60.,minimum_down_minutes=30.):
        self.time=_time(time_minute)
        self.up=_time(minimum_up_minutes);self.down=_time(minimum_down_minutes)
        self.powers=self._powers(initial_powers)
        self.running=self.powers>EPS
        self.last_on=np.full(3,self.time-self.up)
        self.last_off=np.full(3,self.time-self.down)
        self.initial_age='Initially running/off resources assumed eligible; prior actual ages unknown'

    @staticmethod
    def _powers(value):
        try:a=np.asarray(value,dtype=float)
        except (TypeError,ValueError) as exc:raise ValueError('Three actual nonnegative GT powers required') from exc
        if a.shape!=(3,) or not np.isfinite(a).all() or (a<0).any():
            raise ValueError('Three actual nonnegative GT powers required')
        return a.copy()

    def commit(self,powers,time_minute,reason='actual_execution'):
        now=_time(time_minute);values=self._powers(powers)
        if now<self.time-EPS:raise ValueError('Actual GT clock cannot go backward')
        if not isinstance(reason,str) or not reason:raise ValueError('Actual transition reason required')
        on=values>EPS;events=[]
        for i in range(3):
            if bool(on[i])!=bool(self.running[i]):
                events.append(dict(device=f'GT_{i+1}',time_minute=now,
                    before_kW=float(self.powers[i]),after_kW=float(values[i]),
                    transition='started' if on[i] else 'stopped',reason=reason))
                if on[i]:self.last_on[i]=now
                else:self.last_off[i]=now
        self.powers=values;self.running=on;self.time=now
        return events

    def can_stop(self,index,t):
        now=_time(t)
        return bool(not bool(self.running[index]) or now-self.last_on[index]>=self.up-EPS)

    def earliest_start(self,index,t):
        now=_time(t)
        return now if self.running[index] else max(now,float(self.last_off[index]+self.down))

    def as_dict(self,t):
        now=_time(t)
        return dict(observation_time_minute=now,last_actual_commit_minute=self.time,
            actual_gt_kW=self.powers.tolist(),running=self.running.tolist(),
            last_actual_on_minute=self.last_on.tolist(),last_actual_off_minute=self.last_off.tolist(),
            can_stop=[self.can_stop(i,now) for i in range(3)],
            earliest_start_minute=[self.earliest_start(i,now) for i in range(3)],
            minimum_up_minutes=self.up,minimum_down_minutes=self.down,initial_age_assumption=self.initial_age)


class EngineeringDynamicsMixin:
    """No planner or fast action: only already-arrived actuator and latch state."""
    def __init__(self,env,initial_action):
        self.gt_clocks=None;self.interlock_latched={};self._arriving_command=None
        self._transition_reason='actual_execution'
        super().__init__(env,initial_action)
        c=env.engineering_config
        self.gt_clocks=GTClockLedger(self.state.gt_prev,time_minute=self.time,
            minimum_up_minutes=c['minimum_up_minutes'],minimum_down_minutes=c['minimum_down_minutes'])
        self.log('engineering_initial_age_assumption',assumption=self.gt_clocks.initial_age,
            dynamics_contract=DYNAMICS_CONTRACT_ID,parameters=c)

    def _commit_sources(self,a,actual=False):
        super()._commit_sources(a,actual=actual)
        if self.gt_clocks is not None and not getattr(self,'_read_only_source_probe',False):
            reason=getattr(self,'_transition_reason','actual_execution')
            for event in self.gt_clocks.commit(self.state.gt_prev,float(self.time),reason):
                kind='gt_actual_'+event.pop('transition')
                event.pop('time_minute',None)
                self.log(kind,**event)
            self._transition_reason='actual_execution'

    def _stress_for_solution(self,connected,solution):
        previous=getattr(self,'_read_only_source_probe',False)
        self._read_only_source_probe=True
        try:return super()._stress_for_solution(connected,solution)
        finally:self._read_only_source_probe=previous

    def _rearm_allowed(self,i,dispatch,f):
        device=f'GT_{i+1}';latch=self.interlock_latched[device];c=self.env.engineering_config
        if (not c['interlock_rearm_enabled'] or f[device]<=EPS or device in self.state.extra_faulted
            or self.time<latch['reset_eligible_minute']-EPS
            or self.gt_clocks.earliest_start(i,self.time)>self.time+EPS
            or self._arriving_command is None):return False
        _,row,loads,_=self.data_at(self.time)
        cuts=np.clip(np.asarray(self._arriving_command['disconnected'],float),0.,loads)
        connected=loads-cuts
        auxiliary=maximum_auxiliary_sink(self.env.plant,row,connected,f,dispatch)
        # GRID is a regulated nonnegative importer, not an absorbing sink.
        rated=float(gt_level_properties(self.env.plant,float(row.outdoor_temperature_C))[0][-1])
        diesel=(min(float(dispatch['diesel_kW']),self.env.plant.emergency_diesel_kW*f['DIESEL'])
            if self.diesel_available(self.time,f) else 0.)
        generation=sum(min(max(0.,float(x)),rated*f[f'GT_{j+1}'])
            for j,x in enumerate(dispatch['gt_electric_kW']))+diesel
        return generation<=float(connected[:,0].sum())+auxiliary+1e-4

    def _clamp(self,dispatch,f,normal_ramp=False):
        request=copy.deepcopy(dispatch);previous=np.asarray(self.executed['gt_electric_kW'],float)
        c=self.env.engineering_config
        existing=set(self.gt_start_pending)
        for i,power in enumerate(previous):
            device=f'GT_{i+1}'
            if normal_ramp and float(request['gt_electric_kW'][i])<=EPS and i in self.gt_start_pending:
                cancelled=self.gt_start_pending.pop(i)
                self.log('gt_pending_start_cancelled_by_arrived_stop',device=device,
                    cancelled_start=cancelled,actual_output_kW=float(power))
            if device in self.interlock_latched and float(request['gt_electric_kW'][i])>EPS:
                if normal_ramp and self._rearm_allowed(i,request,f):
                    latch=self.interlock_latched.pop(device)
                    self.log('interlock_rearmed_by_arrived_command',device=device,previous_latch=latch,
                        command_id=self._arriving_command['id'],true_protection_cleared=False)
                else:request['gt_electric_kW'][i]=0.
            if (normal_ramp and f[device]>EPS and power>EPS
                and float(request['gt_electric_kW'][i])<=EPS and not self.gt_clocks.can_stop(i,self.time)):
                request['gt_electric_kW'][i]=float(power)
                self.log('minimum_up_normal_stop_held',device=device,requested_kW=0.,held_kW=float(power),
                    minimum_up_minutes=c['minimum_up_minutes'])
        result=super()._clamp(request,f,normal_ramp=normal_ramp)
        for i in range(3):
            device=f'GT_{i+1}'
            if f[device]<=EPS and previous[i]>EPS:self._transition_reason='forced_fault_or_protection'
            pending=self.gt_start_pending.get(i)
            # min-down is independent of the optional startup-delay parameter.
            if (pending is None and normal_ramp and previous[i]<=EPS and f[device]>EPS
                and float(request['gt_electric_kW'][i])>EPS
                and self.gt_clocks.earliest_start(i,self.time)>self.time+EPS):
                eligible=self.gt_clocks.earliest_start(i,self.time)
                pending=dict(started=eligible,ready=eligible+c['gt_start_delay_minutes'],
                    requested=float(result['gt_electric_kW'][i]))
                self.gt_start_pending[i]=pending
            if pending is not None:
                if normal_ramp and c['gt_start_delay_minutes']==0.:
                    pending['requested']=float(request['gt_electric_kW'][i])
                    pending['ready']=pending['started']+pending['requested']/float(self.env.config['gt_ramp_kW_per_hour'])*60.
                if i not in existing:
                    candidate_started=float(pending['started']);candidate_ready=float(pending['ready'])
                    eligible=self.gt_clocks.earliest_start(i,self.time)
                    pending['started']=eligible
                    rate=float(self.env.config['gt_ramp_kW_per_hour'])
                    pending['ready']=eligible+max(c['gt_start_delay_minutes'],pending['requested']/rate*60.)
                    pending['start_event_recorded']=False
                    self.log('gt_start_schedule_projected',device=device,
                        requested_process_start_minute=candidate_started,requested_ready_minute=candidate_ready,
                        actual_eligible_process_start_minute=float(pending['started']),
                        projected_ready_minute=float(pending['ready']),
                        reason='actual minimum-off clock and original aggregate ramp/start readiness',
                        projection_is_not_actual_supply=True)
                    if eligible>self.time+EPS:
                        self.log('minimum_off_start_wait',device=device,earliest_start_minute=eligible,
                            requested_kW=pending['requested'])
                if self.time<pending['ready']-EPS:result['gt_electric_kW'][i]=0.
            if device in self.interlock_latched:result['gt_electric_kW'][i]=0.
        _,row,_,_=self.data_at(self.time)
        powers,steam,fuel=gt_level_properties(self.env.plant,float(row.outdoor_temperature_C))
        actual=np.asarray(result['gt_electric_kW'],float)
        for i in range(3):
            floor=.4*float(powers[-1])*f[f'GT_{i+1}']
            if (normal_ramp and float(request['gt_electric_kW'][i])<=EPS and previous[i]>=floor
                and EPS<actual[i]<floor-1e-4 and self.gt_clocks.can_stop(i,self.time)):
                # Slower ordinary shutdown at the existing stable floor; no
                # power increase, no faster down-ramp, no false hardware trip.
                actual[i]=floor;result['gt_electric_kW'][i]=floor
                self.log('normal_shutdown_stable_floor_step',device=f'GT_{i+1}',actual_kW=floor,
                    requested_kW=0.,continuation='await next ordinary arrived stop command')
        result['hrsg_steam_lbph']=float(sum(actual[i]*f[f'HRSG_{i+1}'] for i in range(3))*steam[-1]/powers[-1])
        result['gt_fuel_MMBtu_h']=float(actual.sum()*fuel[-1]/powers[-1])
        return result

    def boundary(self):
        due=[c for c in self.pending_commands if c['arrival_minute']<=self.time+EPS]
        self._arriving_command=max(due,key=lambda c:(c['generated_minute'],c['id'])) if due else None
        try:super().boundary()
        finally:self._arriving_command=None
        for i,pending in self.gt_start_pending.items():
            if self.time>=pending['started']-EPS and not pending.get('start_event_recorded',False):
                self.log('gt_start_process_begun',device=f'GT_{i+1}',
                    actual_start_minute=pending['started'],ready_minute=pending['ready'])
                pending['start_event_recorded']=True

    def _next_boundary(self,end):
        native=super()._next_boundary(end)
        starts=[float(p['started']) for p in self.gt_start_pending.values()
            if self.time+EPS<float(p['started'])<=native]
        return min([native]+starts)

    def _surplus_interlock(self,loads,f,row):
        previous=set(self.state.extra_faulted);begin=len(self.events)
        self._transition_reason='forced_algebraic_interlock'
        try:super()._surplus_interlock(loads,f,row)
        finally:self._transition_reason='actual_execution'
        for event in self.events[begin:]:
            if event['kind'] not in ('no_export_interlock','minimum_output_interlock'):continue
            device=event['device']
            if device in previous:continue  # Never clear a true pre-existing protection trip.
            self.state.extra_faulted.discard(device)
            self.gt_start_pending.pop(int(device[-1])-1,None)
            self.interlock_latched[device]=dict(reason=event['kind'],latched_minute=self.time,
                reset_eligible_minute=self.time+self.env.engineering_config['interlock_rearm_wait_minutes'])
            self.log('algebraic_interlock_latched',device=device,latch=self.interlock_latched[device],
                hardware_failure_asserted=False,automatic_restart=False,true_protection_cleared=False)

    def observe(self):
        value=super().observe()
        statuses=[]
        for i in range(3):
            device=f'GT_{i+1}';pending=self.gt_start_pending.get(i)
            if device in self.state.extra_faulted:status='PROTECTION_LOCKED'
            elif value['availability'][device]<=EPS:status='EXOGENOUS_UNAVAILABLE'
            elif device in self.interlock_latched:status='INTERLOCK_LATCHED'
            elif pending is not None:status='WAIT_MIN_OFF' if pending['started']>self.time+EPS else 'STARTING'
            else:status='RUNNING' if self.state.gt_prev[i]>EPS else 'OFF'
            statuses.append(status)
            # Legacy sensors keep their declared enum; the new block is explicit.
            if status=='WAIT_MIN_OFF':value['gt_status'][i]='OFF'
        value['engineering_execution']=dict(contract_id=DYNAMICS_CONTRACT_ID,
            gt_physical_status=statuses,actual_clock=self.gt_clocks.as_dict(self.time),
            interlock_latches=copy.deepcopy(self.interlock_latched),
            true_protection_devices=sorted(self.state.extra_faulted),
            pending_starts=copy.deepcopy(self.gt_start_pending))
        return value
