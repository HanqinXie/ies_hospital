"""Verify the current independent frozen checkout without importing science."""
from pathlib import Path, PurePosixPath
import hashlib
import json
import subprocess

ROOT=Path(__file__).resolve().parent
PROFILE='annual_responsive_dynamic_bridge_20261104.v1'


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def safe_path(root,relative):
    if not isinstance(relative,str) or not relative or ':' in relative or '\\' in relative:
        raise ValueError('Ordinary package-relative path required: '+str(relative))
    raw=PurePosixPath(relative)
    if raw.is_absolute() or '..' in raw.parts:
        raise ValueError('Frozen path escapes source: '+relative)
    root=Path(root).resolve();path=root/relative
    if not path.resolve().is_relative_to(root) or any(p.is_symlink() for p in (path,*path.parents) if p!=root.parent):
        raise ValueError('Frozen path contains a link or escape: '+relative)
    return path


def verify(package_root=ROOT):
    root=Path(package_root).resolve();source=root/'source'
    freeze=read_json(root/'evidence/SOURCE_FREEZE.json')
    if not (source/'.git').is_dir() or (source/'.git/objects/info/alternates').exists():
        raise ValueError('Source requires an independent Git repository without object alternates')
    common=subprocess.check_output(['git','-C',str(source),'rev-parse','--git-common-dir'],text=True).strip()
    common=Path(common) if Path(common).is_absolute() else source/common
    if common.resolve()!=(source/'.git').resolve():raise ValueError('Source common Git directory is external')
    commit=subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True).strip()
    if commit!=freeze.get('git_commit'):raise ValueError('Source HEAD differs from current SOURCE_FREEZE')
    errors=[]
    for name in ('source','inputs'):
        mapping=freeze.get(name)
        if not isinstance(mapping,dict) or not mapping:raise ValueError('Empty frozen '+name+' inventory')
        for relative,expected in mapping.items():
            path=safe_path(source,relative)
            if not path.is_file() or sha(path)!=expected:errors.append(relative)
    if errors:raise ValueError('Frozen source/input bytes differ: '+repr(errors))
    status=subprocess.check_output(['git','-C',str(source),'status','--porcelain'],text=True)
    if status.strip():raise ValueError('Frozen source worktree is dirty: '+status)
    provenance_path=root/'evidence/PAYLOAD_PROVENANCE.json'
    if provenance_path.is_file():
        provenance=read_json(provenance_path)
        if provenance.get('source_commit')!=commit:raise ValueError('Payload provenance commit differs')
        if provenance.get('execution_revision',provenance.get('profile',provenance.get('annual_profile',PROFILE)))!=PROFILE:
            raise ValueError('Payload provenance annual profile differs')
    return dict(status='passed',scope='Current Git/source/input byte verification only',
        source_commit=commit,source_files=len(freeze['source']),input_files=len(freeze['inputs']),
        source_freeze_sha256=sha(root/'evidence/SOURCE_FREEZE.json'),profile=PROFILE,
        independent_git=True,physical_or_optimizer_runs=0,server_admission_granted=False)


def main():print(json.dumps(verify(),ensure_ascii=False))


if __name__=='__main__':main()
