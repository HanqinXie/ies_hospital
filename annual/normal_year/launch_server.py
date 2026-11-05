"""Explicit admitted Windows launch; importing never starts scientific work.

The unchanged Windows process owner is supervised outside the invoking Codex
Job. WMI is selected explicitly; a rejected launch never triggers a fallback.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from transfer_checks import require_admission, write_new
from scripts.verify_launcher import require_launcher_probe

PACKAGE = Path(__file__).resolve().parent
HELPER_SHA256 = '715fd828b471b34f3187dbebaade9c52126d26cf6c9690c72d0ceac37e468dca'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def launch(work_root, method='wmi'):
    work = Path(work_root).resolve()
    admission = require_admission(PACKAGE, work)
    launcher_probe = require_launcher_probe(PACKAGE, work)
    if method not in ('wmi', 'breakaway'):
        raise ValueError('Choose an explicit wmi or breakaway launch method')
    if method != launcher_probe.get('launch_method', 'wmi'):
        raise ValueError('Independent launcher probe does not authorize this launch method')
    limits = admission['limits']
    for field, maximum in (('max_wall_seconds', 2592000), ('grace_seconds', 120)):
        value = limits[field]
        if type(value) is not int or not (0 if field == 'grace_seconds' else 1) <= value <= maximum:
            raise ValueError('Durable launch requires registered finite integer seconds: ' + field)
    durable = PACKAGE / 'scripts/durable_launch.py'
    helper = PACKAGE / 'scripts/vendor/owned_process_hidden.py'
    if sha(helper) != HELPER_SHA256:
        raise ValueError('Frozen OwnedProcess helper source binding changed')
    spec = dict(schema='hospital_ies.durable_launch.v1',
        command=[sys.executable, '-B', str(PACKAGE / 'server_runner.py'), 'run', '--work-root', str(work)],
        cwd=str(PACKAGE), run_directory=str(work / 'supervisor'),
        max_seconds=limits['max_wall_seconds'], grace_seconds=limits['grace_seconds'],
        stop_file=str(work / 'runtime/STOP'), cooperative_stop_files=[str(work / 'runtime/cases' / method / 'stop/STOP')
            for method in ('M_available', 'M_ready', 'C_ready')])
    spec_path = work / 'SUPERVISOR_SPEC.json'
    write_new(spec_path, spec)
    command = [sys.executable, '-S', str(durable), 'launch', '--spec', str(spec_path), '--method', method]
    write_new(work / 'SUPERVISOR_BINDING.json', dict(
        created_utc=datetime.now(timezone.utc).isoformat(), admission_binding=admission['binding'],
        admission_sha256=sha(work / 'admission/ADMISSION.json'), spec_sha256=sha(spec_path),
        helper_sha256=sha(helper), durable_launcher_sha256=sha(durable),
        entrypoint_sha256=sha(__file__), inner_runner_sha256=sha(PACKAGE / 'server_runner.py'),
        command=command, launch_method=method, automatic_fallback=False,
        launcher_probe_status=launcher_probe['status'],
        outer_rss_limit_bytes=10_000_000_000, outer_min_available_ram_bytes=16_000_000_000,
        outer_min_free_disk_bytes=30_000_000_000))
    with (work / 'SUPERVISOR_LAUNCH.stdout.log').open('x', encoding='utf8') as out, \
            (work / 'SUPERVISOR_LAUNCH.stderr.log').open('x', encoding='utf8') as err:
        try:
            result = subprocess.run(command, cwd=str(PACKAGE), capture_output=True, text=True, encoding='utf8',
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
            out.write(result.stdout)
            err.write(result.stderr)
        except BaseException as error:
            write_new(work / 'SUPERVISOR_LAUNCH_EXIT.json', dict(exit_code=None,
                error=dict(type=type(error).__name__, message=str(error)), automatic_fallback=False))
            raise
    write_new(work / 'SUPERVISOR_LAUNCH_EXIT.json', dict(exit_code=result.returncode,
        automatic_fallback=False, annual_completion_claimed=False, launch_method=method,
        spec_sha256=sha(spec_path)))
    if result.stdout:
        print(result.stdout.rstrip())
    if result.stderr:
        print(result.stderr.rstrip(), file=sys.stderr)
    return result.returncode


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work-root', type=Path, required=True)
    parser.add_argument('--method', choices=('wmi', 'breakaway'), default='wmi',
        help='Explicit same-user method; WMI is default, no automatic fallback')
    args = parser.parse_args(argv)
    return launch(args.work_root, args.method)


if __name__ == '__main__':
    raise SystemExit(main())
