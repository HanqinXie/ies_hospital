"""Explicit SF2 local runner with complete NL scope and final actual witness.

Only a fresh caller-supplied canonical case is executed. Source matching alone
never selects SF2 physics or grants annual/final-blind numerical access.
"""
from __future__ import annotations
import argparse
from contextlib import ExitStack
from copy import deepcopy
from pathlib import Path
import json
import sys
from types import MethodType

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]

from hospital_ies.nonlinear import nonlinear_evidence_scope
from tools.annual_readiness import runner as native
from tools.eight_fault_study.runner import _check_stopfile
from tools.eight_fault_study.runtime import create_source_context,runtime_configuration,with_local_runtime
from tools.model_correctness_v23.environment import isolated
from tools.single_fault_model_20261102.runtime import ModelSpec as StorageSpec
from tools.single_fault_fuel_20261102.environment import with_single_fault_fuel,decision_snapshot
from tools.single_fault_fuel_20261102.fuel import FuelCurve
from tools.single_fault_fuel_20261102.runtime import MODEL_ID,ModelSpec,FuelCoreMixin,validate_single_fault_scenario
from tools.single_fault_fuel_20261102.nl_store import NonlinearStore

_execute_loop=isolated(native._execute_loop,dict(native._execute_loop.__globals__,_snapshot=decision_snapshot))
_native_execute_attempt=isolated(native._execute_attempt,dict(native._execute_attempt.__globals__,_execute_loop=_execute_loop))


def _require_sf2_binding(env,spec):
    curve=getattr(env,'single_fault_fuel_curve',None)
    config=getattr(env,'operating_model_config',{})
    if (getattr(env,'single_fault_fuel_spec',None) is not spec
            or getattr(env,'_single_fault_fuel_bound_spec',None) is not spec
            or type(curve) is not FuelCurve or getattr(env,'_single_fault_fuel_bound_curve',None) is not curve
            or curve.as_dict()!=FuelCurve.from_source(ROOT,env.plant).as_dict()
            or config.get('single_fault_fuel_model')!=spec.as_dict()
            or config.get('single_fault_fuel_curve')!=curve.as_dict()
            or config.get('single_fault_model')!=StorageSpec(physical_max_segment_minutes=spec.physical_max_segment_minutes).as_dict()
            or env.config.get('_sf2_fuel_curve')!=curve.as_dict()
            or env.config.get('_sf2_nonactual_fuel_role')!='point_probe'
            or env.config.get('_sf2_free_fuel_role')!='free_reference_probe'
            or FuelCoreMixin not in env.engineering_core_type.__mro__ or env.model_id!=MODEL_ID):
        raise ValueError('SF2 runner requires its actual immutable spec/curve/config/core mode before reset')


def _build_runtime(case,registry,output,writer,*,model_spec):
    from tools.model_correction_v25.environment import MODEL_ID as CONSTRUCTOR_MODEL_ID
    from tools.sci_engineering.design import SEALED_DAYS
    validate_single_fault_scenario(case['scenario']);meta=case['annual_readiness']
    configuration=runtime_configuration(case,registry);bindings=native._read(output/'BINDINGS.json')
    context=create_source_context(ROOT,case=case,configuration=configuration,bindings=bindings)
    original=with_single_fault_fuel(meta['method'],model_spec=model_spec,
        requalification_minutes=meta['parameters']['requalification_minutes'],
        overlay_spec=case.get('v27_load_overlay'),thermal_profile=case['v28_thermal_profile'])
    adapter=with_local_runtime(original,context)
    env=adapter.OperatingHospitalIESEnv(ROOT,allowed_hours=context['allowed_hours'],sealed_days=SEALED_DAYS,
        model_id=CONSTRUCTOR_MODEL_ID,model_config=deepcopy(configuration['model_config']),
        operating_rule=deepcopy(configuration['operating_rule']),solver_row_sidecar=writer,
        solver_evidence_directory=output/'domain_evidence',enable_all_validation_rows=True)
    _require_sf2_binding(env,model_spec)
    policy=configuration['policy'];policy_id=meta['method']+'_single_fault_sf2_20261102'
    controller=adapter.controller_factory(policy['alias'],config=policy['config'],plant=env.plant,policy_id=policy_id)
    native._write_new(output/'LOCAL_ACTUAL_WITNESS_SOURCE_CONTEXT.json',context)
    return env,controller,dict(model_config=deepcopy(env.operating_model_config),control_rule_id=env.control_rule_id,
        control_rule=deepcopy(env.control_rule),policy=dict(alias=policy['alias'],policy_id=getattr(controller,'policy_id',policy_id),config=deepcopy(controller.config)),
        local_runtime_adapter='single_fault_sf2_local_20261102',local_source_context_signature=context['signature'],
        annual_numeric_grant=False,single_fault_model=StorageSpec(physical_max_segment_minutes=model_spec.physical_max_segment_minutes).as_dict(),
        single_fault_fuel_model=model_spec.as_dict(),physical_execution_model_id=MODEL_ID,recording_wrapper_model_id=native.MODEL_ID)


def _execute_attempt(output,*,model_spec,stopfile=None):
    from tools.annual_program.compiled_capture import capture_scope
    from tools.annual_program.witness import compiled_origin
    output=Path(output).resolve();stopfile=Path(stopfile).resolve() if stopfile is not None else None
    if (output/'RESULT.json').exists() or (output/'SUMMARY.json').exists():raise ValueError('Fresh SF2 final outputs required')
    saved=native._read(output/'CASE.json');bindings=native._read(output/'BINDINGS.json')
    store=NonlinearStore(output,bindings=bindings,case_id=saved['case']['id'],physical_model_spec=model_spec.as_dict(),
        storage_model_spec=StorageSpec(physical_max_segment_minutes=model_spec.physical_max_segment_minutes).as_dict(),
        defer_directory_initialization=True)
    pending={};session=None;result=None;error=None
    def defer(path,value,**kwargs):
        path=Path(path)
        if path.parent.resolve()==output and path.name in ('RESULT.json','SUMMARY.json'):
            if path.name in pending:raise ValueError('Duplicate native final SF2 output')
            pending[path.name]=deepcopy(value);return
        return native._write_new(path,value,**kwargs)
    execute=isolated(_native_execute_attempt,dict(_native_execute_attempt.__globals__,_write_new=defer))
    try:
        with nonlinear_evidence_scope(store) as session,ExitStack() as capture:
            def build(case,registry,destination,writer):
                _check_stopfile(stopfile,'before SF2 native construction')
                origin=compiled_origin(ROOT,bindings['source'])
                from tools.annual_program.flow_source_contracts import SCI_FUEL_CAPTURE_PROFILE
                from tools.annual_program.witness import native_source_binding
                if native_source_binding(ROOT)['source_profile']!=SCI_FUEL_CAPTURE_PROFILE:
                    raise ValueError('SF2 runner requires the exact reviewed SCI_FUEL source profile')
                capture.enter_context(capture_scope(origin))
                env,controller,resolved=_build_runtime(case,registry,destination,writer,model_spec=model_spec)
                old_step=env.step
                def step(self,action=None):
                    _check_stopfile(stopfile,'before SF2 native step');return old_step(action)
                env.step=MethodType(step,env)
                return env,controller,resolved
            result=execute(output,runtime_builder=build)
    except BaseException as exc:
        error=native._error(exc)
    result=deepcopy(result if isinstance(result,dict) else pending.get('RESULT.json',dict(case_id=saved['case']['id'],status='failed')))
    if error is not None:result.update(status='failed',error=error)
    try:
        evidence=store.finish(session)
        if session.get('status')!='closed_completed':
            raise ValueError('SF2 nonlinear core scope did not close completed')
    except Exception as exc:
        previous=result.get('error');failure=native._error(exc)
        if previous is not None:failure['native_error']=previous
        result.update(status='failed',error=failure)
        try:evidence=store.receipt(session)
        except Exception:evidence=None
    result['sf2_nonlinear_evidence']=evidence
    # Native writes are deliberately deferred: neither callback nor SESSION
    # failure can leave an already-published completed RESULT or SUMMARY.
    native._write_new(output/'RESULT.json',result)
    summary=pending.get('SUMMARY.json',{})
    summary={key:result.get(key) for key in summary} if summary else dict(case_id=result.get('case_id'),status=result['status'],error=result.get('error'))
    native._write_new(output/'SUMMARY.json',summary)
    return result


def run_case(case,output,*,physical_max_segment_minutes=1.,retain_history=False,case_input_binding=None,stopfile=None):
    spec=ModelSpec(physical_max_segment_minutes=physical_max_segment_minutes)
    storage=StorageSpec(physical_max_segment_minutes=physical_max_segment_minutes)
    validate_single_fault_scenario(case['scenario']);stopfile=Path(stopfile).resolve() if stopfile is not None else None
    case,output,bindings=native._prepare_attempt(case,output,retain_history=retain_history,case_input_binding=case_input_binding)
    native._write_new(output/'INVOCATION.json',dict(mode='single_fault_sf2_local_direct_callable',case_id=case['id'],
        retain_history=retain_history,runtime_builder=__name__+'._build_runtime',single_fault_model=storage.as_dict(),
        single_fault_fuel_model=spec.as_dict(),annual_numeric_grant=False,stopfile=str(stopfile) if stopfile else None,
        stopfile_existence_only=True,stopfile_is_policy_input=False))
    native._write_new(output/'PHYSICAL_MODEL_BINDING.json',dict(schema='single_fault.sf2.physical_model_binding.v1',
        physical_model_spec=spec.as_dict(),storage_model_spec=storage.as_dict(),recording_wrapper_model_id=native.MODEL_ID,
        physical_execution_model_id=MODEL_ID,scientific_source_binding=deepcopy(bindings['source']),
        old_source_byte_equivalence_claimed=False,original_canonical_case_unchanged=True,
        scientific_source_admission='Exact SCI_FUEL source plus actual immutable SF2 spec/config/curve/core and complete NL scope required',
        control_interval_minutes=15.,command_queue_and_delay_unchanged=True))
    with native._local_temp(output):return _execute_attempt(output,model_spec=spec,stopfile=stopfile)


def main(argv=None):
    from tools.sci_engineering.runner import sha
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case-file',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--physical-max-segment-minutes',type=float,choices=(1.,.5),default=1.)
    parser.add_argument('--retain-history',action='store_true');parser.add_argument('--stopfile',type=Path)
    args=parser.parse_args(argv);case_file=args.case_file.resolve()
    result=run_case(native._read(case_file),args.output,physical_max_segment_minutes=args.physical_max_segment_minutes,
        retain_history=args.retain_history,case_input_binding=dict(path=str(case_file),sha256=sha(case_file)),stopfile=args.stopfile)
    print(json.dumps(dict(case_id=result['case_id'],status=result['status'],output=str(args.output.resolve()))))
    return 0 if result['status']=='completed' else 1

if __name__=='__main__':raise SystemExit(main())
