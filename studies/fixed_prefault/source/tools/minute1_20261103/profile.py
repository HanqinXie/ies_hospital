"""Authenticate an externally frozen minute producer, never create approvals.

The caller supplies an absolute admission path and its independently retained
SHA. This module has no freeze/write/register function and no current-byte
approval fallback. The review receipt and admission must be outside the source
closure, avoiding circular source/manifest/recorder hashes. Full producer
inventory and exact loaded helpers are checked separately by the recorder.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

from tools.annual_program import flow_source_contracts as contracts

REVIEW_SCHEMA='hospital_ies.minute1.source_review.v1'
THERMAL_REVIEW_SCHEMA='hospital_ies.minute1.thermal_source_review.v2'


def _wire(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf8')


def _reject_constant(value):
    raise ValueError('Nonfinite minute admission JSON: '+value)


def _unique_object(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise ValueError('Duplicate minute admission JSON key: '+key)
        result[key]=value
    return result


def _read_pinned(reference,kind):
    if (type(reference) is not dict or set(reference)!={'path','sha256'}
            or type(reference.get('path')) is not str or not reference['path'].strip()
            or not contracts._hash(reference.get('sha256'))):
        raise ValueError('Explicit pinned minute '+kind+' reference required')
    path=Path(reference['path'])
    if not path.is_absolute():raise ValueError('Absolute minute '+kind+' path required')
    try:raw=path.read_bytes()
    except OSError as exc:raise ValueError('Missing minute '+kind+' file') from exc
    if hashlib.sha256(raw).hexdigest()!=reference['sha256']:
        raise ValueError('Minute '+kind+' SHA changed')
    try:return json.loads(raw.decode('utf8'),object_pairs_hook=_unique_object,parse_constant=_reject_constant)
    except (UnicodeError,json.JSONDecodeError) as exc:raise ValueError('Invalid minute '+kind+' JSON') from exc


def read_source_admission(reference):
    """Read the externally pinned descriptor and its source-bound review.

    This proves matching reviewed declarations only, not actual producer bytes,
    runtime execution, numerical correctness or a new data-access grant.
    """
    value=_read_pinned(reference,'admission')
    files=value.get('source_files') if type(value) is dict else None
    matched=contracts.match_native_source_profile(files,source_admission=value)
    if matched not in (contracts.MINUTE1_CAPTURE_PROFILE,contracts.MINUTE_THERMAL_CAPTURE_PROFILE):
        raise ValueError('Incomplete or unsupported minute source admission')
    review=_read_pinned(value['review_receipt'],'review receipt')
    thermal=matched==contracts.MINUTE_THERMAL_CAPTURE_PROFILE
    keys={'schema','status','source_files_sha256','recorder_sha256'}
    if thermal:keys.add('native_revision_contract_sha256')
    if (type(review) is not dict or set(review)!=keys
            or review.get('schema')!=(THERMAL_REVIEW_SCHEMA if thermal else REVIEW_SCHEMA) or review.get('status')!='accepted'
            or review.get('source_files_sha256')!=hashlib.sha256(_wire(files)).hexdigest()
            or review.get('recorder_sha256')!=value['recorder_sha256']):
        raise ValueError('Minute review does not accept the exact complete source identity')
    if thermal and review['native_revision_contract_sha256']!=hashlib.sha256(_wire(value['native_revision_contract'])).hexdigest():
        raise ValueError('Minute thermal review does not accept the exact native revision contract')
    return deepcopy(value)


def verify_source_admission(source_root,reference,*,source_hashes=None):
    """Authenticate every source byte against an explicit external admission.

    Checking the inventory prevents a partial closure from borrowing an approved
    native leaf. No hash observed here is written into an approval declaration.
    """
    value=read_source_admission(reference);root=Path(source_root).resolve()
    expected=value['source_files']
    if source_hashes is not None and source_hashes!=expected:
        raise ValueError('Minute frozen source map differs from admission')
    # Same documented inventory as source_binding, but independently read here:
    # replacing that producer helper must not authenticate changed source bytes.
    paths=set()
    for folder in ('src','tools','scripts','tests'):paths.update((root/folder).rglob('*.py'))
    paths.update((root/'configs').rglob('*.json'));paths.update(root.glob('*.py'))
    if (root/'pytest.ini').is_file():paths.add(root/'pytest.ini')
    observed={path.relative_to(root).as_posix():hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(paths) if '__pycache__' not in path.parts and not path.is_symlink()}
    if observed!=expected:raise ValueError('Minute actual source bytes/inventory differ from admission')
    return value


def resolve_source_freeze(env):
    """Resolve a bound minute declaration; confer no source/data permissions."""
    reference=getattr(env,'minute1_source_admission',None)
    frozen=getattr(env,'minute1_source_freeze',None)
    if type(frozen) is not dict:raise ValueError('Explicit minute source freeze required')
    verify_source_admission(env.root,reference,source_hashes=frozen)
    return deepcopy(frozen)
