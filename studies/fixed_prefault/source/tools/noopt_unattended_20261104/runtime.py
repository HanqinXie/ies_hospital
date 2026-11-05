"""No-optimization execution hooks, behind the source-checked SF2 fuel wrapper.

These hooks preserve native state/event commits. Only actual flow evaluation,
optimizing diagnostic witnesses and cold-mode selection migrate to arithmetic
when U reaches minute60. Normal/responsive flows retain native optimization.
"""
from __future__ import annotations
from copy import deepcopy
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict
import math
import sys
import numpy as np
from hospital_ies.state_transition import EPS
from hospital_ies.model import RT_TO_KW,illustrative_tariff,gt_level_properties
from tools.single_fault_fuel_20261102.runtime import FuelCoreMixin
from tools.cooling_dynamics_20261104 import CoolingDynamics
from .forward import evaluate_forward,cooling_vector,CONTRACT


def noopt(core):
    return core.env.minute1_variant=='unattended'


class ForbiddenDispatchOptimization(BaseException):
    # Fatal execution-contract violation: normal ``except Exception`` fallback
    # handlers must not catch it and continue after CPython clears profiling.
    category='strict_unattended_optimizer_called'


_ACTIVE_U_GUARD=ContextVar('hospital_ies_fixed_branch_noopt_guard',default=None)
_NATIVE_GUARDS_INSTALLED=False


def _reject_optimizer(core,function,module,event):
    item=dict(time_minute=float(core.time),function=function,module=module,
        blocked_before_body=True,event=event)
    core._noopt_optimizer_attempts.append(item)
    core._strict_noopt_failed=True;core.failed=True
    raise ForbiddenDispatchOptimization(str(item))


def _install_native_guards():
    global _NATIVE_GUARDS_INSTALLED
    if _NATIVE_GUARDS_INSTALLED:return
    import gurobipy
    # Cython calls do not consistently emit Python profile events. Intercept
    # their public descriptors explicitly; outside the active U context these
    # wrappers call the unchanged original native method.
    def guarded(original,name):
        def entry(*args,**kwargs):
            core=_ACTIVE_U_GUARD.get()
            if core is not None:_reject_optimizer(core,name,'gurobipy.Model','explicit_native_entry')
            return original(*args,**kwargs)
        return entry
    for name in ('__init__','optimize','optimizeAsync','optimizeBatch','feasRelax','feasRelaxS','computeIIS','tune','presolve'):
        original=getattr(gurobipy.Model,name,None)
        if original is not None:setattr(gurobipy.Model,name,guarded(original,name))
    _NATIVE_GUARDS_INSTALLED=True


@contextmanager
def forbid_optimization(core):
    """Block Python and native entries without replacing pinned project functions."""
    if core.env.minute1_variant!='unattended':
        yield
        return
    if getattr(core,'_strict_noopt_failed',False):
        raise ForbiddenDispatchOptimization('Unattended optimizer guard previously failed; continuation prohibited')
    _install_native_guards()
    guard_token=_ACTIVE_U_GUARD.set(core)
    from tools.operating_response import physical_balance
    from hospital_ies import solver_backend
    from hospital_ies import model
    blocked={fn.__code__ for fn in (physical_balance.solve_physical,solver_backend.milp,model.solve_hour)}
    names={'solve_hour','solve_physical','solve_joint','_solve_milp','milp','linprog',
        '_gurobi_attempt','_linprog_attempt','_highs_attempt','_choose_storage_candidate'}
    previous=sys.getprofile()
    def audit(frame,event,arg):
        module=frame.f_globals.get('__name__','') if event=='call' else getattr(arg,'__module__','') or ''
        owner=getattr(arg,'__self__',None)
        owner_module=type(owner).__module__ if owner is not None else ''
        prohibited=(event=='call' and (frame.f_code in blocked or
            (module.startswith(('hospital_ies.','tools.')) and frame.f_code.co_name in names))
            or event in ('call','c_call') and any(x.startswith(('scipy.optimize','gurobipy','highspy'))
                for x in (module,owner_module)))
        if prohibited and noopt(core):
            item=dict(time_minute=float(core.time),function=frame.f_code.co_name,
                module=module,blocked_before_body=True,event=event,
                native_function=getattr(arg,'__name__',None) if event=='c_call' else None)
            core._noopt_optimizer_attempts.append(item)
            core._strict_noopt_failed=True;core.failed=True
            raise ForbiddenDispatchOptimization(str(item))
        if previous is not None:previous(frame,event,arg)
    sys.setprofile(audit)
    try:
        yield
        if getattr(core,'_strict_noopt_failed',False):
            raise ForbiddenDispatchOptimization('A forbidden optimizer entry was caught internally; U failed')
    finally:
        sys.setprofile(previous)
        _ACTIVE_U_GUARD.reset(guard_token)


class ArithmeticCoreMixin:
    def __init__(self,env,initial_action):
        self._noopt_reference=None;self._last_running_actual=deepcopy(initial_action)
        self._last_steam_abs_fraction=0.;self._noopt_optimizer_attempts=[]
        self.forward_evaluations=[]
        super().__init__(env,initial_action)
        abs_steam=float(initial_action.get('absorption_cooling_kW',0.))/RT_TO_KW*env.plant.abs_steam_lb_per_rt
        supplied=float(initial_action.get('hrsg_steam_lbph',0.))+float(initial_action.get('boiler_steam_lbph',0.))
        self._last_steam_abs_fraction=abs_steam/max(abs_steam,supplied) if abs_steam>EPS else 0.
        # The inherited reset serializes its initial core before activating
        # current minute requests. Install the new typed state only after that
        # immutable original initialization receipt has been produced.
        self.cooling_dynamics=None

    def boundary(self):
        if (not noopt(self) and getattr(self.env,'_fixed_branch_boundary_stop',None)==self.time):
            # Stop common preparation before t60 exogenous events/old commands
            # and terminal-boundary optimizer diagnostics can execute.
            self.env._fixed_branch_boundary_deferred_at=float(self.time)
            return
        if noopt(self):
            if self._noopt_reference is None:
                self._noopt_reference=deepcopy(self._last_running_actual)
                self._noopt_reference['_steam_abs_fraction']=self._last_steam_abs_fraction
                # A previously requested, currently powered restart retains its
                # old target. This is not a new permission or standby selection.
                if not self.station.boiler_ready:
                    self._noopt_reference['boiler_steam_lbph']=float(self.arrived_dispatch.get('boiler_steam_lbph',0.))
                self.log('strict_noopt_reference_latched',reference=self._noopt_reference,
                    source='last actual interval plus already requested startup targets',contract=CONTRACT)
            due=[x for x in self.pending_commands if x['arrival_minute']<=self.time+EPS]
            if due:
                command=max(due,key=lambda x:(x['generated_minute'],x['id']))
                planned=command.get('joint_dispatch',{}).get('planned_current_action')
                # Dispatch ceilings still reach the native permission state,
                # but cannot invent a new running target when no actual plan
                # accompanied that old command.
                permission_only=planned is None
                if permission_only:planned={}
                for key in ('absorption_cooling_kW','electric_chiller_cooling_kW','boiler_steam_lbph'):
                    if key in planned:self._noopt_reference[key]=deepcopy(planned[key])
                # Old commands retain their equipment permissions and arrival
                # times, but cannot move the frozen heat/ABS branch split.
                self.log('strict_noopt_old_command_reference_arrived',command_id=command['id'],
                    generated_minute=command['generated_minute'],reference=self._noopt_reference,
                    permission_only=permission_only,
                    no_new_optimizer_or_command=True)
        return super().boundary()

    def _cooling_preview(self,dt_h,reference,f):
        p=self.env.plant
        caps=np.array([p.absorption_rt,*p.electric_chillers_rt])*RT_TO_KW
        raw=np.array([f.get('ABS',1.),*[f.get(f'EC_{i}',1.) for i in (1,2,3)]])
        target=np.minimum(np.maximum(0.,reference),caps*raw)
        preview=self.cooling_dynamics.preview(dt_h*60.,target.tolist(),f)
        effective=dict(f)
        effective['TOWER']=f['TOWER']*preview['tower_mean_factor']
        effective['AUX_CHW_PUMP']=f.get('AUX_CHW_PUMP',1.)*preview['pump_mean_factor']
        return target,preview,effective

    def _forward(self,loads,f,dt_h,execution_action=None,*,require_balance=True,fuel_path=None):
        _,row,_,_=self.data_at(self.time)
        reference=self._noopt_reference or self._last_running_actual
        a=deepcopy(self.executed if execution_action is None else execution_action)
        target,preview,effective=self._cooling_preview(dt_h,cooling_vector(reference),f)
        context=self._current_context(effective)
        stores={name:dict(store.config,energy_kWh=store.energy_kWh)
            for name,store in (('ups',self.ups),('cold',self.cold_store))}
        cfg=dict(self.env.config,grid_import_limit_kW=float(self.env.scenario.get('grid_limit_kW',self.env.config['grid_import_limit_kW'])),
            cold_pump_kW_per_kW=self.env.sci_config['cold_pump_kW_per_kW'])
        result=evaluate_forward(row=row,plant=self.env.plant,loads=loads,availability=effective,
            execution_action=a,reference=reference,context=context,stores=stores,dt_h=dt_h,config=cfg,
            execution_contract=self.env.operating_execution_contract,
            cooling_limits_kW=preview['mean_cooling_limits_kW'],fuel_curve=self.env.single_fault_fuel_curve,
            fuel_path=fuel_path,require_balance=require_balance)
        result['_cooling_reference_kW']=target.tolist();result['_cooling_raw_availability']=dict(f)
        result['_cooling_preview']=preview
        return result

    def _solve(self,loads,f,dt_h,phase,action,**kwargs):
        if not noopt(self):
            result=super()._solve(loads,f,dt_h,phase,action,**kwargs)
            if self.cooling_dynamics is None:return result
            # Desire comes from an actual feasible source/resource dispatch,
            # never the broad independent converter command ceilings.
            target,preview,effective=self._cooling_preview(dt_h,cooling_vector(result['action']),f)
            fixed=deepcopy(kwargs.get('execution_action',self.executed))
            fixed['absorption_cooling_kW']=min(float(fixed['absorption_cooling_kW']),preview['mean_cooling_limits_kW'][0])
            fixed['electric_chiller_cooling_kW']=np.minimum(fixed['electric_chiller_cooling_kW'],preview['mean_cooling_limits_kW'][1:]).tolist()
            changed=(not np.allclose(cooling_vector(fixed),cooling_vector(kwargs.get('execution_action',self.executed)),rtol=0.,atol=1e-7)
                or effective!=f)
            if changed:
                revised=dict(kwargs,execution_action=fixed)
                result=super()._solve(loads,effective,dt_h,phase,action,**revised)
            result['_cooling_reference_kW']=target.tolist();result['_cooling_raw_availability']=dict(f)
            result['_cooling_preview']=preview
            return result
        result=self._forward(loads,f,dt_h,kwargs.get('execution_action'),fuel_path=kwargs.get('sf2_fuel_path'))
        if phase!='local_down_endpoint':self._storage_candidate=dict(time_minute=self.time,action=deepcopy(result['action']))
        self.forward_evaluations.append(dict(time_minute=float(self.time),dt_h=float(dt_h),phase=phase,
            contract=CONTRACT,dispatch_optimizer_calls=0,energy_balance_residuals=result['energy_balance_residuals']))
        return result

    def _select_current_ports(self,loads,f):
        if not noopt(self):return super()._select_current_ports(loads,f)
        self._m22_ports=self._base_ports()

    def _automatic_shed(self,loads,sol=None):
        if not noopt(self):return super()._automatic_shed(loads,sol)
        # Do not generate priority-ordered shedding. An already sent physical
        # command retains its original arrival and exact requested amounts.
        pending=self.pending_shed
        if pending is None:return
        if self.time+EPS<float(pending['arrival']):return
        connected=np.maximum(0.,np.asarray(loads,float)-self.state.disconnected_loads)
        extra=np.minimum(np.maximum(0.,np.asarray(pending['requested'],float)),connected)
        self.state.disconnected_loads+=extra;self.automatic_disconnected+=extra
        self.disconnected_fraction=np.divide(self.state.disconnected_loads,loads,
            out=self.disconnected_fraction.copy(),where=loads>EPS)
        self.automatic_fraction=np.divide(self.automatic_disconnected,loads,
            out=self.automatic_fraction.copy(),where=loads>EPS)
        self.pending_shed=None;self.auto_shed_latched=True
        self.log('prefault_automatic_shed_command_arrived',disconnected_kW=extra,
            new_priority_selection=False,original_arrival_minute=pending['arrival'])

    def _surplus_interlock(self,loads,f,row):
        if not noopt(self):return super()._surplus_interlock(loads,f,row)
        # Retain the registered minimum-stable and no-export protections. The
        # old subset optimizer is not called and no fictitious sink is added.
        if self.env.execution_contract['surplus_interlock']=='none':return
        floor=.4*float(gt_level_properties(self.env.plant,float(row.outdoor_temperature_C))[0][-1])
        if self.env.execution_contract['surplus_interlock_delay_minutes']==0.:
            for i,power in enumerate(list(self.executed['gt_electric_kW'])):
                if EPS<power<floor*f[f'GT_{i+1}']-1e-4:
                    self._latch_gt(i,f,'minimum_output_interlock',actual_kW=power,minimum_kW=floor*f[f'GT_{i+1}'])
        while True:
            proof=self._forward(loads,f,self.env.dt_h,require_balance=False)
            surplus=proof['unabsorbed_electric_kW']
            if surplus<=1e-4:return
            if self.env.execution_contract['surplus_interlock_delay_minutes']>0.:return
            i=int(np.argmax(self.executed['gt_electric_kW']))
            if self.executed['gt_electric_kW'][i]<=EPS:return
            self._latch_gt(i,f,'no_export_interlock',power_surplus_kW=surplus,
                connected_electric_kW=float(loads[:,0].sum()),arithmetic_proof=True)

    def _electric_sink_upper(self,loads,f,row):
        if not noopt(self):return super()._electric_sink_upper(loads,f,row)
        r=self._forward(loads,f,self.env.dt_h,require_balance=False)
        a=r['action'];q=cooling_vector(a)
        converter=(q[0]*self.env.plant.abs_electric_kW_per_rt+q[1:].sum()*self.env.plant.ec_electric_kW_per_rt)/RT_TO_KW
        return dict(upper_kW=sum(a['gt_electric_kW'])+a['grid_import_kW']+a['diesel_kW']-r['unabsorbed_electric_kW'],
            maximum_converter_electric_kW=converter,maximum_held_station_auxiliary_kW=a['station_auxiliary_kW'],arithmetic_only=True)

    def _correct_gt_floor(self,loads,f,row):
        if noopt(self):return False
        return super()._correct_gt_floor(loads,f,row)

    def _rearm_allowed(self,i,dispatch,f):
        if not noopt(self):return super()._rearm_allowed(i,dispatch,f)
        device=f'GT_{i+1}';latch=self.interlock_latched[device];cfg=self.env.engineering_config
        if (not cfg['interlock_rearm_enabled'] or f[device]<=EPS or device in self.state.extra_faulted
            or self.time<latch['reset_eligible_minute']-EPS or self.gt_clocks.earliest_start(i,self.time)>self.time+EPS
            or self._arriving_command is None):return False
        _,_,loads,_=self.data_at(self.time)
        connected=loads-np.clip(np.asarray(self._arriving_command['disconnected'],float),0.,loads)
        r=self._forward(connected,f,self.env.dt_h,dispatch,require_balance=False)
        return abs(r['unabsorbed_electric_kW'])<=1e-4

    def _destination_return_ready(self,hardware):
        if not noopt(self):return super()._destination_return_ready(hardware)
        _,_,total,_=self._hardware_data_at(self.time)
        loads=np.maximum(0.,total-self.state.disconnected_loads)
        candidate=deepcopy(self.executed);candidate['grid_import_kW']=float(self.arrived_dispatch['grid_import_kW']);candidate['diesel_kW']=0.
        result=self._forward(loads,hardware,self.env.dt_h,candidate,require_balance=False)
        # Existing hardware/timer gates remain in the caller. Do not condition
        # transfer on preferential preservation of a service class.
        return abs(result['unabsorbed_electric_kW'])<=1e-4

    def _critical_preservation_probe(self,hardware,*,diesel):
        if not noopt(self):return super()._critical_preservation_probe(hardware,diesel=diesel)
        _,_,total,_=self._hardware_data_at(self.time);loads=np.maximum(0.,total-self.state.disconnected_loads)
        candidate=deepcopy(self.executed);candidate['grid_import_kW']=0.
        candidate['diesel_kW']=float(self.arrived_dispatch['diesel_kW']) if diesel else 0.
        r=self._forward(loads,dict(hardware,GRID=0.),self.env.dt_h,candidate,require_balance=False)
        return abs(r['unabsorbed_electric_kW'])<=1e-4

    def non_diesel_witness(self):
        if not noopt(self):return super().non_diesel_witness()
        _,_,total,f=self.data_at(self.time);loads=np.maximum(0.,total-self.state.disconnected_loads)
        a=deepcopy(self.executed);a['diesel_kW']=0.
        r=self._forward(loads,f,self.env.dt_h,a,require_balance=False)
        return dict(status='arithmetic_fixed_branches_only',ready=bool(abs(r['unabsorbed_electric_kW'])<=1e-4),
            actual_action=r['action'],critical_unserved_kW=r['lost'][:2].tolist(),inventory_committed=False,dispatch_optimizer_calls=0)


class ForwardCommitMixin:
    def _after_actual_segment(self,segment,result):
        arithmetic=result.get('forward_contract')==CONTRACT
        if arithmetic:
            # Migrate only the old native-primal owner requirement. All lower
            # SF1/storage/boiler/thermal/qualification commits still execute.
            super(FuelCoreMixin,self)._after_actual_segment(segment,result)
            fuel=deepcopy(result['arithmetic_fuel']);fuel.update(schema='hospital_ies.arithmetic_running_fuel.v1',
                accounting_committed=True,curve_identity=self.env.single_fault_fuel_curve.identity,
                source='same immutable FuelPath integral; arithmetic result owner; no native primal claimed')
            segment['arithmetic_running_fuel']=fuel
            diesel=segment.get('diesel_local_response',{})
            adjustment=diesel.get('shaft_equivalent_fee_USD',0.)-diesel.get('original_electric_fee_USD',0.)
            segment['sf2_physical_operating_fee_USD_step']=result['sf2_physical_operating_fee_USD_step']+adjustment
            segment['sf2_artificial_unused_steam_tie_USD_step']=0.
            segment['forward_contract']=CONTRACT;segment['dispatch_optimizer_calls']=0
            segment['strict_unattended_forward']=dict(contract=CONTRACT,
                running_reference=deepcopy(self._noopt_reference),dispatch_optimizer_calls=0,
                allocation=deepcopy(result['allocation']),priority_order_used=False,
                source_partition=deepcopy(result['source_partition']),
                named_balance_residuals=deepcopy(result['named_balance_residuals']),
                original_inventory_event_split_and_physical_commits_retained=True)
        else:
            # Bypass only the legacy annual native-witness exporter. Keep the
            # source-checked accepted native gas result and all physical hooks.
            FuelCoreMixin._after_actual_segment(self,segment,result)
        raw=result['_cooling_raw_availability'];target=result['_cooling_reference_kW']
        a=segment['action']
        support=dict(tower=bool(a.get('tower_rejection_kW',0.)>EPS),
            pump=bool(a.get('absorption_cooling_kW',0.)+sum(a.get('electric_chiller_cooling_kW',[]))
                +a.get('cold_discharge_kW',0.)+a.get('cold_charge_kW',0.)>EPS))
        segment['cooling_dynamics']=self.cooling_dynamics.commit_actual(cooling_vector(segment['action']).tolist(),
            segment['end_minute']-segment['start_minute'],target,raw,support_running=support)
        self._last_running_actual=deepcopy(segment.get('terminal_action',segment['action']))
        a=segment['action'];plant=self.env.plant
        abs_steam=a['absorption_cooling_kW']/RT_TO_KW*plant.abs_steam_lb_per_rt
        heat_steam=float(np.asarray(result['served'])[:,1].sum())/(plant.steam_kWh_per_lb*plant.exchanger_eff)
        self._last_steam_abs_fraction=abs_steam/(abs_steam+heat_steam) if abs_steam+heat_steam>EPS else 0.
        hour,row,_=self.env.minute_source.at_elapsed(segment['start_minute'],self.env.scenario['start_hour'])
        base=(self.env.scenario['start_hour']-1)*60.;start=base+segment['start_minute'];end=base+segment['end_minute']
        source_start=float(row.minute_of_year-1)
        if not source_start<=start<end<=source_start+1.:raise ValueError('Actual segment exceeds native minute support')
        segment['grid_tariff_USD_kWh']=illustrative_tariff(row.timestamp,self.env.plant)
        segment['minute1_source_context']=dict(schema='hospital_ies.minute1.actual_source_context.v1',
            source_manifest_schema=self.env.minute_source.spec['schema'],files=deepcopy(self.env.minute_source.spec['files']),
            minute_of_year=int(row.minute_of_year),source_interval_start_absolute_minute=source_start,
            source_interval_end_absolute_minute=source_start+1.,actual_interval_start_absolute_minute=start,
            actual_interval_end_absolute_minute=end,compiled_flow_DTO_contains_this_context=False)
        segment['minute1_sampling']=dict(self.env.minute1_sampling_spec.as_dict(),minute_of_year=int(row.minute_of_year),hour_of_year=hour,
            source_interval_start_standard=str(row.timestamp_start_standard),source_interval_end_standard=str(row.timestamp_end_standard),
            actual_interval_minutes=segment['end_minute']-segment['start_minute'])
        segment['actual_result_owner']='arithmetic_forward' if arithmetic else 'accepted_native_optimizer_with_dynamic_limits'
        segment['unused_steam_lbph']=(result.get('unused_steam_lbph') if arithmetic else
            max(0.,float(a['hrsg_steam_lbph'])+a['boiler_steam_lbph']-abs_steam-heat_steam))
