"""Controlled, metadata-only journal resume; no environment or physical solve."""
import copy
import hashlib
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.annual_readiness import journal as legacy


IDENTITY = dict(case_id='annual_synthetic', source_sha256='fixed-source',
                input_sha256='fixed-input', config_sha256='fixed-config', unknown=None)
BINDING = dict(source={'code': 'fixed-source'}, inputs={'loads': 'fixed-input'},
               config={'hardware': 'H2', 'rule': 'M_ready'})


def api():
    try:
        return importlib.import_module('tools.annual_program.journal')
    except ModuleNotFoundError as exc:
        if exc.name != 'tools.annual_program.journal':
            raise
        pytest.fail('Controlled acknowledged-prefix journal resume is missing', pytrace=False)


def block(start=0., end=15.):
    matrix = [[1., 2., 3.] for _ in range(4)]
    return dict(start_minute=start, end_minute=end,
        segments=[dict(start_minute=start, end_minute=end, service_request_ledger={
            key: copy.deepcopy(matrix) for key in ('original_demand_kW', 'delivered_kW',
                                                   'active_cut_kW', 'connected_unserved_kW')})],
        solver_calls=[dict(success=False, known_failure=True)],
        events=[dict(kind='synthetic_event', missing=None)],
        decision=dict(action=2), terminal=dict(time_minute=end, inventory=None))


def opened(tmp_path, name='journal'):
    return legacy.BlockJournal(tmp_path/name, identity=copy.deepcopy(IDENTITY))


def file_bytes(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in Path(root).rglob('*') if p.is_file()}


def test_checkpoint_receipt_authenticates_an_open_acknowledged_prefix_without_summary(tmp_path):
    journal = opened(tmp_path)
    acknowledged = journal.append(block())
    before = file_bytes(journal.output)
    receipt = api().checkpoint_receipt(journal)
    assert receipt['block_count'] == 1
    assert receipt['observed_until_minute'] == 15.
    assert receipt['last_digest'] == acknowledged['digest']
    assert receipt['identity_sha256'] == legacy._sha(journal.output/'IDENTITY.json')
    assert receipt['index_sha256'] == legacy._sha(journal.output/'blocks.jsonl')
    assert receipt['digest'] == legacy._record_digest(receipt)
    assert file_bytes(journal.output) == before
    assert not (journal.output/'SUMMARY.json').exists()


def test_interrupted_reopen_append_matches_uninterrupted_raw_chain_and_final_summary(tmp_path, monkeypatch):
    uninterrupted = opened(tmp_path, 'uninterrupted')
    interrupted = opened(tmp_path, 'interrupted')
    first, second = block(), block(15., 30.)
    uninterrupted.append(first)
    uninterrupted.append(second)
    uninterrupted.close(status='completed', expected_end_minute=30.)
    interrupted.append(first)
    prefix = file_bytes(interrupted.output)
    receipt = api().checkpoint_receipt(interrupted)
    def forbidden(*args, **kwargs):
        raise AssertionError('Resume must not call BlockJournal constructor')
    monkeypatch.setattr(legacy.BlockJournal, '__init__', forbidden)
    resumed = api().reopen_journal(interrupted.output, receipt, IDENTITY)
    assert type(resumed) is legacy.BlockJournal
    assert resumed._count == 1 and resumed._end == 15.
    assert resumed._last_digest == receipt['last_digest']
    assert resumed._close_attempt_count == 0
    assert file_bytes(resumed.output) == prefix
    acknowledgement = resumed.append(second)
    assert acknowledgement['index'] == 1 and acknowledgement['file'].endswith('block_000001.json.gz')
    resumed.close(status='completed', expected_end_minute=30.)
    assert file_bytes(resumed.output) == file_bytes(uninterrupted.output)
    assert legacy.verify_journal(resumed.output)['block_count'] == 2


def test_empty_acknowledged_prefix_reopens_without_resetting_future_file_numbers(tmp_path):
    journal = opened(tmp_path)
    receipt = api().checkpoint_receipt(journal)
    assert receipt['block_count'] == 0 and receipt['last_digest'] is None
    resumed = api().reopen_journal(journal.output, receipt, IDENTITY)
    assert resumed.append(block())['index'] == 0


@pytest.mark.parametrize('field,value', [('_count', 2), ('_end', 16.),
    ('_last_digest', '0'*64), ('_identity', {'different': True}),
    ('_write_error', {'phase': 'failed'}), ('_closed', True), ('_close_attempt_count', 1)])
def test_live_receipt_rejects_unacknowledged_or_unhealthy_writer_state(tmp_path, field, value):
    journal = opened(tmp_path)
    journal.append(block())
    setattr(journal, field, value)
    before = file_bytes(journal.output)
    with pytest.raises((ValueError, RuntimeError)):
        api().checkpoint_receipt(journal)
    assert file_bytes(journal.output) == before


@pytest.mark.parametrize('mutation', [
    lambda root: (root/'blocks/block_000000.json.gz').write_bytes(b'corrupt raw'),
    lambda root: (root/'blocks/block_000000.json.gz').unlink(),
    lambda root: (root/'IDENTITY.json').write_bytes(legacy._json({'different': True})),
    lambda root: (root/'blocks.jsonl').write_bytes((root/'blocks.jsonl').read_bytes()+b'{"partial":'),
    lambda root: (root/'blocks/orphan.json.gz').write_bytes(b'preserve orphan'),
    lambda root: (root/'SUMMARY.json').write_bytes(b'preserve summary'),
    lambda root: (root/'SUMMARY.prepare_000000.json').write_bytes(b'preserve failed close'),
])
def test_resume_rejects_corruption_missing_data_or_orphan_evidence_without_deleting_it(tmp_path, mutation):
    journal = opened(tmp_path)
    journal.append(block())
    receipt = api().checkpoint_receipt(journal)
    mutation(journal.output)
    before = file_bytes(journal.output)
    with pytest.raises((ValueError, FileNotFoundError)):
        api().reopen_journal(journal.output, receipt, IDENTITY)
    assert file_bytes(journal.output) == before


def test_newer_acknowledged_prefix_is_not_silently_truncated_to_an_old_checkpoint(tmp_path):
    journal = opened(tmp_path)
    journal.append(block())
    receipt = api().checkpoint_receipt(journal)
    journal.append(block(15., 30.))
    before = file_bytes(journal.output)
    with pytest.raises(ValueError):
        api().reopen_journal(journal.output, receipt, IDENTITY)
    assert file_bytes(journal.output) == before


def test_expected_identity_and_current_implementation_must_match_the_receipt(tmp_path, monkeypatch):
    journal = opened(tmp_path)
    journal.append(block())
    receipt = api().checkpoint_receipt(journal)
    with pytest.raises(ValueError):
        api().reopen_journal(journal.output, receipt, dict(IDENTITY, config_sha256='changed'))
    actual = api()._code_binding()
    actual['tools/annual_readiness/journal.py'] = '0'*64
    monkeypatch.setattr(api(), '_code_binding', lambda: actual)
    with pytest.raises(ValueError):
        api().reopen_journal(journal.output, receipt, IDENTITY)


def test_code_drift_during_full_prefix_authentication_is_also_rejected(tmp_path, monkeypatch):
    journal = opened(tmp_path)
    journal.append(block())
    receipt = api().checkpoint_receipt(journal)
    original = api()._code_binding()
    changed = dict(original, **{'tools/annual_program/journal.py': '0'*64})
    calls = []
    def evolving_code():
        calls.append(True)
        return original if len(calls) == 1 else changed
    monkeypatch.setattr(api(), '_code_binding', evolving_code)
    with pytest.raises(ValueError):
        api().reopen_journal(journal.output, receipt, IDENTITY)


def test_an_orphan_created_at_the_end_of_scan_is_retained_and_rejected(tmp_path, monkeypatch):
    journal = opened(tmp_path)
    journal.append(block())
    receipt = api().checkpoint_receipt(journal)
    original_scan = api()._scan
    def interrupted_scan(*args, **kwargs):
        result = original_scan(*args, **kwargs)
        (journal.output/'blocks/new_orphan.json.gz').write_bytes(b'preserve new orphan')
        return result
    monkeypatch.setattr(api(), '_scan', interrupted_scan)
    with pytest.raises(ValueError):
        api().reopen_journal(journal.output, receipt, IDENTITY)
    assert (journal.output/'blocks/new_orphan.json.gz').read_bytes() == b'preserve new orphan'


@pytest.mark.parametrize('field,value', [('block_count', True), ('block_count', -1),
    ('observed_until_minute', 16.), ('last_digest', '0'*64),
    ('identity_sha256', '0'*64), ('index_sha256', '0'*64),
    ('index_bytes', 1), ('digest', '0'*64), ('unregistered_right', True)])
def test_receipt_tampering_cannot_reopen_another_prefix(tmp_path, field, value):
    journal = opened(tmp_path)
    journal.append(block())
    receipt = api().checkpoint_receipt(journal)
    receipt[field] = value
    # A checksum is no authorization; compare even re-signed data to actual files.
    if field != 'digest':
        receipt['digest'] = legacy._record_digest(receipt)
    with pytest.raises(ValueError):
        api().reopen_journal(journal.output, receipt, IDENTITY)


def checkpoint_file(tmp_path, *, minute=15., block_count=1):
    from tools.annual_program import checkpoint
    env = SimpleNamespace()
    core = SimpleNamespace(env=env, time=minute, _m23_advance_depth=0, failed=False)
    env.core = core
    controller = SimpleNamespace(history=[None])
    plan = dict(env=dict(core='alias:core'), core=dict(env='alias:env', time='state',
        _m23_advance_depth='state', failed='state'), controller=dict(history='state'))
    document = checkpoint.capture_checkpoint(env, controller, binding=BINDING,
        field_plan=plan, runner_state=dict(committed_end_minute=minute, block_count=block_count))
    path = tmp_path/'quiescent_checkpoint.json'
    checkpoint.save_checkpoint(document, path)
    return path, document


def test_atomic_publication_binds_verified_checkpoint_bytes_content_and_journal_prefix(tmp_path):
    journal = opened(tmp_path)
    journal.append(block())
    receipt = api().checkpoint_receipt(journal)
    checkpoint_path, document = checkpoint_file(tmp_path)
    marker_path = tmp_path/'PUBLISHED_CHECKPOINT.json'
    marker = api().publish_checkpoint(checkpoint_path, receipt, marker_path)
    assert marker == json.loads(marker_path.read_text(encoding='utf8'))
    assert marker['checkpoint_sha256'] == legacy._sha(checkpoint_path)
    assert marker['checkpoint_content_sha256'] == document['content_sha256']
    assert marker['journal_receipt'] == receipt
    assert marker['digest'] == legacy._record_digest(marker)
    assert marker['journal_receipt_sha256'] == hashlib.sha256(legacy._json(receipt)).hexdigest()
    assert marker_path.with_name(marker_path.name+'.prepare').read_bytes() == marker_path.read_bytes()
    before = marker_path.read_bytes()
    with pytest.raises(FileExistsError):
        api().publish_checkpoint(checkpoint_path, receipt, marker_path)
    assert marker_path.read_bytes() == before


@pytest.mark.parametrize('kind', ['wrong_clock', 'wrong_block_count', 'corrupt_content', 'newer_journal'])
def test_publication_rejects_mismatched_checkpoint_or_prefix_before_creating_a_marker(tmp_path, kind):
    journal = opened(tmp_path)
    journal.append(block())
    receipt = api().checkpoint_receipt(journal)
    checkpoint_path, _ = checkpoint_file(tmp_path,
        minute=30. if kind=='wrong_clock' else 15.,
        block_count=2 if kind=='wrong_block_count' else 1)
    if kind=='corrupt_content':
        document = json.loads(checkpoint_path.read_text(encoding='utf8'))
        document['minute'] = 99.
        checkpoint_path.write_text(json.dumps(document), encoding='utf8')
    if kind=='newer_journal':
        journal.append(block(15., 30.))
    path = tmp_path/'PUBLISHED.json'
    with pytest.raises(ValueError):
        api().publish_checkpoint(checkpoint_path, receipt, path)
    assert not path.exists() and not path.with_name(path.name+'.prepare').exists()


def test_failed_atomic_link_keeps_prepare_evidence_and_never_publishes_or_overwrites(tmp_path, monkeypatch):
    journal = opened(tmp_path)
    journal.append(block())
    receipt = api().checkpoint_receipt(journal)
    checkpoint_path, _ = checkpoint_file(tmp_path)
    path = tmp_path/'PUBLISHED.json'
    def fail(*args, **kwargs):
        raise OSError('Synthetic atomic-link failure')
    monkeypatch.setattr(api().os, 'link', fail)
    with pytest.raises(OSError):
        api().publish_checkpoint(checkpoint_path, receipt, path)
    prepared = path.with_name(path.name+'.prepare')
    assert prepared.exists() and not path.exists()
    before = prepared.read_bytes()
    with pytest.raises(FileExistsError):
        api().publish_checkpoint(checkpoint_path, receipt, path)
    assert prepared.read_bytes() == before
