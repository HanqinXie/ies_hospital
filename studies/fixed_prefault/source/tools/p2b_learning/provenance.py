"""Immutable mathematical evidence checks and complete offline fit provenance."""
import hashlib
from importlib import metadata
import json
from pathlib import Path
import platform
import sys

ROOT=Path(__file__).resolve().parents[2]
METHODS=ROOT/'outputs/v43_p2b_20261013_run01/methods'


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify_source_entries(root,entries,overrides):
    used=[]
    for item in entries:
        current=sha(Path(root)/item['path'])
        if current==item['sha256']:continue
        if overrides.get(item['path'])!=current:raise ValueError('unapproved mathematical source drift: '+item['path'])
        used.append(item['path'])
    return used


def build_fit_provenance(configurations,seeds):
    original_path=METHODS/'method_source_manifest.json'
    correction_path=METHODS/'reference_correction_manifest.json'
    original=json.loads(original_path.read_text(encoding='utf-8'))
    correction=json.loads(correction_path.read_text(encoding='utf-8'))
    if correction['path']!='tools/p2b_methods/validate.py' or correction.get('executed_results_unchanged') is not True:
        raise ValueError('unexpected mathematical source override')
    used=verify_source_entries(ROOT,original['code'],{correction['path']:correction['sha256']})
    registered={i['path'] for i in original['code']}
    method_files=sorted((ROOT/'tools/p2b_methods').glob('*.py'))
    if any(p.relative_to(ROOT).as_posix() not in registered for p in method_files):raise ValueError('unregistered mathematical helper source')
    if sha(METHODS/'PREREGISTRATION.md')!=original['preregistration_sha256']:raise ValueError('mathematical preregistration changed')
    artifact_path=METHODS/'validation_02_manifest.json'
    artifacts=json.loads(artifact_path.read_text(encoding='utf-8'))
    for entry in artifacts:
        if sha(METHODS/entry['path'])!=entry['sha256']:raise ValueError('original mathematical validation artifact changed: '+entry['path'])
    files=method_files+sorted((ROOT/'tools/p2b_learning').glob('*.py'))+[ROOT/'P2B_EXPERIMENT_PLAN.md',ROOT/'tests/test_p2b_learning.py']
    sources=[{'path':p.relative_to(ROOT).as_posix(),'sha256':sha(p)} for p in files]
    evidence_files=['method_source_manifest.json','reference_correction_manifest.json','REFERENCE_CORRIGENDUM.md','PREREGISTRATION.md','METHOD_VALIDATION_REPORT.md','validation_02_manifest.json','validation_02/summary.json','validation_02/D22/summary.json','validation_02/D24/summary.json','validation_02/independent_checks.json']
    runtime={'python':sys.version,'platform':platform.platform(),'packages':{}}
    for package in ['numpy','scipy','scikit-learn','joblib','torch','threadpoolctl']:
        try:runtime['packages'][package]=metadata.version(package)
        except metadata.PackageNotFoundError:runtime['packages'][package]=None
    result={'format':'p2b.offline.fit.provenance.v2','implementation_sources':sources,'candidate_configurations':configurations,'fixed_seeds':list(seeds),'runtime':runtime,'mathematical_evidence':{'verified_validation_artifacts':len(artifacts),'source_overrides_used':used,'original_source_manifest_sha256':sha(original_path),'explicit_correction_manifest_sha256':sha(correction_path),'files':[{'path':str((METHODS/name).relative_to(ROOT).as_posix()),'sha256':sha(METHODS/name)} for name in evidence_files],'scope':'original validation_02 remains immutable; validate.py reference-only correction explicitly bound'}}
    result['content_sha256']=hashlib.sha256(json.dumps(result,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode()).hexdigest()
    return result
