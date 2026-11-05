"""Explicit minute factory over the original readiness and SF1/SF2 physics.

The historical SF1/SF2 factories remain 15-minute entry points. This factory
installs their unchanged immutable stores/fuel contracts directly on the same
readiness environment, with separately named minute source and command clocks.
"""
from copy import deepcopy
from functools import partial
from types import MethodType,SimpleNamespace

from hospital_ies.model import solve_hour
from tools.annual_readiness.environment import with_readiness
from tools.single_fault_model_20261102.runtime import ModelSpec as StorageSpec,SingleFaultCoreMixin
from tools.single_fault_fuel_20261102.runtime import ModelSpec as FuelSpec,FuelCoreMixin
from tools.single_fault_fuel_20261102.fuel import FuelCurve
from tools.single_fault_fuel_20261102.environment import decision_snapshot as sf2_decision_snapshot
from . import MODEL_ID
from .runtime import MinuteSamplingSpec,MinuteActualCoreMixin,validate_minute_scenario
from .source import MinuteSource

VARIANTS=('normal','unattended','responsive')


class StrictUnattendedForwardUnavailable(RuntimeError):
    """The legacy lower optimizer cannot implement the strict U definition.

    This is an execution refusal, not an outage, all-zero result, or completed
    forward model. The exception leaves the native step entirely unentered.
    """
    category='strict_unattended_forward_unavailable'


def with_minute1(method,*,minute_source,source_admission,source_freeze,
                 sampling_spec=None,variant='responsive',requalification_minutes=None,
                 overlay_spec=None,thermal_profile=None):
    sampling=MinuteSamplingSpec() if sampling_spec is None else sampling_spec
    if type(sampling) is not MinuteSamplingSpec or type(minute_source) is not MinuteSource:
        raise ValueError('Explicit immutable minute sampling and loaded minute source required')
    if variant not in VARIANTS:raise ValueError('Unknown registered minute intervention')
    if type(source_admission) is not dict or type(source_freeze) is not dict:
        raise ValueError('Explicit externally pinned minute source admission and freeze required')
    reference=deepcopy(source_admission);frozen=deepcopy(source_freeze)
    storage=StorageSpec(physical_max_segment_minutes=sampling.physical_max_segment_minutes)
    fuel=FuelSpec(physical_max_segment_minutes=sampling.physical_max_segment_minutes)
    original=with_readiness(method,requalification_minutes=requalification_minutes,
        overlay_spec=overlay_spec,thermal_profile=thermal_profile)

    def create(*args,**kwargs):
        env=original.OperatingHospitalIESEnv(*args,**kwargs)
        if getattr(env,'annual_program_data_context',None) is not None or getattr(env,'local_actual_witness_source_context',None) is not None:
            raise ValueError('Minute factory cannot combine historical runtime source contexts')
        curve=FuelCurve.from_source(env.root,env.plant)
        solver=partial(solve_hour,sf2_fuel_config=curve.as_dict())
        env.single_fault_model_spec=env._single_fault_model_bound_spec=storage
        env.single_fault_fuel_spec=env._single_fault_fuel_bound_spec=fuel
        env.single_fault_fuel_curve=env._single_fault_fuel_bound_curve=curve
        env.minute1_sampling_spec=env._minute1_bound_sampling_spec=sampling
        env.minute_source=env._minute1_bound_source=minute_source
        env.minute1_source_admission=deepcopy(reference);env.minute1_source_freeze=deepcopy(frozen)
        env.minute1_variant=variant
        env.operating_model_config.update(single_fault_model=storage.as_dict(),
            single_fault_fuel_model=fuel.as_dict(),single_fault_fuel_curve=curve.as_dict(),
            minute1_sampling=sampling.as_dict())
        env.config.update(_sf2_fuel_curve=curve.as_dict(),_sf2_nonactual_fuel_role='point_probe',
            _sf2_free_fuel_role='free_reference_probe')
        env.reference_initialization_solver=env._sf2_bound_reference_solver=solver
        env.engineering_core_type=type('MinuteSingleFaultFuelCore',
            (MinuteActualCoreMixin,FuelCoreMixin,SingleFaultCoreMixin,env.engineering_core_type),{})
        old_reset=env.reset;old_step=env.step

        def check(self):
            if (self.minute1_sampling_spec is not sampling or self._minute1_bound_sampling_spec is not sampling
                    or self.minute_source is not minute_source or self._minute1_bound_source is not minute_source
                    or self.minute1_variant!=variant or self.minute1_source_admission!=reference
                    or self.minute1_source_freeze!=frozen
                    or self.single_fault_model_spec is not storage or self._single_fault_model_bound_spec is not storage
                    or self.single_fault_fuel_spec is not fuel or self._single_fault_fuel_bound_spec is not fuel
                    or self.single_fault_fuel_curve is not curve or self._single_fault_fuel_bound_curve is not curve
                    or type(curve) is not FuelCurve
                    or self.operating_model_config.get('single_fault_model')!=storage.as_dict()
                    or self.operating_model_config.get('single_fault_fuel_model')!=fuel.as_dict()
                    or self.operating_model_config.get('single_fault_fuel_curve')!=curve.as_dict()
                    or self.operating_model_config.get('minute1_sampling')!=sampling.as_dict()
                    or self.config.get('_sf2_fuel_curve')!=curve.as_dict()
                    or self.config.get('_sf2_nonactual_fuel_role')!='point_probe'
                    or self.config.get('_sf2_free_fuel_role')!='free_reference_probe'
                    or self.reference_initialization_solver is not solver or self._sf2_bound_reference_solver is not solver
                    or type(solver) is not partial or solver.func is not solve_hour or solver.args!=()
                    or solver.keywords!={'sf2_fuel_config':curve.as_dict()}
                    or getattr(self,'annual_program_data_context',None) is not None
                    or getattr(self,'local_actual_witness_source_context',None) is not None):
                raise ValueError('Minute source/sampling/unchanged physical model binding changed')

        def reset(self,*,scenario,seed=None):
            validate_minute_scenario(scenario);check(self)
            returned=old_reset(scenario=scenario,seed=seed)
            check(self);self.model_id=MODEL_ID
            self.core._check_single_fault_model();self.core._check_sf2_model();self.core._check_minute_model()
            observation=self._observation()
            return (observation,returned[1]) if isinstance(returned,tuple) else observation

        def issue(self,action):
            check(self)
            if variant=='unattended' and self.core.time>=60.:
                if action not in (None,{}):raise ValueError('Unattended interval cannot issue an upper command')
                return dict(id=None,optimizer_warning='',issued=False,queued=False,
                    reason='registered_unattended_after60')
            return self.core.generate_command(action)

        def step(self,action=None):
            check(self)
            suppressed=variant=='unattended' and self.core.time>=60.
            if suppressed and action is not None:raise ValueError('Unattended post60 step requires an explicit None command request')
            if variant=='unattended' and self.core.time+self.dt_minutes>=60.:
                # advance() calls boundary() at its endpoint. Refuse the whole
                # crossing step so even a t59->t60 endpoint cannot enter the
                # inherited optimizing physical/protection/port-selection path.
                # No service, energy, inventory, queue, or clock is fabricated.
                raise StrictUnattendedForwardUnavailable(
                    'strict_unattended_forward_unavailable: legacy unattended '
                    'execution still uses lower dispatch optimization; a '
                    'validated arithmetic forward evaluator is not implemented. '
                    'Refused before native step because its endpoint reaches '
                    'minute60; this prefix is incomplete, not a strict U result.')
            returned=old_step(action)
            if suppressed:
                observation,reward,done,truncated,info=returned
                info=dict(info,requested_action=None,planned_action=None,command_id=None,
                    upper_commands_suppressed=True,upper_command_suppression_reason='registered_unattended_after60',
                    projection_status='held_command_no_upper_dispatch')
                if self.history:self.history[-1].update(upper_commands_suppressed=True,
                    upper_command_suppression_reason='registered_unattended_after60',
                    projection_status='held_command_no_upper_dispatch')
                return observation,reward,done,truncated,info
            return returned

        env.reset=MethodType(reset,env);env.step=MethodType(step,env)
        env._issue_upper_command=MethodType(issue,env)
        env.model_id=MODEL_ID;env.episode_snapshot_function=decision_snapshot
        return env
    return SimpleNamespace(**dict(vars(original),OperatingHospitalIESEnv=create))


def decision_snapshot(env):
    value=sf2_decision_snapshot(env)
    value['control_execution_model_id']=MODEL_ID
    value['minute1_sampling']=deepcopy(env.core.observe()['minute1_sampling'])
    return value
