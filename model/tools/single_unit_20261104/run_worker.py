"""Use the native finite lifecycle; its exclusive RESULT.json has one writer.

The only temporary Python binding is the native runtime factory. The registered
adapter owns reset declaration/lowering; no solver or physical function changes.
Extension metadata is written once to COMPLETION.json after native closure.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import traceback

SOURCE = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(SOURCE), str(SOURCE / 'src')]
for _name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS'):
    os.environ[_name] = '1'

from tools.cooling_dynamics_20261104.extract_results import read, sha, write


def verify_binding(root):
    """Reuse all native drift checks, including the new external byte pins."""
    from tools.cooling_dynamics_20261104.run_worker import verify_binding as native_verify
    binding = native_verify(Path(root))
    if binding['schema'] != 'hospital_ies.single_unit.source_binding.v1':
        raise ValueError('Explicit single-unit source freeze required')
    return binding


def write_completion(output, *, label, native_exit_code, source_binding_sha256):
    """Write extension evidence without ever opening the native result to write."""
    output = Path(output)
    result_path = output / 'RESULT.json'
    result = read(result_path) if result_path.is_file() else None
    result_pin = dict(path=str(result_path), sha256=sha(result_path)) if result is not None else None
    completed = native_exit_code == 0 and result is not None and result.get('status') == 'completed'
    receipt = dict(schema='hospital_ies.single_unit.completion.v1', label=label,
        status='completed' if completed else 'failed', native_exit_code=native_exit_code,
        native_result=result_pin, native_result_original_source_binding=result.get('source_binding') if result else None,
        source_binding_sha256=source_binding_sha256,
        native_RESULT_exclusive_owner='tools.cooling_dynamics_20261104.run_worker.run',
        native_RESULT_rewritten=False, adapter='tools.single_unit_20261104.runtime.build_runtime',
        completed_utc=datetime.now(timezone.utc).isoformat())
    write(output / 'COMPLETION.json', receipt)
    if result_pin and sha(result_path) != result_pin['sha256']:
        raise ValueError('Native RESULT changed while extension receipt was written')
    return receipt


def run(root, label, attempt=1):
    root = Path(root).resolve()
    verify_binding(root)
    from tools.cooling_dynamics_20261104 import run_worker as native
    # Capture the unmodified native factory in runtime before its binding changes.
    from tools.single_unit_20261104.runtime import build_runtime
    original_factory = native.build_runtime
    output = root / 'runs' / (label + '_attempt' + str(attempt).zfill(2))
    exit_code = 1
    try:
        native.build_runtime = build_runtime
        exit_code = native.run(root, label, attempt)
    except BaseException as exc:
        output.mkdir(parents=True, exist_ok=True)
        write(output / 'WORKER_EXCEPTION.json', dict(type=type(exc).__name__, message=str(exc),
            traceback=traceback.format_exc(), native_RESULT_rewritten=False))
    finally:
        native.build_runtime = original_factory
    receipt = write_completion(output, label=label, native_exit_code=exit_code,
                               source_binding_sha256=sha(root / 'SOURCE_BINDING.json'))
    print(json.dumps(receipt, ensure_ascii=False), flush=True)
    return 0 if receipt['status'] == 'completed' else 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    parser.add_argument('--label', required=True)
    parser.add_argument('--attempt', type=int, default=1)
    args = parser.parse_args()
    return run(args.root, args.label, args.attempt)


if __name__ == '__main__':
    raise SystemExit(main())
