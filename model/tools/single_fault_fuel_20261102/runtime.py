"""Explicit SF2 fuel paths and accepted-segment accounting over unchanged SF1 stores."""
from copy import deepcopy
from dataclasses import asdict,dataclass
from numbers import Real
from functools import partial
from types import FunctionType
import math
import numpy as np

from . import fuel,leaf
from .fuel import FuelCurve,FuelPath,CONTRACT,digest
from hospital_ies import nonlinear,solver_backend,model
from tools.operating_response import physical_balance
from tools.sci_engineering import planner
from tools.single_fault_model_20261102.runtime import INTEGRATION_CONTRACT,validate_single_fault_scenario

MODEL_ID='SF2_PNNL_fuel_single_fault_20261102'
PHYSICAL_REVISION='single_fault.physical_revision.sf2.v1'
PLANNER_CONTRACT='hospital_ies.sf2.exact_power_graph.v1'


def accepted_actual_attempt(attempt, profile):
    """Accept honest numerical ownership; never turn a cache proof into OPTIMAL."""
    if type(attempt.get('status')) is not int or attempt['status']!=0 or attempt.get('error') is not None:
        return False
    if profile is None:
        return attempt.get('native_status')==2
    from hospital_ies.engineering_numerics import engineering_profile
    if profile!=engineering_profile():return False
    proof=attempt.get('engineering_acceptance',{})
    if (proof.get('schema')!='hospital_ies.engineering_acceptance.v1'
            or proof.get('profile')!=profile['profile'] or proof.get('original_domain_checked') is not True
            or proof.get('physical_state_replaced') is not False
            or proof.get('validation_thresholds')!=profile['tolerances']
            or len(proof.get('current_domain_sha256',''))!=64):return False
    check=proof.get('validation_summary',{});tol=profile['tolerances']
    for field,limit in (('max_original_violation',tol['physical_rows_bounds']),
            ('max_lock_violation',tol['normalized_locks']),('max_integrality_distance',tol['integrality'])):
        value=check.get(field)
        if type(value) not in (int,float) or not math.isfinite(value) or value>limit:return False
    quality=proof.get('optimality',{})
    if quality.get('previous_problem_bound_used') is not False:return False
    if attempt.get('optimizer_called') is True:
        return (attempt.get('native_status')==2 and proof.get('mode')=='native_optimization'
                and quality.get('native_optimal_status_claimed') is True)
    gap=quality.get('gap_upper_bound')
    return (attempt.get('optimizer_called') is False and attempt.get('native_status') is None
        and proof.get('mode') in ('current_domain_natural_bound','current_domain_lp_dual_bound')
        and quality.get('native_optimal_status_claimed') is False
        and type(gap) in (int,float) and math.isfinite(gap)
        and 0.<=gap<=profile['objective_bound_abs_tolerance'])


@dataclass(frozen=True)
class ModelSpec:
    physical_model_id: str=MODEL_ID
    physical_revision: str=PHYSICAL_REVISION
    storage_integration_contract: str=INTEGRATION_CONTRACT
    running_fuel_contract: str=CONTRACT
    planner_fuel_contract: str=PLANNER_CONTRACT
    physical_max_segment_minutes: float=1.

    def __post_init__(self):
        if (self.physical_model_id!=MODEL_ID or self.physical_revision!=PHYSICAL_REVISION
                or self.storage_integration_contract!=INTEGRATION_CONTRACT
                or self.running_fuel_contract!=CONTRACT or self.planner_fuel_contract!=PLANNER_CONTRACT):
            raise ValueError('Explicit immutable loaded SF2 contracts required')
        value=self.physical_max_segment_minutes
        if isinstance(value,bool) or not isinstance(value,Real) or value not in (1.,.5):
            raise ValueError('SF2 physical segments must be 1 or .5 minutes; control remains 15 minutes')

    def as_dict(self):return asdict(self)


_FUNCTIONS=tuple((owner,name,getattr(owner,name)) for owner,names in (
    (fuel,('number','digest')),(leaf,('prepare','fixed_coefficients','receipt')),
    (nonlinear,('pricing_shadow','evaluate_nonlinear','exact_solve_layer','emit_nonlinear_domain','complete_nonlinear_call',
        '_canonical','_domain','_evidence_error','nonlinear_evidence_scope')),
    (solver_backend,('milp','_gurobi_attempt','_evaluate')),
    (physical_balance,('solve_physical',)),(planner,('_solve_joint_candidate','solve_joint')),
    (model,('solve_hour','gt_level_properties')),
) for name in names)
_METHODS=tuple((owner,name,getattr(owner,name)) for owner,names in (
    (ModelSpec,('as_dict','__post_init__')),
    (FuelCurve,('coefficient','rate','integral','qualification','as_dict')),
    (FuelPath,('integrals',)),(nonlinear.NonlinearSpec,('validate_domain','as_dict')),
    (nonlinear.NonlinearSourceContext,('as_dict','__post_init__')),
) for name in names)
_ALIASES=((physical_balance,'milp',solver_backend.milp),(planner,'milp',solver_backend.milp),
    (physical_balance,'gt_level_properties',model.gt_level_properties),(planner,'gt_level_properties',model.gt_level_properties),
    (leaf,'FuelCurve',FuelCurve),(leaf,'FuelPath',FuelPath),
    (nonlinear,'_EVIDENCE_SINK',nonlinear._EVIDENCE_SINK),(nonlinear,'_LAYER_CONTEXT',nonlinear._LAYER_CONTEXT))
_DESCRIPTORS=tuple((owner,name,vars(owner)[name]) for owner,names in (
    (FuelCurve,('__init__','__post_init__','from_source','from_config','identity','exponent')),
    (FuelPath,('__init__','__post_init__','build','identity','hours')),
    (nonlinear.PowerLawEquality,('__init__','__post_init__')),
    (nonlinear.NonlinearSpec,('__init__','__post_init__','identity')),
    (ModelSpec,('__init__','__setattr__')),
    (nonlinear.NonlinearSourceContext,('__init__','build','__setattr__')),
) for name in names)
_TYPES=((fuel,'FuelCurve',FuelCurve),(fuel,'FuelPath',FuelPath),
    (nonlinear,'NonlinearSpec',nonlinear.NonlinearSpec),(nonlinear,'PowerLawEquality',nonlinear.PowerLawEquality),
    (nonlinear,'NonlinearSourceContext',nonlinear.NonlinearSourceContext))
_ACTUAL_PHASES=frozenset(('fixed_execution','after_actual_shedding','local_event_split',
    'diesel_domain_split','protection_threshold_split','m23_event_power_probe'))


class FuelCoreMixin:
    def __init__(self,env,initial_action):
        # Only JSON state lives on the physical core. Typed immutable path
        # values are ephemeral return-only arguments, never mutable history.
        self._sf2_spec_value=env.single_fault_fuel_spec.as_dict()
        self._sf2_curve_value=env.single_fault_fuel_curve.as_dict()
        super().__init__(env,initial_action)
        self._check_sf2_model()
        self.log('sf2_running_fuel_model_installed',**self.single_fault_metadata(),
            startup_or_OFF_fuel_inferred=False,inventory_reset=False)

    def _check_sf2_model(self):
        e=self.env;spec=e.single_fault_fuel_spec;curve=e.single_fault_fuel_curve
        if (type(spec) is not ModelSpec or spec is not e._single_fault_fuel_bound_spec
                or spec.as_dict()!=self._sf2_spec_value
                or e.operating_model_config.get('single_fault_fuel_model')!=self._sf2_spec_value
                or e.operating_model_config.get('single_fault_fuel_curve')!=self._sf2_curve_value
                or type(curve) is not FuelCurve or curve is not e._single_fault_fuel_bound_curve
                or curve.as_dict()!=self._sf2_curve_value or e.config.get('_sf2_fuel_curve')!=self._sf2_curve_value
                or e.config.get('_sf2_nonactual_fuel_role')!='point_probe'
                or e.config.get('_sf2_free_fuel_role')!='free_reference_probe'):
            raise ValueError('SF2 frozen source fuel/specification changed')
        if (any(getattr(owner,name) is not loaded for owner,name,loaded in _FUNCTIONS+_METHODS+_ALIASES+_TYPES)
                or any(vars(owner).get(name) is not loaded for owner,name,loaded in _DESCRIPTORS)):
            raise ValueError('SF2 loaded fuel/path/planner/backend method changed')
        if any(fn.__code__ is not code or fn.__defaults__!=defaults or fn.__kwdefaults__!=kwdefaults
                for fn,code,defaults,kwdefaults in _CODE_GUARDS):
            raise ValueError('SF2 loaded function code/defaults changed')
        reference=e.reference_initialization_solver
        if (reference is not e._sf2_bound_reference_solver or type(reference) is not partial
                or reference.func is not model.solve_hour or reference.args!=()
                or reference.keywords!={'sf2_fuel_config':self._sf2_curve_value}):
            raise ValueError('SF2 frozen reference initialization hook changed')
        for name,loaded in _CORE_METHODS.items():
            # The source-locked local witness wrapper legitimately precedes
            # the physical mixin for commit/boundary hooks. Do not replace or
            # bypass its final native-witness call to force a bound-method match.
            actual=getattr(self,name)
            if (getattr(FuelCoreMixin,name) is not loaded or name in vars(self)
                    or name in ('_local_interval_action','_solve','single_fault_metadata','_check_sf2_model')
                    and getattr(actual,'__func__',actual) is not loaded):
                raise ValueError('SF2 loaded core fuel method changed: '+name)

    def single_fault_metadata(self):
        self._check_sf2_model()
        value=super().single_fault_metadata()
        value.update(deepcopy(self._sf2_spec_value))
        value.update(running_fuel_curve_identity=self.env.single_fault_fuel_curve.identity,
            actual_running_fuel_scope=self.env.single_fault_fuel_curve.qualification())
        return value

    def _local_interval_action(self,next_time,f,row):
        self._check_sf2_model()
        action,start,end=super()._local_interval_action(next_time,f,row)
        action['_sf2_fuel_path']=FuelPath.build(self.env.single_fault_fuel_curve,role='actual_interval',
            start_minute=float(self.time),end_minute=float(next_time),temperature_C=float(row.outdoor_temperature_C),
            start_kW=start,end_kW=end)
        return action,start,end

    def _solve(self,loads,f,dt_h,phase,action,**kwargs):
        self._check_sf2_model()
        fixed=kwargs.get('execution_action')
        path=fixed.get('_sf2_fuel_path') if isinstance(fixed,dict) else None
        if path is not None:
            if phase not in _ACTUAL_PHASES or type(path) is not FuelPath or path.role!='actual_interval':
                raise ValueError('SF2 actual path has a nonactual or unknown role')
            _,row,_,_=self.data_at(self.time)
            expected,_,_=self._local_interval_action(self.time+dt_h*60.,f,row)
            if path!=expected['_sf2_fuel_path'] or fixed!=expected:
                raise ValueError('SF2 actual path differs from lawful unchanged source start/end/mean')
            kwargs['execution_action']=dict(fixed);kwargs['execution_action'].pop('_sf2_fuel_path')
            kwargs.update(sf2_fuel_path=path,sf2_fuel_role='actual_interval')
        elif phase in _ACTUAL_PHASES:
            raise ValueError('SF2 actual solve requires a lawful return-only path')
        else:
            kwargs.update(sf2_fuel_role='point_probe')
        return super()._solve(loads,f,dt_h,phase,action,**kwargs)

    def boundary(self):
        self._check_sf2_model();return super().boundary()

    def _next_boundary(self,end):
        self._check_sf2_model();return super()._next_boundary(end)

    def observe(self):
        self._check_sf2_model();return super().observe()

    def _after_actual_segment(self,segment,result):
        self._check_sf2_model()
        meter=deepcopy(result.get('_sf2_fuel_receipt'))
        if (not isinstance(meter,dict) or meter.get('schema')!='single_fault.sf2.running_fuel_receipt.v1'
                or meter.get('role')!='actual_interval' or meter.get('accounting_committed') is not False
                or meter.get('curve_identity')!=self.env.single_fault_fuel_curve.identity):
            raise ValueError('SF2 accepted actual segment requires its uncommitted source/primal receipt')
        context=meter.get('path_context')
        if not isinstance(context,dict):raise ValueError('SF2 accepted segment missing actual path')
        context=dict(context)
        for name in ('start_kW','end_kW','mean_kW'):context[name]=tuple(context[name])
        path=FuelPath(**context);raw=meter.get('accepted_raw_x')
        layers=result.get('solver_backend_layers') or []
        final=layers[-1] if layers else {}
        accepted=[(i,value) for i,value in enumerate(final.get('attempts',[])) if value.get('accepted') is True]
        if (final.get('layer')!='cost' or final.get('backend')!='gurobipy'
                or type(final.get('status')) is not int or final.get('status')!=0
                or len(accepted)!=1 or type(accepted[0][1].get('status')) is not int or accepted[0][1].get('status')!=0
                or not accepted_actual_attempt(accepted[0][1],getattr(self.env,'engineering_numerical_profile',None))
                or type(raw) is not list or type(accepted[0][1].get('raw_x')) is not list
                or digest(raw)!=digest(accepted[0][1]['raw_x'])):
            raise ValueError('SF2 receipt complete primal differs from unique final accepted native result owner')
        if (path.identity!=meter.get('path_identity') or path.start_minute!=segment['start_minute']
                or path.end_minute!=segment['end_minute'] or tuple(segment['source_start_gt_kW'])!=path.start_kW
                or tuple(segment['source_end_gt_kW'])!=path.end_kW or meter.get('accepted_raw_x_sha256')!=digest(raw)
                or meter.get('native_vector_dimension')!=len(raw) or len(raw) not in (47,48)
                or not np.allclose(raw[:3],path.mean_kW,rtol=0.,atol=1e-7)
                or not np.allclose(segment['action']['gt_electric_kW'],raw[:3],rtol=0.,atol=1e-7)):
            raise ValueError('SF2 accepted segment path/start/end/mean/native primal mismatch')
        expected=leaf.receipt(self.env.single_fault_fuel_curve,path,'actual_interval',raw,path.hours,
            path.temperature_C,self.env.plant.gas_price_usd_per_mmbtu)
        if (meter['running_fuel_by_unit_MMBtu']!=expected['running_fuel_by_unit_MMBtu']
                or meter['running_gas_fee_USD']!=expected['running_gas_fee_USD']
                or meter['running_fuel_MMBtu']!=expected['running_fuel_MMBtu']
                or not math.isclose(segment['action']['gt_fuel_MMBtu_h'],sum(expected['average_running_fuel_MMBtu_h']),rel_tol=1e-12,abs_tol=1e-12)):
            raise ValueError('SF2 actual action meter differs from source path integral')
        super()._after_actual_segment(segment,result)
        meter['accounting_committed']=True
        meter['commit_basis']='accepted actual segment only; final solver-owned primal and lawful path checked'
        meter['native_result_owner']=dict(result_layer_index=len(layers)-1,accepted_attempt_index=accepted[0][0],
            layer='cost',backend='gurobipy',full_raw_x_sha256=digest(accepted[0][1]['raw_x']),
            full_vector_exact_identity=True,primal_selected_by_GP_or_action_matching=False)
        if getattr(self.env,'engineering_numerical_profile',None) is not None:
            meter['native_result_owner'].update(optimizer_called=accepted[0][1]['optimizer_called'],
                engineering_acceptance=deepcopy(accepted[0][1]['engineering_acceptance']),
                native_optimal_status_claimed=accepted[0][1]['native_status']==2)
        segment['sf2_running_fuel']=meter
        diesel=segment.get('diesel_local_response',{})
        shaft_adjustment=diesel.get('shaft_equivalent_fee_USD',0.)-diesel.get('original_electric_fee_USD',0.)
        segment['sf2_physical_operating_fee_USD_step']=result['sf2_physical_operating_fee_USD_step']+shaft_adjustment
        segment['sf2_physical_operating_fee_basis']='grid + modeled GT/boiler gas + .30 USD per modeled diesel shaft kWh; no artificial tie/startup gas/chemical diesel claim'
        segment['sf2_artificial_unused_steam_tie_USD_step']=result['sf2_artificial_unused_steam_tie_USD_step']
        segment['single_fault_model']=self.single_fault_metadata()


_CORE_METHODS={name:getattr(FuelCoreMixin,name) for name in
    ('_local_interval_action','_solve','single_fault_metadata','_after_actual_segment','_check_sf2_model','boundary','_next_boundary','observe')}


def _protected_functions():
    values=[value for _,_,value in _FUNCTIONS+_METHODS+_ALIASES+_DESCRIPTORS]+list(_CORE_METHODS.values())
    values.append(nonlinear.nonlinear_evidence_scope.__wrapped__)
    functions=[]
    for value in values:
        if isinstance(value,(classmethod,staticmethod)):value=value.__func__
        parts=(value.fget,value.fset,value.fdel) if isinstance(value,property) else (value,)
        for fn in parts:
            if isinstance(fn,FunctionType) and fn not in functions:functions.append(fn)
    return tuple(functions)


_CODE_GUARDS=tuple((fn,fn.__code__,deepcopy(fn.__defaults__),deepcopy(fn.__kwdefaults__)) for fn in _protected_functions())
