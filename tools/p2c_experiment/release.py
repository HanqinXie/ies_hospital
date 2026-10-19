"""Evidence-bound release gate; a numerical subset never releases a regression."""
from __future__ import annotations
import json
from pathlib import Path
import hashlib
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[2]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def resolve_evidence(value, root=ROOT):
    """Project-relative paths are portable; archived absolute paths are rebased."""
    p = Path(value)
    if not p.is_absolute():
        return root / p
    try:
        return root / p.relative_to(ROOT)
    except ValueError:
        return p


def ref(path):
    p = Path(path).resolve()
    return dict(path=p.relative_to(ROOT).as_posix(), sha256=sha(p))


def checked(item):
    p = resolve_evidence(item['path'])
    if not p.is_file() or sha(p) != item['sha256']:
        raise ValueError('release evidence missing or changed: ' + item['path'])
    return p


def junit(path):
    tree = ET.parse(path)
    cases = tree.findall('.//testcase')
    return dict(tests=len(cases), failed=sum(c.find('failure') is not None for c in cases),
                errors=sum(c.find('error') is not None for c in cases),
                skipped=sum(c.find('skipped') is not None for c in cases),
                identities={(c.get('classname'), c.get('name')) for c in cases})


def validate_command_binding(command, junit_path):
    argv = command['command']
    paths = [v.split('=',1)[1] for v in argv if v.startswith('--junitxml=')]
    paths += [argv[i+1] for i,v in enumerate(argv[:-1]) if v == '--junitxml']
    if not any(resolve_evidence(p).resolve() == Path(junit_path).resolve() for p in paths):
        raise ValueError('regression command did not write the bound JUnit')


def validate_review_binding(independent, source):
    required = {'src/hospital_ies/resilience.py','src/hospital_ies/solver_diagnostics.py'}
    if (independent.get('passed') is not True or not required <= set(independent.get('source_sha256',{}))
            or any(independent['source_sha256'][rel] != source[rel] for rel in required)):
        raise ValueError('independent review source differs from released numerical source')


def source_snapshot(output):
    p = Path(output)
    if p.exists():
        raise FileExistsError('Do not overwrite pre-regression source snapshot')
    files = {p.relative_to(ROOT).as_posix():sha(p) for directory in ('src','tests','tools')
             for p in sorted((ROOT/directory).rglob('*.py')) if '__pycache__' not in p.parts}
    p.write_text(json.dumps(dict(utc=datetime.now(timezone.utc).isoformat(),source_sha256=files),indent=2),encoding='utf-8')
    return files


def validate_release(path):
    if path is None:
        raise ValueError('global regression release gate is required')
    g = json.loads(resolve_evidence(path).read_text(encoding='utf-8'))
    if g.get('passed') is not True or not g.get('source_sha256'):
        raise ValueError('global regression release is not passed')
    required = {'src/hospital_ies/resilience.py', 'src/hospital_ies/solver_diagnostics.py'}
    if not required <= set(g['source_sha256']):
        raise ValueError('global release does not bind numerical source')
    for rel, expected in g['source_sha256'].items():
        if sha(ROOT/rel) != expected:
            raise ValueError('global release source changed: ' + rel)
    command = json.loads(checked(g['command']).read_text(encoding='utf-8'))
    if command['exit_code'] != 0 or 'pytest' not in command['command']:
        raise ValueError('global regression did not exit zero')
    checked(g['stdout']); checked(g['stderr'])
    junit_path = checked(g['junit'])
    validate_command_binding(command,junit_path)
    actual = junit(junit_path)
    baseline = junit(checked(g['baseline_junit']))
    if (baseline['tests'] != 259 or actual['failed'] or actual['errors'] or actual['skipped']
            or not baseline['identities'] <= actual['identities']):
        raise ValueError('global regression omitted baseline cases or has failed/skipped cases')
    for key in ('tests', 'failed', 'errors', 'skipped'):
        if actual[key] != g['actual'][key]:
            raise ValueError('global regression summary differs from raw JUnit')
    snapshot = json.loads(checked(g['pre_regression_source']).read_text(encoding='utf-8'))
    if snapshot['source_sha256'] != g['source_sha256'] or snapshot['utc'] > command['started_utc']:
        raise ValueError('regression source was not captured before the run')
    independent = json.loads(checked(g['independent_review']).read_text(encoding='utf-8'))
    validate_review_binding(independent,g['source_sha256'])
    return g


def create(command_path, junit_path, baseline_path, independent_path, snapshot_path, output):
    command_path = Path(command_path)
    stem = command_path.name.removesuffix('.command.json')
    actual = junit(junit_path)
    source = json.loads(Path(snapshot_path).read_text(encoding='utf-8'))['source_sha256']
    g = dict(passed=True, pytest_exit_code=0, failed=actual['failed'],source_sha256=source, command=ref(command_path),
        stdout=ref(command_path.with_name(stem+'.stdout.log')),
        stderr=ref(command_path.with_name(stem+'.stderr.log')),
        junit=ref(junit_path), baseline_junit=ref(baseline_path),
        independent_review=ref(independent_path),
        pre_regression_source=ref(snapshot_path),
        actual={k:v for k,v in actual.items() if k != 'identities'},
        claim='Complete 259-case baseline retained plus new regressions; no physical calibration claim')
    out = Path(output)
    if out.exists():
        raise FileExistsError('Do not overwrite release evidence')
    out.write_text(json.dumps(g,ensure_ascii=False,indent=2),encoding='utf-8')
    validate_release(out)
    return g


if __name__ == '__main__':
    import argparse
    a = argparse.ArgumentParser()
    sub = a.add_subparsers(dest='mode',required=True)
    s = sub.add_parser('snapshot');s.add_argument('output')
    c = sub.add_parser('create')
    for name in ('command','junit','baseline','independent','snapshot','output'):
        c.add_argument(name)
    args = a.parse_args()
    if args.mode == 'snapshot':
        print(json.dumps(dict(files=len(source_snapshot(args.output)))))
    else:
        print(json.dumps(create(args.command,args.junit,args.baseline,args.independent,args.snapshot,args.output)['actual']))
