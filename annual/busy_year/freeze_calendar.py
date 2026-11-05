"""Resolve the shared calendar from original normal-year actual production."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from calendar_plan import resolve_plan

PACKAGE = Path(__file__).resolve().parent


def freeze(reference_output, output):
    target = Path(output).absolute()
    reference = Path(reference_output).absolute().resolve()
    package = PACKAGE.resolve()
    if target != target.resolve() or target == package or target.is_relative_to(package) or package.is_relative_to(target):
        raise ValueError('Calendar output must be a new explicit directory outside the package')
    if target == reference or target.is_relative_to(reference) or reference.is_relative_to(target):
        raise ValueError('Calendar output must not overlap the read-only original reference tree')
    if target.exists():
        raise ValueError('Calendar output already exists; preserve previous evidence')
    plan = resolve_plan(reference_output)
    target.mkdir(parents=True, exist_ok=False)
    for name, value in (('REFERENCE_PROOF.json', plan['reference_proof']), ('CALENDAR.json', plan)):
        with (target/name).open('x', encoding='utf8') as stream:
            json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
    return plan


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference-output', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    result = freeze(args.reference_output, args.output)
    print(json.dumps(dict(status=result['status'], plan_sha256=result['plan_sha256'],
                          output=str(args.output.resolve()), scientific_runs_started=0)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
