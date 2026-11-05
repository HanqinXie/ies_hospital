"""Bounded journal evidence contracts, with arithmetic independent of a simulator."""
from copy import deepcopy
import gzip
import hashlib
import importlib
import json
from pathlib import Path

import pytest


def api():
    assert (Path(__file__).parents[1]/'tools/annual_readiness/journal.py').is_file(), 'continuous evidence journal is not implemented'
    return importlib.import_module('tools.annual_readiness.journal')


IDENTITY = {'case_id': 'research_only', 'source_sha256': 'frozen-source', 'input_sha256': 'frozen-input', 'DC_energy_kWh': None}


def receipt(initial, final, charged, discharged, standing=.2, conversion=.3):
    return dict(initial_kWh=initial, final_kWh=final, charged_input_kWh=charged,
        discharged_kWh=discharged, standing_loss_kWh=standing, conversion_loss_kWh=conversion)


def segment(start, end, *, request=12., delivered=9., cut=1., connected=2., storage=None):
    rows = {k: [[0., 0., 0.] for _ in range(4)] for k in
        ('original_demand_kW', 'delivered_kW', 'active_cut_kW', 'connected_unserved_kW')}
    for field, value in zip(rows, (request, delivered, cut, connected)):
        rows[field][0][0] = value
    rows['original_demand_kW'][1][1] = 8.
    rows['delivered_kW'][1][1] = 4.
    rows['active_cut_kW'][1][1] = 2.
    rows['connected_unserved_kW'][1][1] = 2.
    return dict(start_minute=start, end_minute=end, service_request_ledger=rows,
        m21_storage={name: deepcopy(storage) for name in ('ups', 'cold_store')},
        extra_actual_context={'unknown': None, 'producer_numeric_zero': 0.})


def block(start=0., end=15., *, segments=None):
    return dict(start_minute=start, end_minute=end,
        segments=segments if segments is not None else [segment(start, end, storage=receipt(10., 8., 0., 1.5))],
        solver_calls=[{'success': False, 'exception': {'type': 'infeasible', 'context': [1, None]}},
                      {'success': True, 'candidate': {'arbitrary': ['untrimmed', None]}}],
        events=[{'kind': 'actual_event', 'details': {'missing': None}}],
        decision={'action': 2, 'not_a_terminal_snapshot': True},
        terminal={'snapshot_time_minute': end, 'inventories': {'ups': {'stock_kWh': 8.}}, 'pending': [None]})


def make(tmp_path):
    return api().BlockJournal(tmp_path/'journal', identity=deepcopy(IDENTITY))


def test_append_persists_full_failed_attempt_context_and_immutable_identity(tmp_path):
    identity = deepcopy(IDENTITY)
    journal = api().BlockJournal(tmp_path/'journal', identity=identity)
    identity['source_sha256'] = 'caller-later-mutated'
    raw = block()
    original = deepcopy(raw)
    receipt_out = journal.append(raw)
    raw['solver_calls'].clear()
    assert raw != original
    with gzip.open(tmp_path/'journal'/receipt_out['file'], 'rt', encoding='utf8') as stream:
        assert json.load(stream) == original
    summary = journal.close(status='completed', expected_end_minute=15.)
    assert summary['identity'] == IDENTITY
    assert summary['solver_call_count'] == 2
    assert summary['observation']['complete'] is True
    assert summary['native_material_classification'] is None
    assert summary['native_material_classification_reason']


def test_unequal_segments_and_cross_block_inventory_have_independent_expected_totals(tmp_path):
    journal = make(tmp_path)
    first = segment(0., 5., storage=receipt(10., 8., 0., 1.5))
    second = segment(5., 15., request=6., delivered=2., cut=3., connected=1., storage=receipt(8., 9., 2., .5))
    journal.append(block(segments=[first, second]))
    journal.append(block(15., 30., segments=[segment(15., 30., request=4., delivered=3., cut=.25, connected=.75,
        storage=receipt(9., 7., 0., 1.5))]))
    out = journal.close(status='completed', expected_end_minute=30.)
    electric = out['carrier_service']['P0']['e']
    assert electric['requested_kWh'] == pytest.approx(3.)
    assert electric['delivered_kWh'] == pytest.approx(11./6.)
    assert electric['ENS_kWh'] == pytest.approx(7./6.)
    assert electric['active_cut_kWh'] == pytest.approx(31./48.)
    assert electric['connected_unserved_kWh'] == pytest.approx(25./48.)
    heat = out['carrier_service']['P1']['h']
    assert heat['requested_kWh'] == pytest.approx(4.)
    assert heat['delivered_kWh'] == pytest.approx(2.)
    for name in ('ups', 'cold_store'):
        store = out['storage'][name]
        assert store['initial_stock_kWh'] == 10.
        assert store['final_stock_kWh'] == 7.
        assert store['minimum_stock_kWh'] == 7.
        assert store['charged_input_kWh'] == 2.
        assert store['discharged_kWh'] == 3.5
        assert store['standing_loss_kWh'] == pytest.approx(.6)
        assert store['conversion_loss_kWh'] == pytest.approx(.9)
        assert store['energy_residual_kWh'] == pytest.approx(0.)
        assert store['receipt_chain_max_gap_kWh'] == 0.
    assert out['block_count'] == 2 and out['segment_count'] == 3
    assert out['observation']['unknown_tail_minutes'] == 0.


def test_missing_values_stay_unknown_without_destroying_other_measured_fields(tmp_path):
    journal = make(tmp_path)
    raw = block()
    raw['segments'][0]['service_request_ledger']['delivered_kW'][0][0] = None
    raw['segments'][0]['m21_storage']['ups']['charged_input_kWh'] = None
    journal.append(raw)
    out = journal.close(status='failed', expected_end_minute=45., error={'type': 'actual_solver_failure', 'message': 'do not erase'})
    e = out['carrier_service']['P0']['e']
    assert e['requested_kWh'] == 3.
    assert e['delivered_kWh'] is None and e['ENS_kWh'] is None
    assert e['active_cut_kWh'] == .25
    assert out['storage']['ups']['charged_input_kWh'] is None
    assert out['storage']['ups']['energy_residual_kWh'] is None
    assert out['observation']['observed_until_minute'] == 15.
    assert out['observation']['unknown_tail_minutes'] == 30.
    assert out['observation']['complete'] is False
    assert out['error']['type'] == 'actual_solver_failure'
    assert out['unobserved_tail_service'] is None


def test_stock_discontinuity_and_nonclosing_energy_are_reported_not_repaired(tmp_path):
    journal = make(tmp_path)
    journal.append(block())
    journal.append(block(15., 30., segments=[segment(15., 30., storage=receipt(9., 7.5, 0., 1.5))]))
    store = journal.close(status='completed', expected_end_minute=30.)['storage']['ups']
    assert store['receipt_chain_max_gap_kWh'] == 1.
    assert store['energy_residual_kWh'] == pytest.approx(-1.5)
    assert store['segment_energy_max_absolute_residual_kWh'] == pytest.approx(.5)


@pytest.mark.parametrize('bad', [
    block(1., 15.), block(0., 0.), block(0., float('inf')),
    block(segments=[segment(0., 5.), segment(6., 15.)]),
    block(segments=[segment(0., 8.), segment(7., 15.)]),
    block(segments=[segment(0., 14.)]), block(segments=[]),
])
def test_invalid_first_block_or_segment_coverage_is_never_accepted(tmp_path, bad):
    journal = make(tmp_path)
    with pytest.raises(ValueError):
        journal.append(bad)
    out = journal.close(status='failed', expected_end_minute=15.)
    assert out['block_count'] == 0
    assert out['observation']['unknown_tail_minutes'] == 15.


def test_cross_block_gap_overlap_and_repeated_close_refused(tmp_path):
    journal = make(tmp_path)
    journal.append(block())
    for bad in (block(16., 30.), block(14., 30.), block()):
        with pytest.raises(ValueError):
            journal.append(bad)
    journal.close(status='completed', expected_end_minute=15.)
    with pytest.raises(RuntimeError):
        journal.close(status='completed', expected_end_minute=15.)
    with pytest.raises(RuntimeError):
        journal.append(block(15., 30.))


def test_existing_output_is_exclusive_even_when_empty(tmp_path):
    output = tmp_path/'journal'
    output.mkdir()
    with pytest.raises(FileExistsError):
        api().BlockJournal(output, identity=IDENTITY)
    assert list(output.iterdir()) == []


def test_sha_chain_verification_checks_raw_bytes_and_clocks(tmp_path):
    journal = make(tmp_path)
    journal.append(block())
    journal.append(block(15., 30.))
    summary = journal.close(status='completed', expected_end_minute=30.)
    checked = api().verify_journal(tmp_path/'journal')
    assert checked['block_count'] == 2
    assert checked['observed_until_minute'] == 30.
    records = [json.loads(line) for line in (tmp_path/'journal'/'blocks.jsonl').read_text(encoding='utf8').splitlines()]
    assert records[0]['previous_digest'] is None
    assert records[1]['previous_digest'] == records[0]['digest']
    assert summary['raw_files_sha256'][0]['sha256'] == hashlib.sha256((tmp_path/'journal'/records[0]['file']).read_bytes()).hexdigest()
    with (tmp_path/'journal'/records[0]['file']).open('ab') as stream:
        stream.write(b'tampering')
    with pytest.raises(ValueError, match='SHA|sha|hash'):
        api().verify_journal(tmp_path/'journal')


def test_identity_file_tampering_cannot_enter_a_successful_close(tmp_path):
    journal = make(tmp_path)
    journal.append(block())
    (tmp_path/'journal'/'IDENTITY.json').write_text('{}', encoding='utf8')
    with pytest.raises(ValueError, match='identity|Identity'):
        journal.close(status='completed', expected_end_minute=15.)
    assert not (tmp_path/'journal'/'SUMMARY.json').exists()


def test_write_failure_retains_only_committed_prefix_and_unknown_tail(tmp_path, monkeypatch):
    journal = make(tmp_path)
    journal.append(block())
    import os
    original = os.fsync
    def fail_fsync(fd):
        raise OSError('injected full disk / flush failure')
    with monkeypatch.context() as context:
        context.setattr(os, 'fsync', fail_fsync)
        with pytest.raises(OSError):
            journal.append(block(15., 30.))
    assert os.fsync is original
    with pytest.raises(RuntimeError, match='failed|failure'):
        journal.append(block(15., 30.))
    with pytest.raises(RuntimeError, match='failed|failure'):
        journal.close(status='completed', expected_end_minute=30.)
    summary = journal.close(status='failed', expected_end_minute=30., error={'message': 'disk failure'})
    assert summary['block_count'] == 1
    assert summary['observation']['unknown_tail_minutes'] == 15.
    assert summary['journal_write_error']['type'] == 'OSError'
    assert summary['uncommitted_files']
    assert summary['integrity_verified'] is False


def test_no_blocks_does_not_invent_zero_service_or_storage(tmp_path):
    journal = make(tmp_path)
    summary = journal.close(status='failed', expected_end_minute=15., error={'message': 'failed before first decision'})
    assert summary['carrier_service']['P0']['e']['requested_kWh'] is None
    assert summary['storage']['ups']['initial_stock_kWh'] is None
    assert summary['observation']['unknown_tail_minutes'] == 15.


def test_nonfinite_payload_is_refused_without_erasing_prior_blocks(tmp_path):
    journal = make(tmp_path)
    journal.append(block())
    bad = block(15., 30.)
    bad['solver_calls'][0]['diagnostic_value'] = float('nan')
    with pytest.raises(ValueError):
        journal.append(bad)
    assert journal.close(status='failed', expected_end_minute=30.)['block_count'] == 1


@pytest.mark.parametrize('field', ['carrier_service', 'storage', 'observation'])
def test_verifier_rejects_summary_arithmetic_or_coverage_tampering(tmp_path, field):
    journal = make(tmp_path)
    journal.append(block())
    journal.close(status='completed', expected_end_minute=15.)
    path = tmp_path/'journal'/'SUMMARY.json'
    summary = json.loads(path.read_text(encoding='utf8'))
    if field == 'carrier_service':
        summary[field]['P0']['e']['ENS_kWh'] = 0.
    elif field == 'storage':
        summary[field]['ups']['final_stock_kWh'] = 999.
    else:
        summary[field]['observed_until_minute'] = 0.
    path.write_text(json.dumps(summary), encoding='utf8')
    with pytest.raises(ValueError, match='SUMMARY|summary'):
        api().verify_journal(tmp_path/'journal')


def test_unclosed_index_after_failed_fsync_is_not_accepted_by_verifier(tmp_path, monkeypatch):
    journal = make(tmp_path)
    journal.append(block())
    import os
    original = os.fsync
    calls = 0
    def fail_second_fsync(fd):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError('index flush failed after writing record')
        return original(fd)
    with monkeypatch.context() as context:
        context.setattr(os, 'fsync', fail_second_fsync)
        with pytest.raises(OSError):
            journal.append(block(15., 30.))
    with pytest.raises(ValueError, match='closed|committed|failure'):
        api().verify_journal(tmp_path/'journal')
    out = journal.close(status='failed', expected_end_minute=30.)
    assert out['block_count'] == 1
    assert out['uncommitted_index_record_count'] == 1
    assert out['observation']['unknown_tail_minutes'] == 15.


def test_summary_flush_failure_does_not_publish_success_and_closes_append_path(tmp_path, monkeypatch):
    journal = make(tmp_path)
    journal.append(block())
    import os
    def fail_fsync(fd):
        raise OSError('summary flush failed')
    with monkeypatch.context() as context:
        context.setattr(os, 'fsync', fail_fsync)
        with pytest.raises(OSError):
            journal.close(status='completed', expected_end_minute=15.)
    assert not (tmp_path/'journal'/'SUMMARY.json').exists()
    with pytest.raises(ValueError, match='closed|committed|failure'):
        api().verify_journal(tmp_path/'journal')
    with pytest.raises(RuntimeError, match='failed|failure'):
        journal.append(block(15., 30.))
    out = journal.close(status='failed', expected_end_minute=30., error={'message': 'summary flush failed'})
    assert out['observation']['unknown_tail_minutes'] == 15.
    assert out['journal_write_error']['type'] == 'OSError'
    assert out['integrity_verified'] is False


def test_active_close_remains_bound_to_the_receipt_it_acknowledged(tmp_path):
    journal = make(tmp_path)
    acknowledged = journal.append(block())
    output = tmp_path/'journal'
    raw_path = output/acknowledged['file']
    with gzip.open(raw_path, 'rt', encoding='utf8') as stream:
        substituted = json.load(stream)
    substituted['segments'][0]['service_request_ledger']['delivered_kW'][0][0] = 8.
    raw_path.write_bytes(gzip.compress(json.dumps(substituted).encode('utf8'), mtime=0))
    altered = deepcopy(acknowledged)
    altered['sha256'] = hashlib.sha256(raw_path.read_bytes()).hexdigest()
    metadata = {k: v for k, v in altered.items() if k != 'digest'}
    altered['digest'] = hashlib.sha256((json.dumps(metadata, sort_keys=True, ensure_ascii=False,
        allow_nan=False, separators=(',', ':'))+'\n').encode('utf8')).hexdigest()
    (output/'blocks.jsonl').write_text(json.dumps(altered)+'\n', encoding='utf8')
    with pytest.raises(ValueError, match='acknowledged|receipt|digest'):
        journal.close(status='completed', expected_end_minute=15.)
