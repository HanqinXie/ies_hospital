"""Durable, bounded-memory evidence blocks for an opt-in continuous run.

This module records JSON-compatible producer data without constructing a model,
controller, solver, or qualification claim.  Energy integrals cover only the
committed observed prefix. Missing numeric inputs remain ``None``. Native
material-interruption and recovery metrics require cross-block native state;
they are explicitly unknown here rather than approximated with new thresholds.
"""
from __future__ import annotations

import copy
import gzip
import hashlib
import json
import math
import os
from pathlib import Path

SCHEMA = 'hospital_ies.annual_readiness.block_journal.v1'
PRIORITIES = ('P0', 'P1', 'P2', 'P3')
CARRIERS = ('e', 'h', 'c')
_SERVICE_FIELDS = ('requested_kWh', 'delivered_kWh', 'active_cut_kWh', 'connected_unserved_kWh', 'ENS_kWh')
_STORAGE_FIELDS = ('charged_input_kWh', 'discharged_kWh', 'standing_loss_kWh', 'conversion_loss_kWh')


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _json(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
        separators=(',', ':'))+'\n').encode('utf8')


def _sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _write_exclusive(path, data):
    with Path(path).open('xb') as stream:
        if stream.write(data) != len(data):
            raise OSError('Incomplete journal file write')
        stream.flush()
        os.fsync(stream.fileno())


def _validate_block(block, previous_end):
    if not isinstance(block, dict):
        raise ValueError('A block must be a JSON object')
    for key, kind in (('segments', list), ('solver_calls', list), ('events', list),
                      ('decision', dict), ('terminal', dict)):
        if not isinstance(block.get(key), kind):
            raise ValueError('Missing or invalid block field: '+key)
    start, end = block.get('start_minute'), block.get('end_minute')
    if not all(_number(v) for v in (start, end)) or start != previous_end or end <= start:
        raise ValueError('Block clocks must be finite, positive-duration and consecutive from zero')
    cursor = start
    for segment in block['segments']:
        if not isinstance(segment, dict):
            raise ValueError('An actual segment must be a JSON object')
        a, b = segment.get('start_minute'), segment.get('end_minute')
        if not all(_number(v) for v in (a, b)) or a != cursor or b <= a or b > end:
            raise ValueError('Actual segments must cover the block without gaps or overlap')
        cursor = b
    if not block['segments'] or cursor != end:
        raise ValueError('Actual segments must cover the entire block')
    return end


def _record_digest(record):
    return hashlib.sha256(_json({k: v for k, v in record.items() if k != 'digest'})).hexdigest()


def _cell(matrix, priority, carrier):
    if not isinstance(matrix, list) or len(matrix) != 4:
        return None
    row = matrix[priority]
    if not isinstance(row, list) or len(row) != 3:
        return None
    value = row[carrier]
    return value if _number(value) else None


class _Total:
    """An empty or partially unknown sum is unknown, while known parts survive."""
    def __init__(self):
        self.count = 0
        self.known_count = 0
        self.known_total = 0.

    def add(self, value):
        self.count += 1
        if _number(value):
            self.known_count += 1
            self.known_total = math.fsum((self.known_total, value))

    def value(self):
        return self.known_total if self.count and self.count == self.known_count else None


class _Accumulator:
    def __init__(self):
        self.services = {p: {c: {key: _Total() for key in _SERVICE_FIELDS} for c in CARRIERS} for p in PRIORITIES}
        self.storage = {name: dict(count=0, initial=None, final=None, minimum=None,
            boundaries_complete=True, chain_complete=True, max_gap=0., segment_residual_complete=True,
            max_segment_residual=0., totals={key: _Total() for key in _STORAGE_FIELDS})
            for name in ('ups', 'cold_store')}
        self.segment_count = self.solver_call_count = self.event_count = 0
        self.terminal = None

    def add(self, block):
        self.solver_call_count += len(block['solver_calls'])
        self.event_count += len(block['events'])
        self.terminal = copy.deepcopy(block['terminal'])
        for segment in block['segments']:
            self.segment_count += 1
            dt = (segment['end_minute']-segment['start_minute'])/60.
            ledger = segment.get('service_request_ledger') or {}
            if not isinstance(ledger, dict):
                ledger = {}
            for ip, priority in enumerate(PRIORITIES):
                for ic, carrier in enumerate(CARRIERS):
                    request, delivered, cut, connected = [_cell(ledger.get(key), ip, ic) for key in
                        ('original_demand_kW', 'delivered_kW', 'active_cut_kW', 'connected_unserved_kW')]
                    ens = max(request-delivered, 0.) if all(_number(v) for v in (request, delivered)) else None
                    for key, value in zip(_SERVICE_FIELDS, (request, delivered, cut, connected, ens)):
                        self.services[priority][carrier][key].add(value*dt if _number(value) else None)
            raw_storage = segment.get('m21_storage') or {}
            for name, store in self.storage.items():
                receipt = raw_storage.get(name) if isinstance(raw_storage, dict) else None
                receipt = receipt if isinstance(receipt, dict) else {}
                initial, final = receipt.get('initial_kWh'), receipt.get('final_kWh')
                if store['count'] == 0:
                    store['initial'] = initial if _number(initial) else None
                elif all(_number(v) for v in (store['final'], initial)):
                    store['max_gap'] = max(store['max_gap'], abs(store['final']-initial))
                else:
                    store['chain_complete'] = False
                for value in (initial, final):
                    if _number(value):
                        store['minimum'] = value if store['minimum'] is None else min(store['minimum'], value)
                    else:
                        store['boundaries_complete'] = False
                terms = [initial]+[receipt.get(k) for k in _STORAGE_FIELDS]+[final]
                if all(_number(v) for v in terms):
                    residual = terms[0]+terms[1]-math.fsum(terms[2:])
                    store['max_segment_residual'] = max(store['max_segment_residual'], abs(residual))
                else:
                    store['segment_residual_complete'] = False
                for key, total in store['totals'].items():
                    total.add(receipt.get(key))
                store['final'] = final if _number(final) else None
                store['count'] += 1

    def export(self):
        service = {p: {c: dict(**{key: total.value() for key, total in totals.items()},
            carrier=c, unit='kWh_'+c, source='actual service_request_ledger; actual elapsed segment duration',
            missing_numeric_inputs={key: total.count-total.known_count for key, total in totals.items()},
            material_classification=None) for c, totals in carriers.items()} for p, carriers in self.services.items()}
        storage = {}
        for name, store in self.storage.items():
            totals = {key: total.value() for key, total in store['totals'].items()}
            terms = [store['initial']]+list(totals.values())+[store['final']]
            residual = terms[0]+terms[1]-math.fsum(terms[2:]) if all(_number(v) for v in terms) else None
            storage[name] = dict(initial_stock_kWh=store['initial'], final_stock_kWh=store['final'],
                minimum_stock_kWh=store['minimum'] if store['boundaries_complete'] and store['count'] else None,
                minimum_scope='recorded actual segment boundaries only', intrasegment_minimum_stock_kWh=None,
                **totals, energy_residual_kWh=residual,
                receipt_chain_max_gap_kWh=(store['max_gap'] if store['chain_complete'] and store['boundaries_complete'] and store['count'] else None),
                segment_energy_max_absolute_residual_kWh=(store['max_segment_residual'] if store['segment_residual_complete'] and store['count'] else None),
                source='actual m21_storage receipts; initial + charge - useful discharge - standing loss - conversion loss - final')
        return dict(carrier_service=service, storage=storage, segment_count=self.segment_count,
            solver_call_count=self.solver_call_count, event_count=self.event_count,
            terminal=copy.deepcopy(self.terminal), native_material_classification=None,
            native_material_classification_reason='Not recomputed: tools.model_review_v30.evaluation._services requires whole-prefix native interruption/recovery state; no substitute threshold is introduced.',
            unobserved_tail_service=None)


def _scan(output, *, expected_identity_sha256=None, committed_limit=None):
    """Hold at most one decoded raw block; metadata grows only with block count."""
    output = Path(output)
    identity_sha = _sha(output/'IDENTITY.json')
    if expected_identity_sha256 is not None and identity_sha != expected_identity_sha256:
        raise ValueError('Journal identity SHA mismatch')
    identity = json.loads((output/'IDENTITY.json').read_text(encoding='utf8'))
    if not isinstance(identity, dict):
        raise ValueError('Journal identity must remain an object')
    accumulator = _Accumulator()
    previous_digest, end, count = None, 0., 0
    raw_files = []
    extra_index_records = 0
    with (output/'blocks.jsonl').open('r', encoding='utf8') as index:
        for line in index:
            if committed_limit is not None and count >= committed_limit:
                extra_index_records += 1
                continue
            if not line.endswith('\n'):
                raise ValueError('Incomplete journal index record')
            record = json.loads(line)
            expected_file = 'blocks/block_'+format(count, '06d')+'.json.gz'
            if (not isinstance(record, dict) or record.get('schema') != SCHEMA or
                    record.get('index') != count or record.get('file') != expected_file or
                    record.get('previous_digest') != previous_digest or
                    record.get('identity_sha256') != identity_sha or
                    record.get('digest') != _record_digest(record)):
                raise ValueError('Journal index hash chain or identity mismatch')
            path = output/expected_file
            if _sha(path) != record.get('sha256'):
                raise ValueError('Raw block SHA mismatch: '+expected_file)
            with gzip.open(path, 'rt', encoding='utf8') as stream:
                block = json.load(stream)
            _json(block)  # JSON readers otherwise accept NaN/Infinity.
            end = _validate_block(block, end)
            expected = dict(start_minute=block['start_minute'], end_minute=block['end_minute'],
                segment_count=len(block['segments']), solver_call_count=len(block['solver_calls']))
            if any(record.get(key) != value for key, value in expected.items()):
                raise ValueError('Journal index clock or count mismatch')
            accumulator.add(block)
            raw_files.append(dict(file=expected_file, sha256=record['sha256']))
            count += 1
            previous_digest = record['digest']
            del block  # Release this raw block before decoding the next one.
    if committed_limit is not None and count != committed_limit:
        raise ValueError('Committed journal prefix is missing index records')
    files = {str(p.relative_to(output)).replace('\\', '/') for p in (output/'blocks').iterdir()}
    uncommitted = sorted(files-{record['file'] for record in raw_files})
    return dict(identity=identity, identity_sha256=identity_sha, block_count=count,
        observed_until_minute=end, last_digest=previous_digest, raw_files_sha256=raw_files,
        uncommitted_files=uncommitted, uncommitted_index_record_count=extra_index_records,
        **accumulator.export())


def verify_journal(output):
    """Verify all raw SHA values, block clocks, chain links and summary binding.

    A journal with uncommitted files/records is rejected. This is integrity
    verification, not a physical or clinical qualification.
    """
    summary_path = Path(output)/'SUMMARY.json'
    if not summary_path.is_file():
        raise ValueError('Journal is not durably closed; index bytes alone do not prove committed acceptance')
    result = _scan(output)
    if result['uncommitted_files']:
        raise ValueError('Uncommitted raw journal files remain')
    summary = json.loads(summary_path.read_text(encoding='utf8'))
    for key in ('identity', 'identity_sha256', 'block_count', 'last_digest', 'raw_files_sha256',
                'segment_count', 'solver_call_count', 'event_count', 'carrier_service', 'storage', 'terminal',
                'native_material_classification', 'native_material_classification_reason', 'unobserved_tail_service'):
        if summary.get(key) != result[key]:
            raise ValueError('Journal SUMMARY binding mismatch: '+key)
    observation = summary.get('observation') or {}
    observed = result['observed_until_minute']
    requested = observation.get('requested_end_minute')
    expected_complete = bool(summary.get('status') == 'completed' and observed == requested and result['block_count'])
    if (not _number(requested) or requested < observed or observation != dict(
            observed_from_minute=0., observed_until_minute=observed, observed_duration_minutes=observed,
            requested_end_minute=requested, unknown_tail_minutes=requested-observed,
            complete=expected_complete, contiguous=True, quantities_are_observed_prefix_only=True)):
        raise ValueError('Journal SUMMARY observation coverage mismatch')
    if summary.get('journal_write_error') or summary.get('integrity_verified') is not True:
        raise ValueError('Journal records a write failure or unverified evidence')
    result['integrity_verified'] = True
    return result


class BlockJournal:
    """Exclusive writer. An append is acknowledged only after raw/index fsync.

    ``append`` preserves the complete producer object; it does not trim live
    core history. The runner may release history only after the returned
    receipt. A write failure permanently rejects further appends and successful
    close; a failed close records the last acknowledged prefix and unknown tail.
    """
    def __init__(self, output, *, identity):
        if not isinstance(identity, dict):
            raise ValueError('Journal identity must be an object')
        encoded_identity = _json(identity)
        self.output = Path(output)
        self.output.mkdir(parents=True, exist_ok=False)
        (self.output/'blocks').mkdir()
        _write_exclusive(self.output/'IDENTITY.json', encoded_identity)
        _write_exclusive(self.output/'blocks.jsonl', b'')
        self._identity_sha = hashlib.sha256(encoded_identity).hexdigest()
        self._identity = json.loads(encoded_identity)
        self._count, self._end, self._last_digest = 0, 0., None
        self._closed = False
        self._write_error = None
        self._close_attempt_count = 0

    def _check_open(self):
        if self._closed:
            raise RuntimeError('Journal is already closed')
        if _sha(self.output/'IDENTITY.json') != self._identity_sha:
            raise ValueError('Journal identity changed')

    def append(self, block):
        self._check_open()
        if self._write_error is not None:
            raise RuntimeError('Journal has a prior write failure')
        end = _validate_block(block, self._end)
        encoded = _json(block)
        filename = 'blocks/block_'+format(self._count, '06d')+'.json.gz'
        path = self.output/filename
        try:
            _write_exclusive(path, gzip.compress(encoded, mtime=0))
            actual_sha = _sha(path)
            # Validate the actual persisted data before acknowledging the block.
            with gzip.open(path, 'rt', encoding='utf8') as stream:
                persisted = json.load(stream)
            if _json(persisted) != encoded:
                raise ValueError('Persisted journal block differs from supplied data')
            _validate_block(persisted, self._end)
            record = dict(schema=SCHEMA, index=self._count, file=filename, sha256=actual_sha,
                identity_sha256=self._identity_sha, previous_digest=self._last_digest,
                start_minute=block['start_minute'], end_minute=end,
                segment_count=len(block['segments']), solver_call_count=len(block['solver_calls']))
            record['digest'] = _record_digest(record)
            data = _json(record)
            with (self.output/'blocks.jsonl').open('ab') as index:
                if index.write(data) != len(data):
                    raise OSError('Incomplete journal index write')
                index.flush()
                os.fsync(index.fileno())
        except Exception as exc:
            self._write_error = dict(type=type(exc).__name__, message=str(exc), attempted_block_index=self._count,
                attempted_start_minute=block['start_minute'], attempted_end_minute=end)
            raise
        self._count += 1
        self._end, self._last_digest = end, record['digest']
        return copy.deepcopy(record)

    def close(self, *, status, expected_end_minute, error=None):
        self._check_open()
        if not isinstance(status, str) or not status:
            raise ValueError('An explicit execution status is required')
        if not _number(expected_end_minute) or expected_end_minute < self._end:
            raise ValueError('Expected end must be finite and no earlier than observed evidence')
        if self._write_error is not None and status == 'completed':
            raise RuntimeError('Journal write failure cannot have a completed close')
        scan = _scan(self.output, expected_identity_sha256=self._identity_sha, committed_limit=self._count)
        if scan['last_digest'] != self._last_digest or scan['observed_until_minute'] != self._end:
            raise ValueError('Journal no longer matches its acknowledged append receipt digest or clock')
        integrity = not (self._write_error or scan['uncommitted_files'] or scan['uncommitted_index_record_count'])
        if status == 'completed' and not integrity:
            raise RuntimeError('Uncommitted journal evidence cannot have a completed close')
        observed = scan.pop('observed_until_minute')
        summary = dict(schema=SCHEMA, **scan, status=status, error=copy.deepcopy(error),
            journal_write_error=copy.deepcopy(self._write_error), integrity_verified=integrity,
            observation=dict(observed_from_minute=0., observed_until_minute=observed,
                observed_duration_minutes=observed, requested_end_minute=expected_end_minute,
                unknown_tail_minutes=expected_end_minute-observed,
                complete=bool(status == 'completed' and integrity and observed == expected_end_minute and self._count),
                contiguous=True, quantities_are_observed_prefix_only=True),
            real_hospital_or_clinical_qualification=False)
        # Publish the final name only after the complete content has fsynced.
        # Same-directory hard linking is exclusive (never replaces a summary).
        # Keep each staging file as immutable evidence, including failed attempts.
        staging = self.output/('SUMMARY.prepare_'+format(self._close_attempt_count, '06d')+'.json')
        self._close_attempt_count += 1
        try:
            data = _json(summary)
            _write_exclusive(staging, data)
            if _sha(staging) != hashlib.sha256(data).hexdigest():
                raise ValueError('Persisted journal summary SHA mismatch')
            os.link(staging, self.output/'SUMMARY.json')
        except Exception as exc:
            self._write_error = dict(type=type(exc).__name__, message=str(exc), phase='summary_close',
                attempted_status=status, attempted_expected_end_minute=expected_end_minute)
            raise
        self._closed = True
        return summary
