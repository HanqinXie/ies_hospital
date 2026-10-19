"""Read-only original integrity + function-level frozen-physics comparison."""
import ast
import hashlib
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2]
BASE=ROOT/'outputs/v43_p2a_20261013_120000'

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def run():
    frozen=json.loads((BASE/'manifest_before.json').read_text(encoding='utf-8'))
    same=[];changed=[];missing=[]
    for entry in frozen:
        p=ROOT/entry['path']
        if not p.exists():missing.append(entry['path'])
        elif sha(p)==entry['sha256']:same.append(entry['path'])
        else:changed.append(dict(path=entry['path'],before=entry['sha256'],after=sha(p)))
    physics={}
    for name in ('control_env','state_transition','execution','model','thermal_inertia','idf_parameters','resilience'):
        rel=f'src/hospital_ies/{name}.py';old=(BASE/'.before'/rel).read_text(encoding='utf-8');new=(ROOT/rel).read_text(encoding='utf-8')
        if name!='resilience':
            assert old==new,('Frozen physics file modified',rel)
            physics[rel]='byte-identical'
        else:
            def funcs(text):return {node.name:ast.dump(node,include_attributes=False) for node in ast.parse(text).body
                if isinstance(node,(ast.FunctionDef,ast.ClassDef))}
            a,b=funcs(old),funcs(new)
            diffs=[k for k in set(a)|set(b) if a.get(k)!=b.get(k)]
            assert diffs==['validate_scenario'],diffs
            # Non-function definitions/imports/constants also unchanged.
            rest=lambda text:[ast.dump(n,include_attributes=False) for n in ast.parse(text).body
                if not isinstance(n,(ast.FunctionDef,ast.ClassDef))]
            assert rest(old)==rest(new)
            physics[rel]='only validate_scenario admission changed; every physical/state/label function and constant AST identical'
    protected=[x for x in changed if x['path'].split('/')[0] in ('inputs','configs','outputs')]
    assert not missing and not protected,(missing,protected)
    report=dict(original_files=len(frozen),unchanged=len(same),authorized_changed=changed,missing=missing,
        protected_changes=protected,physics_checks=physics,
        counts_unchanged={category:sum(p.startswith(category+'/') for p in same) for category in ('inputs','configs','outputs')})
    (BASE/'original_integrity.json').write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps(report,indent=2,ensure_ascii=True))

if __name__=='__main__':run()
