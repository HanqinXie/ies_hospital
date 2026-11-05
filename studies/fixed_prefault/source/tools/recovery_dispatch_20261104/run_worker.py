"""Reuse the canonical worker with a named recovery policy attached before reset.

No physical step, reset, serialization, source input or native solver is copied
or changed. The intervention is separate from the byte-identical original case.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import os
from pathlib import Path
import sys
import traceback

SOURCE = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(SOURCE), str(SOURCE / 'src')]
from tools.cooling_dynamics_20261104 import run_worker as native
from tools.cooling_dynamics_20261104.extract_results import read, write, sha
from tools.recovery_dispatch_20261104.run_campaign import append, check_pin, safe_new_root, utc, validate_registration

_native_build_runtime = native.build_runtime
_native_verify_binding = native.verify_binding


def build_runtime(case, source_freeze, cooling_config, *, intervention_config):
    from tools.recovery_dispatch_20261104.policy import attach_recovery_policy
    env, controller, resolved = _native_build_runtime(case, source_freeze, cooling_config)
    original_policy = deepcopy(resolved['policy'])
    controller = attach_recovery_policy(env, controller, config=deepcopy(intervention_config))
    resolved['recovery_dispatch_intervention'] = dict(config=deepcopy(intervention_config),
        attachment='after_original_build_runtime_before_original_reset',
        baseline_policy=original_policy, exact_baseline_case_bytes_retained=True,
        original_policy_field_is_unchanged=True,
        original_raw_optimizer_diagnostic_retained=True,
        command_selection_receipt_location='core queued command.joint_dispatch.diagnostic.recovery_dispatch_selection')
    return env, controller, resolved


def verify_binding(root):
    root = safe_new_root(root)
    if native.SOURCE != SOURCE:
        raise ValueError('Canonical worker resolves a different source root')
    binding = _native_verify_binding(root)
    registration = read(root / 'REGISTRATION.json')
    validate_registration(registration)
    from tools.recovery_dispatch_20261104.policy import R1_CONFIG
    if registration['source_root'] != str(SOURCE) or registration['intervention'] != R1_CONFIG or binding['intervention'] != R1_CONFIG:
        raise ValueError('Named R1 intervention/source identity changed')
    for row in binding['external_dependency_files']:
        check_pin(row)
    for item in registration['producers']:
        if sha(item['case_file']) != item['case_sha256']:
            raise ValueError('Registered exact baseline case changed')
    return binding


def run(root, label, attempt=1):
    root = safe_new_root(root)
    registration = read(root / 'REGISTRATION.json')
    validate_registration(registration)
    if label not in {item['label'] for item in registration['producers']}:
        raise ValueError('Producer is outside the two registered R1 cases')
    config = deepcopy(registration['intervention'])
    previous_build, previous_verify = native.build_runtime, native.verify_binding
    native.build_runtime = lambda case, source_freeze, cooling_config: build_runtime(
        case, source_freeze, cooling_config, intervention_config=config)
    native.verify_binding = verify_binding
    try:
        # Native run owns output creation, reset at zero, all 180 one-minute
        # decisions/steps, serialization, resource guards and failure receipts.
        rc = native.run(root, label, attempt)
    finally:
        native.build_runtime, native.verify_binding = previous_build, previous_verify
    output = root / 'runs' / (label + '_attempt' + str(attempt).zfill(2))
    result = read(output / 'RESULT.json')
    result['base_execution_model_id'] = result.get('execution_model_id')
    result['execution_model_id'] = 'minute1_cooling_dynamics_recovery_dispatch_20261104'
    result['recovery_dispatch_intervention'] = dict(config=config, baseline_label=next(
        item['baseline_label'] for item in registration['producers'] if item['label'] == label),
        registration_sha256=sha(root / 'REGISTRATION.json'), case_bytes_do_not_encode_intervention=True)
    write(output / 'RESULT.json', result)
    return rc


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    parser.add_argument('--label', required=True)
    parser.add_argument('--attempt', type=int, default=1)
    args = parser.parse_args()
    root = safe_new_root(args.root)
    invocation = dict(command=[sys.executable, '-m', 'tools.recovery_dispatch_20261104.run_worker'] + sys.argv[1:],
                      pid=os.getpid(), cwd=str(Path.cwd()), started_utc=utc())
    try:
        rc = run(root, args.label, args.attempt)
    except Exception as exc:
        invocation.update(error=dict(type=type(exc).__name__, message=str(exc), traceback=traceback.format_exc()))
        traceback.print_exc()
        rc = 1
    invocation.update(exit_code=rc, ended_utc=utc())
    append(root / 'logs/WORKER_COMMANDS.jsonl', invocation)
    return rc


if __name__ == '__main__':
    raise SystemExit(main())
