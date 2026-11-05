"""One canonical local eight-fault case with the original complete-prefix loop.

No annual grant, annual dispatcher, resume path, online calendar, search or
training is invoked. Native preparation, input access, raw receipts, failures
and every legal observation/actual segment are retained by the existing runner.
"""
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
from tools.annual_readiness.runner import _execute_loop
from tools.eight_fault_study.runtime import (
    create_source_context, runtime_configuration, with_local_runtime)


class ControlledStopRequested(RuntimeError):
    """External existence-only stop, handled by the native failure journal."""
    category = 'controlled_stop_requested'


def _check_stopfile(path, phase):
    if path is not None and path.exists():
        raise ControlledStopRequested('Registered stop file present '+phase+': '+str(path))


def _build_runtime(case, registry, output, writer):
    from tools.annual_readiness.environment import with_readiness
    from tools.model_correction_v25.environment import MODEL_ID as CONSTRUCTOR_MODEL_ID
    from tools.sci_engineering.design import SEALED_DAYS
    meta = case['annual_readiness']
    configuration = runtime_configuration(case, registry)
    bindings = native._read(output/'BINDINGS.json')
    context = create_source_context(ROOT, case=case, configuration=configuration, bindings=bindings)
    original = with_readiness(meta['method'],
        requalification_minutes=meta['parameters']['requalification_minutes'],
        overlay_spec=case.get('v27_load_overlay'), thermal_profile=case['v28_thermal_profile'])
    adapter = with_local_runtime(original, context)
    env = adapter.OperatingHospitalIESEnv(ROOT,
        allowed_hours=context['allowed_hours'], sealed_days=SEALED_DAYS,
        model_id=CONSTRUCTOR_MODEL_ID, model_config=deepcopy(configuration['model_config']),
        operating_rule=deepcopy(configuration['operating_rule']), solver_row_sidecar=writer,
        solver_evidence_directory=output/'domain_evidence', enable_all_validation_rows=True)
    spec = configuration['policy']
    policy_id = meta['method']+'_eight_fault_local_20261102'
    controller = adapter.controller_factory(spec['alias'], config=spec['config'],
        plant=env.plant, policy_id=policy_id)
    native._write_new(output/'LOCAL_ACTUAL_WITNESS_SOURCE_CONTEXT.json', context)
    return env, controller, dict(model_config=deepcopy(configuration['model_config']),
        control_rule_id=env.control_rule_id, control_rule=deepcopy(env.control_rule),
        policy=dict(alias=spec['alias'], policy_id=getattr(controller,'policy_id',policy_id),
            config=deepcopy(controller.config)), local_runtime_adapter='eight_fault_local_20261102',
        local_source_context_signature=context['signature'], annual_numeric_grant=False)


def _execute_attempt(output, *, stopfile=None):
    from tools.annual_program.compiled_capture import capture_scope
    from tools.annual_program.witness import compiled_origin
    output = Path(output).resolve()
    stopfile = Path(stopfile).resolve() if stopfile is not None else None
    def build(case, registry, destination, writer):
        _check_stopfile(stopfile, 'before native construction')
        bindings = native._read(destination/'BINDINGS.json')
        # Keep origin/source errors inside the native attempt's original
        # failure journal, before constructing or parsing a native model.
        origin = compiled_origin(ROOT, bindings['source'])
        env, controller, resolved = _build_runtime(case, registry, destination, writer)
        original_step = env.step

        def step(self, action=None):
            _check_stopfile(stopfile, 'before native step')
            # Match the original annual capture lifetime exactly: reset,
            # initialization, legal snapshots and terminal exports stay out.
            with capture_scope(origin):
                return original_step(action)

        env.step = MethodType(step, env)
        return env, controller, resolved
    return native._execute_attempt(output, runtime_builder=build)


def run_case(case, output, *, retain_history=False, case_input_binding=None, stopfile=None):
    """Execute one local case; keep the public native reset/step APIs intact."""
    stopfile = Path(stopfile).resolve() if stopfile is not None else None
    case, output, bindings = native._prepare_attempt(case, output,
        retain_history=retain_history, case_input_binding=case_input_binding)
    native._write_new(output/'INVOCATION.json', dict(mode='eight_fault_local_direct_callable',
        case_id=case['id'], retain_history=retain_history, runtime_builder=__name__+'._build_runtime',
        annual_numeric_grant=False, stopfile=str(stopfile) if stopfile is not None else None,
        stopfile_existence_only=True, stopfile_is_policy_input=False))
    with native._local_temp(output):
        return _execute_attempt(output, stopfile=stopfile)


def main(argv=None):
    from tools.sci_engineering.runner import sha
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case-file', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--retain-history', action='store_true')
    parser.add_argument('--stopfile', type=Path,
        help='Existence-only external stop before native construction or the next native step')
    args = parser.parse_args(argv)
    case_file = args.case_file.resolve()
    result = run_case(native._read(case_file), args.output, retain_history=args.retain_history,
        case_input_binding=dict(path=str(case_file), sha256=sha(case_file)),
        stopfile=args.stopfile.resolve() if args.stopfile is not None else None)
    print(json.dumps(dict(case_id=result['case_id'], status=result['status'],
        output=str(args.output.resolve())), ensure_ascii=False))
    return 0 if result['status'] == 'completed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
