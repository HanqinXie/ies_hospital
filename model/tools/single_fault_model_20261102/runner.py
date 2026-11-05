"""One canonical local single-fault case using the native evidence journal."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys
from types import MethodType

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT/'src')]

from tools.annual_readiness import runner as native
from tools.eight_fault_study.runner import _check_stopfile
from tools.eight_fault_study.runtime import create_source_context, runtime_configuration, with_local_runtime
from tools.model_correctness_v23.environment import isolated
from tools.single_fault_model_20261102.environment import decision_snapshot, with_single_fault_model
from tools.single_fault_model_20261102.runtime import MODEL_ID, ModelSpec, validate_single_fault_scenario

# Reuse the original code objects and all journal/failure handling. Only the
# explicit snapshot export changes; the controller and 15-minute loop do not.
_execute_loop = isolated(native._execute_loop, dict(native._execute_loop.__globals__, _snapshot=decision_snapshot))
_native_execute_attempt = isolated(native._execute_attempt,
    dict(native._execute_attempt.__globals__, _execute_loop=_execute_loop))


def _build_runtime(case, registry, output, writer, *, model_spec):
    from tools.model_correction_v25.environment import MODEL_ID as CONSTRUCTOR_MODEL_ID
    from tools.sci_engineering.design import SEALED_DAYS
    validate_single_fault_scenario(case['scenario'])
    meta = case['annual_readiness']
    configuration = runtime_configuration(case, registry)
    bindings = native._read(output/'BINDINGS.json')
    context = create_source_context(ROOT, case=case, configuration=configuration, bindings=bindings)
    original = with_single_fault_model(meta['method'], model_spec=model_spec,
        requalification_minutes=meta['parameters']['requalification_minutes'],
        overlay_spec=case.get('v27_load_overlay'), thermal_profile=case['v28_thermal_profile'])
    adapter = with_local_runtime(original, context)
    env = adapter.OperatingHospitalIESEnv(ROOT,
        allowed_hours=context['allowed_hours'], sealed_days=SEALED_DAYS,
        model_id=CONSTRUCTOR_MODEL_ID, model_config=deepcopy(configuration['model_config']),
        operating_rule=deepcopy(configuration['operating_rule']), solver_row_sidecar=writer,
        solver_evidence_directory=output/'domain_evidence', enable_all_validation_rows=True)
    policy = configuration['policy']
    policy_id = meta['method']+'_single_fault_sf1_20261102'
    controller = adapter.controller_factory(policy['alias'], config=policy['config'],
        plant=env.plant, policy_id=policy_id)
    native._write_new(output/'LOCAL_ACTUAL_WITNESS_SOURCE_CONTEXT.json', context)
    return env, controller, dict(model_config=deepcopy(configuration['model_config']),
        control_rule_id=env.control_rule_id, control_rule=deepcopy(env.control_rule),
        policy=dict(alias=policy['alias'], policy_id=getattr(controller, 'policy_id', policy_id),
                    config=deepcopy(controller.config)),
        local_runtime_adapter='single_fault_sf1_local_20261102',
        local_source_context_signature=context['signature'], annual_numeric_grant=False,
        single_fault_model=model_spec.as_dict(), physical_execution_model_id=MODEL_ID,
        recording_wrapper_model_id=native.MODEL_ID)


def _execute_attempt(output, *, model_spec, stopfile=None):
    from tools.annual_program.compiled_capture import capture_scope
    from tools.annual_program.witness import compiled_origin
    output = Path(output).resolve()
    stopfile = Path(stopfile).resolve() if stopfile is not None else None

    def build(case, registry, destination, writer):
        _check_stopfile(stopfile, 'before native construction')
        bindings = native._read(destination/'BINDINGS.json')
        # New scientific bytes must pass the existing explicit source profile.
        # No adapter, catch or hash translation can bypass this admission gate.
        origin = compiled_origin(ROOT, bindings['source'])
        env, controller, resolved = _build_runtime(case, registry, destination, writer, model_spec=model_spec)
        old_step = env.step

        def step(self, action=None):
            _check_stopfile(stopfile, 'before native step')
            with capture_scope(origin):
                return old_step(action)

        env.step = MethodType(step, env)
        return env, controller, resolved

    return _native_execute_attempt(output, runtime_builder=build)


def run_case(case, output, *, physical_max_segment_minutes=1., retain_history=False,
             case_input_binding=None, stopfile=None):
    spec = ModelSpec(physical_max_segment_minutes=physical_max_segment_minutes)
    validate_single_fault_scenario(case['scenario'])
    stopfile = Path(stopfile).resolve() if stopfile is not None else None
    case, output, bindings = native._prepare_attempt(case, output,
        retain_history=retain_history, case_input_binding=case_input_binding)
    native._write_new(output/'INVOCATION.json', dict(mode='single_fault_sf1_local_direct_callable',
        case_id=case['id'], retain_history=retain_history, runtime_builder=__name__+'._build_runtime',
        single_fault_model=spec.as_dict(), annual_numeric_grant=False,
        stopfile=str(stopfile) if stopfile is not None else None,
        stopfile_existence_only=True, stopfile_is_policy_input=False))
    native._write_new(output/'PHYSICAL_MODEL_BINDING.json', dict(
        schema='single_fault.physical_model_binding.v1', physical_model_spec=spec.as_dict(),
        recording_wrapper_model_id=native.MODEL_ID, physical_execution_model_id=MODEL_ID,
        scientific_source_binding=deepcopy(bindings['source']),
        old_source_byte_equivalence_claimed=False, original_canonical_case_unchanged=True,
        scientific_source_admission='Existing compiled_origin profile required before construction',
        control_interval_minutes=15., command_queue_and_delay_unchanged=True))
    with native._local_temp(output):
        return _execute_attempt(output, model_spec=spec, stopfile=stopfile)


def main(argv=None):
    from tools.sci_engineering.runner import sha
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case-file', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--physical-max-segment-minutes', type=float, choices=(1., .5), default=1.)
    parser.add_argument('--retain-history', action='store_true')
    parser.add_argument('--stopfile', type=Path)
    args = parser.parse_args(argv)
    case_file = args.case_file.resolve()
    result = run_case(native._read(case_file), args.output,
        physical_max_segment_minutes=args.physical_max_segment_minutes,
        retain_history=args.retain_history,
        case_input_binding=dict(path=str(case_file), sha256=sha(case_file)), stopfile=args.stopfile)
    print(json.dumps(dict(case_id=result['case_id'], status=result['status'],
        output=str(args.output.resolve())), ensure_ascii=False))
    return 0 if result['status'] == 'completed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
