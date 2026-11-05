"""Explicit foreground entry for the admitted continuous disturbed-year batch."""
from __future__ import annotations
import argparse
import json
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frozen-root', type=Path, required=True)
    parser.add_argument('--work-root', type=Path, required=True)
    parser.add_argument('--method', choices=('M_available', 'M_ready', 'C_ready'))
    args = parser.parse_args(argv)
    from server_runner import PACKAGE, run_campaign
    result = run_campaign(PACKAGE, args.work_root, frozen_root=args.frozen_root,
        methods=[args.method] if args.method else None)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
