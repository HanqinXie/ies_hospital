"""SF2 opt-in factory installed before the existing local runtime is frozen."""
from copy import deepcopy
from functools import partial
from types import MethodType,SimpleNamespace
from hospital_ies.model import solve_hour
from tools.single_fault_model_20261102.environment import with_single_fault_model
from tools.single_fault_model_20261102.runtime import ModelSpec as StorageSpec
from .fuel import FuelCurve
from .runtime import MODEL_ID,ModelSpec,FuelCoreMixin,validate_single_fault_scenario


def with_single_fault_fuel(method,*,model_spec=None,**readiness_options):
    spec=ModelSpec() if model_spec is None else model_spec
    if type(spec) is not ModelSpec:raise ValueError('SF2 immutable explicit ModelSpec required')
    original=with_single_fault_model(method,model_spec=StorageSpec(
        physical_max_segment_minutes=spec.physical_max_segment_minutes),**readiness_options)
    def create(*args,**kwargs):
        env=original.OperatingHospitalIESEnv(*args,**kwargs)
        curve=FuelCurve.from_source(env.root,env.plant)
        env.single_fault_fuel_spec=env._single_fault_fuel_bound_spec=spec
        env.single_fault_fuel_curve=env._single_fault_fuel_bound_curve=curve
        env.operating_model_config['single_fault_fuel_model']=spec.as_dict()
        env.operating_model_config['single_fault_fuel_curve']=curve.as_dict()
        env.config['_sf2_fuel_curve']=curve.as_dict()
        env.config['_sf2_nonactual_fuel_role']='point_probe'
        env.config['_sf2_free_fuel_role']='free_reference_probe'
        reference_solver=partial(solve_hour,sf2_fuel_config=curve.as_dict())
        env.reference_initialization_solver=env._sf2_bound_reference_solver=reference_solver
        env.engineering_core_type=type('SingleFaultFuelCore',(FuelCoreMixin,env.engineering_core_type),{})
        old_reset=env.reset
        def check(self):
            if (self.single_fault_fuel_spec is not spec or self.single_fault_fuel_curve is not curve
                    or self.operating_model_config.get('single_fault_fuel_model')!=spec.as_dict()
                    or self.operating_model_config.get('single_fault_fuel_curve')!=curve.as_dict()
                    or self.config.get('_sf2_fuel_curve')!=curve.as_dict()
                    or self.config.get('_sf2_nonactual_fuel_role')!='point_probe'
                    or self.config.get('_sf2_free_fuel_role')!='free_reference_probe'
                    or self.reference_initialization_solver is not reference_solver
                    or self._sf2_bound_reference_solver is not reference_solver
                    or type(self.reference_initialization_solver) is not partial
                    or self.reference_initialization_solver.func is not solve_hour
                    or self.reference_initialization_solver.args!=()
                    or self.reference_initialization_solver.keywords!={'sf2_fuel_config':curve.as_dict()}):
                raise ValueError('SF2 immutable model/curve changed before reset')
        def reset(self,*,scenario,seed=None):
            validate_single_fault_scenario(scenario)
            check(self)
            returned=old_reset(scenario=scenario,seed=seed)
            check(self)
            self.model_id=MODEL_ID;self.core._check_sf2_model()
            observation=self._observation()
            return (observation,returned[1]) if isinstance(returned,tuple) else observation
        env.reset=MethodType(reset,env);env.model_id=MODEL_ID
        env.episode_snapshot_function=decision_snapshot
        return env
    return SimpleNamespace(**dict(vars(original),OperatingHospitalIESEnv=create))


def decision_snapshot(env):
    from tools.single_fault_model_20261102.environment import decision_snapshot as inherited
    value=inherited(env)
    value['single_fault_model']=deepcopy(env.core.single_fault_metadata())
    value['physical_execution_model_id']=MODEL_ID
    return value
