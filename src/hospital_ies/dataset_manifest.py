"""Byte-level, transitive dependency manifest and verified atomic checkpoints."""
from __future__ import annotations
import copy
import hashlib
import importlib.metadata
import json
import platform
from pathlib import Path

def digest_bytes(data):
    return hashlib.sha256(data).hexdigest()

def canonical_json(value):
    return json.dumps(value,sort_keys=True,ensure_ascii=True,separators=(',',':'),allow_nan=False)

def digest(value):
    return digest_bytes(canonical_json(value).encode('utf-8'))

# Capture before any generation. Fresh Python processes are required after
# editing code; never silently hot-reload a simulator into an existing run.
_IMPORTED_ROOT=Path(__file__).resolve().parents[2]
_SOURCE_AT_IMPORT={p.relative_to(_IMPORTED_ROOT).as_posix():digest_bytes(p.read_bytes())
    for p in [*_IMPORTED_ROOT.glob('*.py'),*(_IMPORTED_ROOT/'src').rglob('*.py')]
    if '__pycache__' not in p.parts}

def runtime_versions():
    try:
        from .solver_backend import descriptor
        solver = descriptor()
    except ImportError:
        solver = {'backend': 'scipy.optimize.milp / bundled HiGHS',
                  'status': 'legacy implementation'}
    return dict(python=platform.python_version(),platform=platform.platform(),
        solver=solver,
        packages=dict(sorted((d.metadata['Name'].lower(),d.version) for d in importlib.metadata.distributions())))

def inventory(root):
    root=Path(root)
    paths=set(root.glob('*.py'))|set(root.glob('requirements*.txt'))
    for directory in ('src','configs','inputs'):
        paths.update(p for p in (root/directory).rglob('*') if p.is_file()
            and '__pycache__' not in p.parts and p.suffix not in ('.pyc','.zip'))
    return [dict(path=p.relative_to(root).as_posix(),bytes=p.stat().st_size,
                 sha256=digest_bytes(p.read_bytes())) for p in sorted(paths)]

def verify_execution_source(root):
    """A data root may change, but manifest code must be the code imported here."""
    actual=Path(__file__).resolve().parents[2];claimed=Path(root).resolve()
    paths=list(actual.glob('*.py'))+list((actual/'src').rglob('*.py'))
    for source in paths:
        if '__pycache__' in source.parts:continue
        relative=source.relative_to(actual);other=claimed/relative
        if _SOURCE_AT_IMPORT.get(relative.as_posix())!=digest_bytes(source.read_bytes()):
            raise ValueError('Production source changed after import; restart in a fresh process')
        if not other.is_file() or digest_bytes(source.read_bytes())!=digest_bytes(other.read_bytes()):
            raise ValueError('Manifest source dependency differs from actual imported implementation: '+relative.as_posix())

def build_manifest(root,scenarios,policies,contract,schemas,options,splits):
    files=inventory(root)
    physics=[x for x in files if x['path'] in (
        'src/hospital_ies/model.py','src/hospital_ies/resilience.py',
        'src/hospital_ies/control_env.py','src/hospital_ies/state_transition.py',
        'src/hospital_ies/execution.py','src/hospital_ies/thermal_inertia.py',
        'src/hospital_ies/idf_parameters.py','configs/plant_configs.json',
        'configs/resilience_assumptions.json',
        'src/hospital_ies/solver_backend.py','src/hospital_ies/solver_diagnostics.py')]
    result=dict(manifest_version='p2a.manifest.v1',files=files,runtime=runtime_versions(),
        scenarios=sorted(copy.deepcopy(scenarios),key=lambda x:x['id']),
        policies=sorted(policies),execution_contract=contract,schemas=schemas,
        options=options,splits=splits,simulator_hash=digest(physics),
        execution_contract_id=contract['name']+':'+digest(contract),
        assumption_set_id='p1b.uncalibrated.v1:'+digest(dict(contract=contract,physics=physics)),
        probability_semantics='q_design; p_reference and importance weights undefined',
        policy_version='v42.lexicographic_unchanged.secure_gt_penalty',
        random_stream_semantics='pre-generated exogenous scripts; no policy RNG draws')
    result['execution_source_policy']='fresh process after edits; source-at-import drift guard; no hot reload'
    result['signature']=digest(result)
    return result

def open_manifest(out,manifest,resume):
    out=Path(out);path=out/'manifest_p2a.json'
    if path.exists():
        if not resume:raise FileExistsError('Existing manifest; use matching resume or new directory')
        old=json.loads(path.read_text(encoding='utf-8'))
        if old!=manifest:raise ValueError('Dependency/schema/scenario fingerprint mismatch; refuse mixed manifest resume')
    else:
        if any(out.glob('resume_manifest*')) or (out/'checkpoints_p2a').exists():
            raise ValueError('Old or orphan checkpoint manifest cannot be resumed')
        if resume:raise ValueError('Resume requires a current manifest')
        if out.exists() and any(out.iterdir()):raise FileExistsError('Existing nonempty output directory has no owned P2A manifest')
        out.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(manifest,indent=2,ensure_ascii=False),encoding='utf-8')
    (out/'checkpoints_p2a').mkdir(exist_ok=True)

def checkpoint_path(out,scenario_id,policy):
    # Never interpolate user-provided IDs as filesystem paths.
    return Path(out)/'checkpoints_p2a'/(digest([scenario_id,policy])+'.json')

def save_checkpoint(path,payload,signature):
    wrapped=dict(manifest_signature=signature,payload=payload)
    raw=json.dumps(wrapped,ensure_ascii=False,allow_nan=False).encode('utf-8')
    temp=path.with_suffix('.tmp');temp.write_bytes(raw);temp.replace(path)
    path.with_suffix('.sha256').write_text(digest_bytes(raw),encoding='ascii')

def load_checkpoint(path,signature):
    raw=path.read_bytes()
    side=path.with_suffix('.sha256')
    if not side.exists() or side.read_text(encoding='ascii')!=digest_bytes(raw):
        raise ValueError('checkpoint bytes do not match integrity hash')
    wrapped=json.loads(raw)
    if wrapped['manifest_signature']!=signature:raise ValueError('checkpoint manifest fingerprint mismatch')
    return wrapped['payload']

def canonical_payload(value):
    """Deterministic replay comparison; raw logs retain all wall-clock fields.

    Gurobi TimeLimit records the measured remainder of the same registered
    per-layer budget. It is not a physical event delay or an extra capability.
    Status, retry count, raw vectors and all other solver parameters remain.
    """
    if isinstance(value,dict):return {k:canonical_payload(v) for k,v in value.items()
        if k not in ('decision_ms','elapsed_seconds','started_utc','finished_utc','TimeLimit')}
    if isinstance(value,list):return [canonical_payload(x) for x in value]
    # Canonical signature/data fields themselves remain exact.
    return value
