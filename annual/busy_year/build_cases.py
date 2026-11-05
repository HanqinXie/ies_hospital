"""Build three canonical disturbed-year cases without running a physical model."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

# Keep the scientific checkout immutable even when the CLI is run without -B.
sys.dont_write_bytecode = True


PACKAGE = Path(__file__).resolve().parent
METHODS = ('M_available', 'M_ready', 'C_ready')


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode('utf8')).hexdigest()


def write_new(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def external_destination(destination):
    raw = Path(destination).absolute()
    resolved = raw.resolve()
    if raw != resolved or any(path.is_symlink() for path in (raw, *raw.parents)):
        raise ValueError('Frozen destination cannot use an aliased or linked path')
    if resolved == PACKAGE or resolved.is_relative_to(PACKAGE) or PACKAGE.is_relative_to(resolved):
        raise ValueError('Frozen output must be outside and must not contain the package')
    if resolved.exists():
        raise FileExistsError('Frozen output already exists: ' + str(resolved))
    return resolved


def canonical_cases(plan):
    from calendar_plan import availability_events, validate_plan
    validate_plan(plan)
    source = PACKAGE / 'source'
    sys.path[:0] = [str(source), str(source / 'src')]
    from tools.annual_program.registration import make_case, validate_case
    events = availability_events(plan)
    result = {}
    for method in METHODS:
        case = make_case(method, mode='FD', configuration='H2', start_hour=1,
            duration_minutes=525600., calendar=events,
            response_wait_minutes=15., boiler_restart_minutes=1.,
            requalification_minutes=60., demand_amplitude=.25,
            demand_start_minute=280800., demand_duration_minutes=40320.,
            execution_revision='annual_responsive_dynamic_bridge_20261104.v1',
            numerical_profile='engineering_fast_v1')
        validate_case(case)
        result[method] = case
    streams = {case['scenario']['exogenous_stream_id'] for case in result.values()}
    if len(streams) != 1:
        raise ValueError('The three methods do not share identical exogenous inputs')
    return result


def _package_binding():
    from runtime_checks import verify_package
    verify_package(PACKAGE)
    provenance = read(PACKAGE / 'config/SOURCE_PROVENANCE.json')
    head = subprocess.check_output(['git', '-C', str(PACKAGE / 'source'),
        'rev-parse', 'HEAD'], text=True).strip()
    if head != provenance['git_commit']:
        raise ValueError('Source commit changed after package sealing')
    return dict(package_manifest_sha256=sha(PACKAGE / 'MANIFEST.json'),
        source_sha256=provenance['source_sha256'], input_sha256=provenance['input_sha256'],
        git_commit=head)


def _write_set(destination, plan, cases, *, frozen, binding=None):
    destination = Path(destination)
    if destination.exists():
        raise FileExistsError('Case-set destination already exists: ' + str(destination))
    destination.mkdir(parents=True)
    write_new(destination / 'CALENDAR.json', plan)
    rows = []
    for method in METHODS:
        relative = 'cases/' + method + '.json'
        write_new(destination / relative, cases[method])
        rows.append(dict(method=method, path=relative,
            file_sha256=sha(destination / relative), canonical_sha256=digest(cases[method]),
            mode='FD', configuration='H2', hours=8760, source_hour=1))
    binding = dict(binding or {})
    binding['calendar_sha256'] = sha(destination / 'CALENDAR.json')
    index = dict(schema='hospital_ies.busy_year_cases.v1',
        calendar_state='frozen' if frozen else 'candidate_requires_normal_reference',
        annual_admitted=False, physical_starts=0, calendar_file='CALENDAR.json',
        calendar_sha256=binding['calendar_sha256'], binding=binding, cases=rows)
    if frozen:
        write_new(destination / 'REFERENCE_PROOF.json', plan['reference_proof'])
        index['reference_proof_sha256'] = sha(destination / 'REFERENCE_PROOF.json')
    write_new(destination / 'INDEX.json', index)
    return index


def write_candidates(destination):
    from calendar_plan import candidate_plan
    destination = Path(destination)
    if destination.exists():
        raise FileExistsError('Candidate destination already exists: ' + str(destination))
    plan = candidate_plan()
    return _write_set(destination, plan, canonical_cases(plan), frozen=False)


def freeze(reference_output, destination):
    destination = external_destination(destination)
    reference_output = Path(reference_output).resolve()
    if (destination == reference_output or destination.is_relative_to(reference_output)
            or reference_output.is_relative_to(destination)):
        raise ValueError('Frozen output and original normal reference must not overlap')
    if not reference_output.is_dir():
        raise FileNotFoundError('The original normal M_ready producer output is missing')
    from calendar_plan import resolve_plan, validate_plan, verify_reference_proof
    plan = resolve_plan(reference_output)
    validate_plan(plan, require_frozen=True)
    verify_reference_proof(plan)
    cases = canonical_cases(plan)
    binding = _package_binding()
    return _write_set(destination, plan, cases, frozen=True, binding=binding)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    candidate = commands.add_parser('candidates', help='Write non-runnable candidate model configurations')
    candidate.add_argument('--output', type=Path, required=True)
    resolved = commands.add_parser('freeze', help='Bind cases to verified original normal M_ready evidence')
    resolved.add_argument('--reference-output', type=Path, required=True)
    resolved.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    result = (write_candidates(args.output) if args.command == 'candidates'
              else freeze(args.reference_output, args.output))
    print(json.dumps(dict(calendar_state=result['calendar_state'], cases=len(result['cases']),
        physical_starts=0, annual_admitted=False)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
