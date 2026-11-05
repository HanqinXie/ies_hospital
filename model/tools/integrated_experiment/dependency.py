"""One evidence check for the current core; historic evidence stays immutable."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def reference(path):
    path = Path(path).resolve()
    return {'path': path.relative_to(ROOT).as_posix(), 'sha256': sha(path)}


def checked_reference(ref):
    path = ROOT / ref['path']
    if not path.is_file() or sha(path) != ref['sha256']:
        raise ValueError('Missing or changed actual evidence: ' + ref['path'])
    return path


def junit_cases(path):
    cases = ET.parse(path).getroot().findall('.//testcase')
    identities = {(x.get('classname'), x.get('name')) for x in cases}
    if len(cases) != len(identities):
        raise ValueError('Duplicate regression identities')
    return cases, identities


def check_regression(command, junit, baseline):
    record = read(command)
    if record['exit_code'] != 0:
        raise ValueError('Current complete regression has nonzero actual exit')
    cases, identities = junit_cases(junit)
    _, inherited = junit_cases(baseline)
    if not cases or not inherited <= identities:
        raise ValueError('Current regression omitted inherited counterexamples')
    if any(any(x.find(k) is not None for k in ('failure', 'error', 'skipped')) for x in cases):
        raise ValueError('Current regression has failure, error or skipped case')
    return {'cases': len(cases), 'inherited_cases': len(inherited), 'exit_code': 0}


def validate_core_gate(path):
    from hospital_ies.dataset_manifest import inventory, digest, verify_execution_source
    gate = read(path)
    unsigned = dict(gate)
    signature = unsigned.pop('signature')
    if digest(unsigned) != signature or gate.get('passed') is not True:
        raise ValueError('Current core evidence binding is invalid')
    verify_execution_source(ROOT)
    actual = inventory(ROOT)
    if actual != gate['files']:
        raise ValueError('Frozen core/input dependency changed; new version required')
    evidence = {k: checked_reference(v) for k, v in gate['evidence'].items()}
    check_regression(evidence['regression_command'], evidence['regression_junit'], evidence['takeover_junit'])
    numerical = read(evidence['solver_summary'])
    if numerical.get('passed') is not True:
        raise ValueError('Known numerical counterexamples are not closed')
    for rel, expected in numerical['source_sha256'].items():
        if sha(ROOT / rel) != expected:
            raise ValueError('Actual numerical evidence belongs to a different core')
    for name, count in (('original111', 111), ('new4', 4), ('success6', 6), ('timing4', 4), ('fixed18', 18)):
        group = numerical['groups'][name]
        if group['attempted'] != count or group['solved'] != count:
            raise ValueError('Incomplete fixed numerical/trajectory group: ' + name)
    # Raw evidence is required in addition to aggregate counts. The numerical
    # runner lists the actual output files, not a certificate of another gate.
    raw = numerical.get('raw_evidence', [])
    if not raw:
        raise ValueError('Numerical summary lacks actual raw evidence')
    for ref in raw:
        checked_reference(ref)
    return gate


def freeze_core_gate(command, junit, baseline, solver_summary, out):
    from hospital_ies.dataset_manifest import inventory, digest
    out = Path(out)
    if out.exists():
        raise FileExistsError('Refuse overwriting a core version')
    result = check_regression(command, junit, baseline)
    gate = {'format': 'integrated.core_evidence.v1', 'passed': True,
            'files': inventory(ROOT), 'actual_regression': result,
            'evidence': {k: reference(v) for k, v in {
                'regression_command': command, 'regression_junit': junit,
                'takeover_junit': baseline, 'solver_summary': solver_summary}.items()}}
    gate['signature'] = digest(gate)
    out.write_text(json.dumps(gate, indent=2, ensure_ascii=False), encoding='utf-8')
    return validate_core_gate(out)


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest='action', required=True)
    f = sub.add_parser('freeze')
    for name in ('command', 'junit', 'baseline', 'solver_summary', 'out'):
        f.add_argument(name)
    c = sub.add_parser('check'); c.add_argument('gate')
    a = p.parse_args()
    if a.action == 'freeze':
        gate = freeze_core_gate(a.command, a.junit, a.baseline, a.solver_summary, a.out)
    else:
        gate = validate_core_gate(a.gate)
    print(json.dumps({'passed': gate['passed'], 'core_signature': gate['signature'],
                      'actual_regression': gate['actual_regression']}))
