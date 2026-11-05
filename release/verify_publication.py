"""Read-only source-distribution verification; no scientific imports."""
from pathlib import Path
import ast
import hashlib
import json
import re
import sys

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify():
    manifest = json.loads((ROOT / 'release/PUBLICATION_MANIFEST.json').read_text('utf-8'))
    errors = []
    listed = set()
    total = 0
    py_count = 0
    for row in manifest['files']:
        relative = row['path']
        path = ROOT / relative
        listed.add(relative)
        if not path.is_file() or path.is_symlink() or path.stat().st_size != row['bytes'] or digest(path) != row['sha256']:
            errors.append('file_identity:' + relative)
            continue
        total += row['bytes']
        if row['bytes'] >= 100_000_000:
            errors.append('oversize:' + relative)
        if path.suffix == '.py':
            try:
                ast.parse(path.read_bytes(), filename=relative)
                py_count += 1
            except (SyntaxError, UnicodeError):
                errors.append('python_syntax:' + relative)
        if path.name.lower() in ('.env', 'gurobi.lic', 'credentials.json', 'id_rsa', 'id_ed25519') or path.suffix.lower() in ('.pem', '.key'):
            errors.append('forbidden_filename:' + relative)
        if path.suffix.lower() in ('.py', '.md', '.json', '.txt', '.csv', '.patch'):
            data = path.read_bytes()
            patterns = [rb'-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----',
                        rb'gh[pousr]_[A-Za-z0-9]{30,}', rb'github_pat_[A-Za-z0-9_]{50,}',
                        rb'AKIA[0-9A-Z]{16}', rb'sk-proj-[A-Za-z0-9_-]{40,}']
            if any(re.search(pattern, data) for pattern in patterns):
                errors.append('potential_secret_redacted:' + relative)
    excluded = {'.git', '__pycache__', '.pytest_cache'}
    actual = {p.relative_to(ROOT).as_posix() for p in ROOT.rglob('*') if p.is_file()
              and not any(part in excluded for part in p.relative_to(ROOT).parts)
              and not p.relative_to(ROOT).as_posix().startswith('release/local_logs/')
              and p.relative_to(ROOT).as_posix() not in ('release/PUBLICATION_MANIFEST.json', 'release/VALIDATION.json')}
    if actual != listed:
        errors += ['unlisted:' + p for p in sorted(actual - listed)]
        errors += ['missing:' + p for p in sorted(listed - actual)]
    original = json.loads((ROOT / 'release/SOURCE_MANIFEST.json').read_text('utf-8'))
    for row in original['files']:
        if not (ROOT / row['path']).is_file() or digest(ROOT / row['path']) != row['sha256']:
            errors.append('source_snapshot_changed:' + row['path'])
    result = dict(status='passed' if not errors else 'failed', checked_files=len(listed), checked_bytes=total,
                  python_syntax_files=py_count, source_snapshot_files=len(original['files']), errors=errors,
                  scientific_tests_executed=False, model_or_training_runs_started=0,
                  scope='Publication byte identity, syntax, coverage, basic secret and file-size checks only')
    print(json.dumps(result, ensure_ascii=False))
    return 0 if not errors else 1


if __name__ == '__main__':
    sys.exit(verify())
