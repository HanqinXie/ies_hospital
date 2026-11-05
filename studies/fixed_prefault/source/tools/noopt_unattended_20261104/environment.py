"""New execution epoch: cooling dynamics and strictly arithmetic post60 U.

This factory intentionally does not claim the legacy compiled/native witness
source profile. New runners must bind its real source inventory and publish
arithmetic energy/stock receipts with their own schema.
"""
from copy import deepcopy
from functools import partial
from types import MethodType,SimpleNamespace
from hospital_ies.model import solve_hour
from tools.annual_readiness.environment import with_readiness
from tools.single_fault_model_20261102.runtime import ModelSpec as StorageSpec,SingleFaultCoreMixin
from tools.single_fault_fuel_20261102.runtime import ModelSpec as FuelSpec,FuelCoreMixin
from tools.single_fault_fuel_20261102.fuel import FuelCurve
from tools.single_fault_fuel_20261102.environment import decision_snapshot as native_snapshot
from tools.minute1_20261103.runtime import MinuteSamplingSpec,MinuteActualCoreMixin,validate_minute_scenario
from tools.cooling_dynamics_20261104 import default_config,CoolingDynamics
from .runtime import ArithmeticCoreMixin,ForwardCommitMixin,forbid_optimization
from .forward import CONTRACT
from .forward import cooling_vector

MODEL_ID='minute1_fixed_prefault_branches_no_priority_20261105'


def decision_snapshot(env):
    with forbid_optimization(env.core):value=native_snapshot(env)
    value.update(control_execution_model_id=env.model_id,execution_revision=CONTRACT,
        minute1_sampling=deepcopy(env.core.observe()['minute1_sampling']),
        cooling_dynamics=env.core.cooling_dynamics.observe(),
        strict_noopt_optimizer_attempts=deepcopy(env.core._noopt_optimizer_attempts))
    return value


def with_forward_minute1(method,*,minute_source,variant='responsive',cooling_config=None,
        sampling_spec=None,requalification_minutes=None,overlay_spec=None,thermal_profile=None,source_freeze=None):
    if variant not in ('normal','unattended','responsive'):raise ValueError('Unknown variant')
    sampling=MinuteSamplingSpec() if sampling_spec is None else sampling_spec
    original=with_readiness(method,requalification_minutes=requalification_minutes,
        overlay_spec=overlay_spec,thermal_profile=thermal_profile)
    storage=StorageSpec(physical_max_segment_minutes=sampling.physical_max_segment_minutes)
    fuel=FuelSpec(physical_max_segment_minutes=sampling.physical_max_segment_minutes)
    dynamic_config=deepcopy(default_config() if cooling_config is None else cooling_config)

    def create(*args,**kwargs):
        env=original.OperatingHospitalIESEnv(*args,**kwargs)
        curve=FuelCurve.from_source(env.root,env.plant);solver=partial(solve_hour,sf2_fuel_config=curve.as_dict())
        env.single_fault_model_spec=env._single_fault_model_bound_spec=storage
        env.single_fault_fuel_spec=env._single_fault_fuel_bound_spec=fuel
        env.single_fault_fuel_curve=env._single_fault_fuel_bound_curve=curve
        env.minute1_sampling_spec=env._minute1_bound_sampling_spec=sampling
        env.minute_source=env._minute1_bound_source=minute_source
        env.minute1_variant=variant;env.cooling_dynamic_config=deepcopy(dynamic_config)
        env.forward_source_freeze=deepcopy(source_freeze)
        env.operating_model_config.update(single_fault_model=storage.as_dict(),single_fault_fuel_model=fuel.as_dict(),
            single_fault_fuel_curve=curve.as_dict(),minute1_sampling=sampling.as_dict(),
            execution_revision=CONTRACT,cooling_dynamics=deepcopy(dynamic_config),strict_emergency_source_partition=True)
        env.config.update(_sf2_fuel_curve=curve.as_dict(),_sf2_nonactual_fuel_role='point_probe',_sf2_free_fuel_role='free_reference_probe')
        env.config['strict_emergency_source_partition']=True
        env.reference_initialization_solver=env._sf2_bound_reference_solver=solver
        # Preserve the exact SF2 public _solve/path checks. The arithmetic
        # dispatch hook is behind that wrapper, not a monkeypatch of it.
        env.engineering_core_type=type('DynamicFixedReferenceCore',
            (ForwardCommitMixin,MinuteActualCoreMixin,FuelCoreMixin,ArithmeticCoreMixin,
                SingleFaultCoreMixin,env.engineering_core_type),{})
        old_reset=env.reset;old_step=env.step
        def reset(self,*,scenario,seed=None):
            if variant=='unattended':
                raise ValueError('Unattended reset would optimize initialization; use fork_fixed_unattended(common_env) checkpoint')
            validate_minute_scenario(scenario)
            result=old_reset(scenario=scenario,seed=seed)
            self.core.cooling_dynamics=CoolingDynamics(self.cooling_dynamic_config,
                initial_cooling_kW=cooling_vector(self.core._last_running_actual).tolist(),
                initial_availability=self.core.data_at(self.core.time)[-1])
            self.model_id=MODEL_ID
            self.core._check_single_fault_model();self.core._check_sf2_model();self.core._check_minute_model()
            self.episode_snapshot_function=decision_snapshot
            observation=self._observation()
            return (observation,result[1]) if isinstance(result,tuple) else observation
        def issue(self,action):
            if variant=='unattended' and self.core.time>=60.:
                if action not in (None,{}):raise ValueError('Strict U cannot issue a new command after minute60')
                return dict(id=None,optimizer_warning='',issued=False,queued=False,reason='strict_unattended_no_optimization')
            return self.core.generate_command(action)
        def step(self,action=None):
            suppressed=variant=='unattended' and self.core.time>=60.
            if suppressed and action is not None:raise ValueError('Strict U requires explicit None after minute60')
            with forbid_optimization(self.core):result=old_step(action)
            if suppressed:
                observation,reward,done,truncated,info=result
                info=dict(info,requested_action=None,planned_action=None,command_id=None,
                    upper_commands_suppressed=True,lower_dispatch_optimization_disabled=True,
                    projection_status='fixed_reference_arithmetic_forward',dispatch_optimizer_calls=0)
                if self.history:self.history[-1].update(projection_status='fixed_reference_arithmetic_forward',
                    upper_commands_suppressed=True,lower_dispatch_optimization_disabled=True)
                return observation,reward,done,truncated,info
            return result
        env.reset=MethodType(reset,env);env.step=MethodType(step,env);env._issue_upper_command=MethodType(issue,env)
        return env
    return SimpleNamespace(**dict(vars(original),OperatingHospitalIESEnv=create,decision_snapshot=decision_snapshot))


def fork_fixed_unattended(common_env):
    """Fork the complete common pre-fault state; never optimize a U reset.

    Common preparation is an external, separately disclosed prerequisite.
    All timers, queues, fuel, inventories and thermal states are deep copied.
    This API accepts live state, not an incomplete exported JSON state dump.
    """
    from .fixed_branches import freeze_branches
    from .checkpoint import copy_checkpoint
    import numpy as np
    core=getattr(common_env,'core',None)
    if (core is None or getattr(common_env,'minute1_variant',None) not in ('normal','responsive')
            or getattr(common_env,'_fixed_branch_boundary_deferred_at',None)!=60.
            or abs(float(getattr(core,'time',-1.))-60.)>1e-9
            or not getattr(core,'segments',None) or getattr(core,'cooling_dynamics',None) is None):
        raise ValueError('A complete common pre-fault minute60 live checkpoint is required')
    last=core.segments[-1]
    if abs(float(last['end_minute'])-core.time)>1e-9 or last['start_minute']>=60.:
        raise ValueError('Checkpoint must end with the actual pre-fault interval')
    served=np.asarray(last['loads_kW'],float)-np.asarray(last['unserved_kW'],float)
    actual=deepcopy(last['action'])
    reference=deepcopy(core._last_running_actual)
    if getattr(core,'station',None) is not None and not core.station.boiler_ready:
        # Preserve the already requested restart target while actual output is
        # zero. Do not create a new target or reset its physical startup clock.
        reference['boiler_steam_lbph']=float(core.arrived_dispatch.get('boiler_steam_lbph',0.))
    reference['_steam_abs_fraction']=core._last_steam_abs_fraction
    reference['_fixed_branches']=freeze_branches(common_env.plant,served,actual,
        source_flows=last.get('prefault_dedicated_source_deliveries_kW'),
        source=dict(start_minute=last['start_minute'],end_minute=last['end_minute'],
            role='actual_common_prefault_interval',future_information_used=False))
    env=copy_checkpoint(common_env)
    if env.core.env is not env:
        raise ValueError('Checkpoint copy did not retain its own physical environment')
    env.minute1_variant='unattended';env.model_id=MODEL_ID+'_source_partition_v2'
    env.core._noopt_reference=reference;env.core._noopt_optimizer_attempts=[]
    env.core.forward_evaluations=[]
    env.fixed_branch_checkpoint=dict(time_minute=float(core.time),
        common_preparation_segment_count=len(core.segments),
        common_preparation_may_use_optimization=True,unattended_optimization_allowed=False,
        branch_reference=deepcopy(reference['_fixed_branches']),complete_live_state_copied=True)
    original_step=env.step
    def reset(self,**kwargs):
        raise ValueError('Fixed unattended requires a common checkpoint; reset is prohibited')
    def issue(self,action):
        if action not in (None,{}):raise ValueError('Fixed unattended cannot issue a new command')
        return dict(id=None,optimizer_warning='',issued=False,queued=False,
            reason='fixed_prefault_branches_no_optimization')
    def step(self,action=None):
        if action is not None:raise ValueError('Fixed unattended accepts only None')
        with forbid_optimization(self.core):result=original_step(None)
        observation,reward,done,truncated,info=result
        info=dict(info,requested_action=None,planned_action=None,command_id=None,
            upper_commands_suppressed=True,lower_dispatch_optimization_disabled=True,
            projection_status='frozen_prefault_branches_arithmetic',dispatch_optimizer_calls=0)
        return observation,reward,done,truncated,info
    env.reset=MethodType(reset,env);env.step=MethodType(step,env)
    env._issue_upper_command=MethodType(issue,env)
    env.core.log('fixed_branch_unattended_checkpoint_activated',checkpoint=deepcopy(env.fixed_branch_checkpoint))
    return env


def finish_common_prefault_step(common_env,action):
    """Complete 59→60 without applying the fault boundary in common preparation."""
    if (common_env.minute1_variant not in ('normal','responsive')
            or abs(float(common_env.core.time)-59.)>1e-9 or common_env.dt_minutes!=1.):
        raise ValueError('Common pre-fault checkpoint step must be exactly minute59→60')
    common_env._fixed_branch_boundary_stop=60.
    try:
        return common_env.step(action)
    finally:
        common_env._fixed_branch_boundary_stop=None
