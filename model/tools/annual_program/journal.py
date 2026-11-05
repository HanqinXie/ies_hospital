"""Controlled resume of the exact durable, acknowledged BlockJournal prefix.

This is a local scientific integrity protocol, not cryptographic identity or an
arbitrary-crash recovery system. One quiescent writer owns a journal. Unexpected
newer, partial or orphan evidence is rejected and retained, never truncated.
The original append/close implementation and raw block format are reused.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re

from tools.annual_readiness import journal as legacy
from tools.annual_readiness.journal import BlockJournal, _scan, _sha, _json, _record_digest


SCHEMA = 'hospital_ies.annual_program.journal_checkpoint.v1'
_FIELDS = frozenset(('schema', 'journal_path', 'implementation_sha256', 'block_count',
    'observed_until_minute', 'last_digest', 'identity_sha256', 'index_sha256',
    'index_bytes', 'digest'))


def _code_binding():
    return {'tools/annual_readiness/journal.py': _sha(legacy.__file__),
            'tools/annual_program/journal.py': _sha(__file__)}


def _sha_value(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None


def _receipt_shape(receipt):
    if not isinstance(receipt, dict) or set(receipt) != _FIELDS or receipt.get('schema') != SCHEMA:
        raise ValueError('Incomplete or unrecognized journal checkpoint receipt')
    count, end = receipt['block_count'], receipt['observed_until_minute']
    if (type(count) is not int or count < 0 or type(end) not in (int, float)
            or not math.isfinite(end) or end < 0.):
        raise ValueError('Invalid acknowledged journal count or clock')
    if ((count == 0 and (end != 0. or receipt['last_digest'] is not None))
            or (count > 0 and (end <= 0. or not _sha_value(receipt['last_digest'])))):
        raise ValueError('Acknowledged journal count, clock and last digest disagree')
    if (type(receipt['index_bytes']) is not int or receipt['index_bytes'] < 0
            or any(not _sha_value(receipt[k]) for k in ('identity_sha256', 'index_sha256', 'digest'))):
        raise ValueError('Invalid exact journal index or identity binding')
    if receipt['digest'] != _record_digest(receipt):
        raise ValueError('Journal checkpoint receipt digest mismatch')


def _reject_closed_or_special_files(output):
    if not output.is_dir() or not (output/'blocks').is_dir():
        raise ValueError('Existing journal and blocks directories are required')
    if any(path.name.upper().startswith('SUMMARY') for path in output.iterdir()):
        raise ValueError('Closed or attempted-summary evidence cannot be reopened')
    for path in (output, output/'blocks', output/'IDENTITY.json', output/'blocks.jsonl'):
        if path.is_symlink():
            raise ValueError('Journal paths must retain their original local file identity')
    if any(path.is_symlink() for path in (output/'blocks').iterdir()):
        raise ValueError('Journal raw files cannot be substituted with links')


def _authenticate_prefix(output, receipt, expected_identity):
    _receipt_shape(receipt)
    output = Path(output).resolve()
    if receipt['journal_path'] != str(output):
        raise ValueError('Journal checkpoint belongs to another output directory')
    if receipt['implementation_sha256'] != _code_binding():
        raise ValueError('Journal implementation code drift prevents acknowledged resume')
    if not isinstance(expected_identity, dict):
        raise ValueError('Expected journal identity must be an explicit object')
    identity_sha = hashlib.sha256(_json(expected_identity)).hexdigest()
    if identity_sha != receipt['identity_sha256']:
        raise ValueError('Expected journal identity differs from the acknowledged prefix')
    _reject_closed_or_special_files(output)
    index = output/'blocks.jsonl'
    if index.stat().st_size != receipt['index_bytes'] or _sha(index) != receipt['index_sha256']:
        raise ValueError('Journal index is newer, partial or changed; no prefix truncation is allowed')
    scan = _scan(output, expected_identity_sha256=identity_sha, committed_limit=receipt['block_count'])
    if scan['uncommitted_files'] or scan['uncommitted_index_record_count']:
        raise ValueError('Uncommitted or orphan journal evidence prevents controlled resume')
    if (scan['identity'] != expected_identity or scan['block_count'] != receipt['block_count']
            or scan['observed_until_minute'] != receipt['observed_until_minute']
            or scan['last_digest'] != receipt['last_digest']):
        raise ValueError('Journal prefix differs from acknowledged identity, count, clock or digest')
    # Catch a writer advancing while the full raw-prefix scan was running.
    if index.stat().st_size != receipt['index_bytes'] or _sha(index) != receipt['index_sha256']:
        raise ValueError('Journal changed during prefix authentication; require one quiescent writer')
    _reject_closed_or_special_files(output)
    actual_raw = {p.relative_to(output).as_posix() for p in (output/'blocks').iterdir()}
    if actual_raw != {row['file'] for row in scan['raw_files_sha256']}:
        raise ValueError('Raw journal evidence changed during prefix authentication')
    if receipt['implementation_sha256'] != _code_binding():
        raise ValueError('Journal implementation code changed during prefix authentication')
    return scan


def recovery_receipt(journal):
    """Receipt for the explicitly locked incremental recovery archiver only.

    The archiver authenticates each new raw/index interval before COMMIT. Unlike
    the legacy publication API, this does not reread the complete prefix. Full
    prefix verification remains mandatory when materializing/restoring a branch.
    """
    if (type(journal) is not BlockJournal or journal._closed
            or journal._write_error is not None or journal._close_attempt_count):
        raise ValueError('Incremental recovery requires one healthy exclusive open journal')
    journal._check_open()
    index=Path(journal.output)/'blocks.jsonl'
    value=dict(schema=SCHEMA+'.incremental_writer_receipt.v1',
        journal_path=str(Path(journal.output).resolve()),implementation_sha256=_code_binding(),
        block_count=journal._count,observed_until_minute=journal._end,
        last_digest=journal._last_digest,identity_sha256=journal._identity_sha,
        index_bytes=index.stat().st_size)
    value['digest']=_record_digest(value)
    return value


def checkpoint_receipt(journal):
    """Authenticate only the prefix already acknowledged after native fsync.

    Call at a controlled post-append boundary. Native append acknowledgements
    follow raw/index fsync; a failed/closed writer or attempted close is rejected.
    No summary is required and no journal bytes are written by this function.
    """
    if type(journal) is not BlockJournal:
        raise ValueError('The original exclusive BlockJournal writer is required')
    if journal._closed or journal._write_error is not None or journal._close_attempt_count != 0:
        raise ValueError('An open, healthy writer with no attempted close is required')
    journal._check_open()
    output = Path(journal.output).resolve()
    receipt = dict(schema=SCHEMA, journal_path=str(output), implementation_sha256=_code_binding(),
        block_count=journal._count, observed_until_minute=journal._end,
        last_digest=journal._last_digest, identity_sha256=journal._identity_sha,
        index_sha256=_sha(output/'blocks.jsonl'), index_bytes=(output/'blocks.jsonl').stat().st_size)
    receipt['digest'] = _record_digest(receipt)
    _authenticate_prefix(output, receipt, journal._identity)
    if (journal._count != receipt['block_count'] or journal._end != receipt['observed_until_minute']
            or journal._last_digest != receipt['last_digest'] or journal._closed
            or journal._write_error is not None or journal._close_attempt_count != 0):
        raise ValueError('Writer changed during checkpoint acknowledgement')
    return copy.deepcopy(receipt)


def reopen_journal(output, checkpoint_receipt, expected_identity):
    """Return an original BlockJournal without invoking its constructor.

    The caller relinquishes any previous writer and supplies its frozen expected
    identity. Every acknowledged raw block is reread; extra evidence is rejected.
    The native next filename/count/hash chain continue from the exact receipt.
    """
    scan = _authenticate_prefix(output, checkpoint_receipt, expected_identity)
    journal = object.__new__(BlockJournal)
    journal.output = Path(output).resolve()
    journal._identity_sha = scan['identity_sha256']
    journal._identity = copy.deepcopy(scan['identity'])
    journal._count = scan['block_count']
    journal._end = scan['observed_until_minute']
    journal._last_digest = scan['last_digest']
    journal._closed = False
    journal._write_error = None
    journal._close_attempt_count = 0
    return journal


def _checkpoint_proof(path, receipt):
    from . import checkpoint
    path = Path(path).resolve()
    if path.is_symlink() or not path.is_file():
        raise ValueError('Checkpoint publication requires an existing local checkpoint file')
    raw = path.read_bytes()
    supplied = json.loads(raw)
    if not isinstance(supplied, dict):
        raise ValueError('Checkpoint content must be an object')
    document = checkpoint.load_checkpoint(path, expected_binding=supplied.get('binding', {}))
    sha = hashlib.sha256(raw).hexdigest()
    if document != supplied or _sha(path) != sha:
        raise ValueError('Checkpoint bytes changed during full content verification')
    if document['minute'] != receipt['observed_until_minute']:
        raise ValueError('Checkpoint clock differs from the acknowledged journal prefix')
    runner = checkpoint._decode(document['runner_state'])
    if 'block_count' in runner and (type(runner['block_count']) is not int
            or runner['block_count'] != receipt['block_count']):
        raise ValueError('Checkpoint block count differs from the acknowledged journal prefix')
    return dict(checkpoint_path=str(path), checkpoint_sha256=sha, checkpoint_bytes=len(raw),
                checkpoint_content_sha256=document['content_sha256'])


def publish_checkpoint(checkpoint_path, journal_receipt, path):
    """Write-once atomic marker after full checkpoint and journal proof.

    A failed staging/link attempt remains evidence and blocks an implicit retry.
    The runner separately validates its frozen checkpoint source/input/config
    binding before restoring physical state. No cryptographic identity is claimed.
    """
    path = Path(path).resolve()
    staging = path.with_name(path.name+'.prepare')
    if path.exists() or staging.exists():
        raise FileExistsError('Checkpoint publication or retained prepare evidence already exists')
    _receipt_shape(journal_receipt)
    output = Path(journal_receipt['journal_path']).resolve()
    identity = json.loads((output/'IDENTITY.json').read_text(encoding='utf8'))
    _authenticate_prefix(output, journal_receipt, identity)
    proof = _checkpoint_proof(checkpoint_path, journal_receipt)
    if path.is_relative_to(output/'blocks') or (path.parent == output and path.name.upper().startswith('SUMMARY')):
        raise ValueError('A checkpoint marker cannot occupy raw-block or summary evidence paths')
    marker = dict(schema=SCHEMA+'.publication', publication_path=str(path), **proof,
        journal_receipt=copy.deepcopy(journal_receipt),
        journal_receipt_sha256=hashlib.sha256(_json(journal_receipt)).hexdigest(),
        cryptographic_identity_claim=False)
    marker['digest'] = _record_digest(marker)
    encoded = _json(marker)
    path.parent.mkdir(parents=True, exist_ok=True)
    legacy._write_exclusive(staging, encoded)
    expected_sha = hashlib.sha256(encoded).hexdigest()
    if _sha(staging) != expected_sha:
        raise ValueError('Persisted checkpoint publication staging SHA mismatch')
    _authenticate_prefix(output, journal_receipt, identity)
    if _checkpoint_proof(checkpoint_path, journal_receipt) != proof:
        raise ValueError('Checkpoint changed before atomic publication')
    os.link(staging, path)
    if _sha(path) != expected_sha:
        raise ValueError('Published checkpoint marker SHA mismatch')
    return copy.deepcopy(marker)
