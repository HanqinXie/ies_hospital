"""Verify baseline immutability and export file diff without a Git repository."""
from __future__ import annotations
import argparse
from datetime import datetime,timezone
import difflib
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

p=argparse.ArgumentParser()
p.add_argument('--run-dir',type=Path,required=True)
a=p.parse_args();root=Path.cwd();run=a.run_dir.resolve()
m=json.loads((run/'manifest.json').read_text(encoding='utf-8'))
sha=lambda path:hashlib.sha256(path.read_bytes()).hexdigest()
changed=[];missing=[];snapshot_errors=[]
for rec in m['files']:
    path=root/rec['path'];snap=run/'.baseline_files'/rec['path']
    if not path.exists():missing.append(rec['path'])
    elif sha(path)!=rec['sha256']:changed.append(rec['path'])
    if not snap.exists() or sha(snap)!=rec['sha256']:snapshot_errors.append(rec['path'])
old=set(x['path'] for x in m['files'])|set(x['path'] for x in m['excluded_archives'])
added=[]
for path in root.rglob('*'):
    if not path.is_file() or run in path.parents:continue
    if any(x in path.parts for x in ('__pycache__','.pytest_cache','.git')):continue
    rel=path.relative_to(root).as_posix()
    if rel not in old:
        added.append(dict(path=rel,size=path.stat().st_size,sha256=sha(path)))
added.sort(key=lambda x:x['path'])
patch=[]
for rec in added:
    rel=rec['path']
    if rel.startswith('outputs/') or Path(rel).suffix not in ('.py','.md'):continue
    lines=(root/rel).read_text(encoding='utf-8-sig').splitlines(keepends=True)
    patch.extend(difflib.unified_diff([],lines,fromfile='/dev/null',tofile='b/'+rel))
(run/'changes.patch').write_text(''.join(patch),encoding='utf-8')
(run/'change_inventory.json').write_text(json.dumps(dict(added=added,modified=changed,deleted=missing),indent=2,ensure_ascii=False),encoding='utf-8')
package=json.loads((root/'task_for_codex/HANDOFF_MANIFEST.json').read_text(encoding='utf-8'))
package_errors=[name for name,digest in package.items() if sha(root/'task_for_codex'/name)!=digest]
copies={'AGENTS.md':'task_for_codex/AGENTS.v43.template.md'}
for folder in ('docs/v43','tools/v42_audit'):
    for path in (root/folder).iterdir():
        original=root/'task_for_codex'/folder/path.name
        if path.is_file() and original.exists():copies[path.relative_to(root).as_posix()]=original.relative_to(root).as_posix()
copy_errors=[dest for dest,src in copies.items() if sha(root/dest)!=sha(root/src)]
tests={}
for name in ('regressions_red_final.xml','pytest_strict.xml'):
    tree=ET.parse(run/name)
    cases=[]
    for t in tree.iter('testcase'):
        status='failed' if t.find('failure') is not None else 'error' if t.find('error') is not None else 'xfail' if t.find('skipped') is not None else 'passed'
        cases.append(dict(name=t.attrib['name'],classname=t.attrib.get('classname'),status=status))
    tests[name]={'counts':{k:sum(c['status']==k for c in cases) for k in ('passed','failed','error','xfail')},'cases':cases}
(run/'regression_results.json').write_text(json.dumps(tests,indent=2),encoding='utf-8')
commands=[json.loads(x.read_text(encoding='utf-8')) for x in (run/'logs').glob('*.command.json')]
ledger=[json.loads(x) for x in (run/'commands_and_exit_codes.txt').read_text(encoding='utf-8').splitlines()]
command_ledger_ok={x['name'] for x in commands}=={x['name'] for x in ledger} and len(commands)==len(ledger)
result=dict(verified_utc=datetime.now(timezone.utc).isoformat(),baseline_files=len(m['files']),
    original_files_changed=changed,original_files_missing=missing,snapshot_hash_errors=snapshot_errors,
    handoff_manifest_mismatches=package_errors,copied_instruction_or_tool_mismatches=copy_errors,
    archive_content_read=False,excluded_archives=m['excluded_archives'],
    command_ledger_matches=command_ledger_ok,added_files_count=len(added),
    production_logic_changes=0 if not changed else None,
    snapshot_directory='.baseline_files',git_available=False,
    tests={k:v['counts'] for k,v in tests.items()})
(run/'integrity_check.json').write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding='utf-8')
print(json.dumps(result,indent=2,ensure_ascii=True))
assert not (changed or missing or snapshot_errors or package_errors or copy_errors)
assert command_ledger_ok
assert tests['regressions_red_final.xml']['counts']==dict(passed=3,failed=12,error=0,xfail=0)
assert tests['pytest_strict.xml']['counts']==dict(passed=35,failed=0,error=0,xfail=12)
