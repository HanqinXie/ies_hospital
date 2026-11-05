"""One R2 native trajectory; native RESULT is immutable, extensions use sidecar."""
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
from tools.recovery_dispatch_20261104.run_campaign import append, check_pin, pin, utc
from tools.recovery_steam_20261104.run_campaign import safe_new_root, validate_registration

_native_build_runtime = native.build_runtime
_native_verify_binding = native.verify_binding


def build_runtime(case, source_freeze, cooling_config, *, intervention_config):
    from tools.recovery_steam_20261104.policy import attach_recovery_policy
    env, controller, resolved = _native_build_runtime(case, source_freeze, cooling_config)
    controller = attach_recovery_policy(env, controller, config=deepcopy(intervention_config))
    resolved['recovery_dispatch_intervention'] = dict(config=deepcopy(intervention_config),
        attachment='after_original_build_runtime_before_original_reset',
        baseline_policy=deepcopy(resolved['policy']), exact_baseline_case_bytes_retained=True,
        original_policy_field_is_unchanged=True, original_raw_optimizer_diagnostic_retained=True,
        command_selection_receipt_location='command.joint_dispatch.diagnostic.recovery_steam_selection')
    return env, controller, resolved


def verify_binding(root):
    root = safe_new_root(root)
    if native.SOURCE != SOURCE:
        raise ValueError('Canonical worker resolves a different source checkout')
    binding = _native_verify_binding(root)
    registration = read(root / 'REGISTRATION.json')
    validate_registration(registration)
    from tools.recovery_steam_20261104.policy import R2_CONFIG
    if registration['source_root'] != str(SOURCE) or registration['intervention'] != R2_CONFIG or binding['intervention'] != R2_CONFIG:
        raise ValueError('Registered R2 intervention/source identity changed')
    for row in binding['external_dependency_files']:
        check_pin(row)
    return binding


def run(root, label='T05_R2', attempt=1):
    root = safe_new_root(root)
    registration = read(root / 'REGISTRATION.json')
    validate_registration(registration)
    if label != 'T05_R2':
        raise ValueError('Only the registered T05_R2 trajectory is authorized')
    config = deepcopy(registration['intervention'])
    previous_build, previous_verify = native.build_runtime, native.verify_binding
    native.build_runtime = lambda case, source_freeze, cooling_config: build_runtime(
        case, source_freeze, cooling_config, intervention_config=config)
    native.verify_binding = verify_binding
    try:
        rc = native.run(root, label, attempt)
    finally:
        native.build_runtime, native.verify_binding = previous_build, previous_verify
    if rc == 0:
        output = root / 'runs' / (label + '_attempt' + str(attempt).zfill(2))
        result_path = output / 'RESULT.json'
        before = result_path.read_bytes()
        result = read(result_path)
        if result.get('status') != 'completed' or result.get('observed_until_minute') != 180. or result.get('label') != label:
            raise ValueError('Native completion does not cover the registered R2 trajectory')
        write(output / 'WRAPPER_COMPLETION.json', dict(schema='hospital_ies.recovery_steam.wrapper_completion.v1',
            label=label, attempt=attempt, producer=str(output), created_utc=utc(),
            native_result=pin(result_path), source_binding=pin(root / 'SOURCE_BINDING.json'),
            registration=pin(root / 'REGISTRATION.json'), intervention=config, native_return_code=rc,
            wrapper_status='native_returned_completed', native_observed_until_minute=180.,
            process_exit_code_is_recorded_by_supervisor=True, native_result_bytes_preserved=True,
            result_metadata_extension_is_sidecar_only=True, physical_qualification_not_claimed=True))
        if result_path.read_bytes() != before:
            raise RuntimeError('Native RESULT changed during sidecar creation')
    return rc


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    parser.add_argument('--label', choices=('T05_R2',), default='T05_R2')
    parser.add_argument('--attempt', type=int, default=1)
    args = parser.parse_args()
    root = safe_new_root(args.root)
    record = dict(command=[sys.executable, '-m', 'tools.recovery_steam_20261104.run_worker'] + sys.argv[1:],
                  pid=os.getpid(), cwd=str(Path.cwd()), started_utc=utc())
    try:
        rc = run(root, args.label, args.attempt)
    except Exception as exc:
        record['error'] = dict(type=type(exc).__name__, message=str(exc), traceback=traceback.format_exc())
        traceback.print_exc()
        rc = 1
    record.update(exit_code=rc, ended_utc=utc())
    append(root / 'logs/WORKER_COMMANDS.jsonl', record)
    return rc


if __name__ == '__main__':
    raise SystemExit(main())
