"""Offline native segment temperature/violation export, one gzip open per raw.

No simulator, scoring change, interpolation, clinical threshold or replacement
of a missing P3 field. Production execution requires an explicit all-stopped
flag and terminal batch receipts; this module is outside the runner binding.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import gzip
import hashlib
import itertools
import json
import math
from pathlib import Path

SCHEMA = 'hospital_ies.native_thermal_prefix.v14.v1'
EPS = 1e-8


class _HashingReader:
    def __init__(self, stream):
        self.stream = stream; self.digest = hashlib.sha256()

    def read(self, size=-1):
        block = self.stream.read(size); self.digest.update(block); return block


def _read_gzip_once(path, expected):
    with path.open('rb') as source:
        reader = _HashingReader(source)
        with gzip.GzipFile(filename='', fileobj=reader, mode='rb') as decoded:
            payload = json.load(decoded)
        # A normal complete gzip read reaches EOF. Drain any remaining bytes
        # through the same open stream so the receipt hashes compressed bytes.
        while reader.read(1024*1024):
            pass
        actual = reader.digest.hexdigest()
    if expected and expected != actual:
        raise ValueError('Compressed raw SHA differs from its saved result')
    return payload, actual


def _clock(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0.:
        raise ValueError('Invalid native thermal clock')
    return float(value)


def extract(payload, requested_end_minute):
    """Return native P0-P3 rows from one already-open payload, or null rows."""
    requested = _clock(requested_end_minute)
    observed = None if payload is None else _clock(payload['summary']['observed_until_minute'])
    segments = [] if payload is None else payload['segments']
    previous = 0.
    for segment in segments:
        a, b = map(_clock, (segment['start_minute'], segment['end_minute']))
        if abs(a-previous) > EPS or b <= a:
            raise ValueError('Native thermal segments must be contiguous from zero')
        previous = b
    if payload is not None:
        if (abs(previous-observed) > EPS or observed > requested+EPS or
                abs(_clock(payload['summary']['requested_end_minute'])-requested) > EPS):
            raise ValueError('Native thermal prefix/horizon differs from registered clocks')
    windows = [('primary', 60., 420.), ('full', 0., 600.)]
    if requested >= 1440.-EPS:
        windows += [('long1440', 0., 1440.), ('extension_600_1440', 600., 1440.)]
    rows = []
    for name, start, end in windows:
        parts = [(max(start, s['start_minute']), min(end, s['end_minute']), s) for s in segments
                 if s['end_minute'] > start and s['start_minute'] < end]
        duration = sum(b-a for a, b, _ in parts)
        complete = observed is not None and observed >= end-EPS and abs(duration-(end-start)) <= EPS
        for priority in ('P0', 'P1', 'P2', 'P3'):
            temperatures = []; temperature_minutes = 0.; flag_minutes = 0.; violation_minutes = 0.
            for a, b, segment in parts:
                temperature = (segment.get('temperature') or {}).get(priority)
                if temperature is not None:
                    if isinstance(temperature, bool) or not isinstance(temperature, (int, float)) or not math.isfinite(temperature):
                        raise ValueError('Invalid native segment temperature')
                    temperatures.append(float(temperature)); temperature_minutes += b-a
                violation = (segment.get('violation') or {}).get(priority)
                if violation is not None:
                    if type(violation) is not bool:
                        raise ValueError('Native violation flag must be bool or absent')
                    flag_minutes += b-a
                    if violation: violation_minutes += b-a
            maximum = max(temperatures) if temperatures else None
            flagged = violation_minutes if flag_minutes > 0. else None
            rows.append(dict(window=name, window_start_minute=start, window_end_minute=end, priority=priority,
                observed_until_minute=observed, requested_end_minute=requested,
                complete_window=complete, observed_window_minutes=duration if observed is not None else None,
                observed_prefix_max_temperature_C=maximum, observed_prefix_violation_minutes=flagged,
                temperature_observed_minutes=temperature_minutes if observed is not None else None,
                temperature_missing_minutes=duration-temperature_minutes if observed is not None else None,
                violation_flag_observed_minutes=flag_minutes if observed is not None else None,
                violation_flag_missing_minutes=duration-flag_minutes if observed is not None else None,
                full_window_max_temperature_C=maximum if complete and abs(temperature_minutes-duration) <= EPS else None,
                full_window_violation_minutes=flagged if complete and abs(flag_minutes-duration) <= EPS else None,
                source='native segments.temperature/violation; classified segment durations; no interpolation'))
    return rows


def _json(path):
    return json.loads(path.read_text(encoding='utf8'))


def _under(path, root):
    path = Path(path)
    if not path.is_absolute(): path = root/path
    value = path.resolve()
    if not value.is_relative_to(root.resolve()): raise ValueError('Registered raw escapes the study run')
    return value


def export(run, out=None, *, all_stopped=False, expected_pairs=None):
    """Export every registered pair after root's explicit final stopped gate."""
    if not all_stopped:
        raise ValueError('Wait for root all-stopped instruction before opening registered raws')
    run = Path(run).resolve(); registrations = []
    for plan_path in sorted((run/'experiments').glob('*/plan.json')):
        folder = plan_path.parent
        if not (folder/'BATCH_RESULT.json').is_file():
            raise ValueError('Batch is not stopped/terminal: '+folder.name)
        plan = _json(plan_path); manifest = _json(folder/'manifest.json')
        scenes = {s['id']: s for s in manifest['scenarios']}
        expected = {(sid, p['policy_id']) for sid in plan['scenario_ids'] for p in plan['policy_specs']}
        if len(expected) != plan['registered_pairs']: raise ValueError('Registered thermal denominator differs')
        values = _json(folder/'analysis/RESULTS.json')['records'] if (folder/'analysis/RESULTS.json').is_file() else []
        records = {(r['scenario_id'], r['policy_id']): r for r in values}
        if len(records) != len(values) or not set(records) <= expected: raise ValueError('Saved thermal result identity differs')
        for sid, pid in sorted(expected):
            scene = scenes[sid]
            if scene.get('is_final_blind'): raise ValueError('Original final-blind raw is excluded')
            registrations.append((dict(batch=folder.name, phase=plan['phase'], model_id=plan['model_id'],
                model_config_signature=hashlib.sha256(json.dumps(plan.get('model_config', {}),sort_keys=True,separators=(',',':')).encode()).hexdigest(),
                scenario_id=sid, policy_id=pid, analysis_class=scene.get('analysis_class'),
                source_day_index=scene.get('source_day_index')), scene, records.get((sid,pid))))
    if not registrations or expected_pairs is not None and len(registrations) != expected_pairs:
        raise ValueError('Actual registered thermal pair count differs or is empty')
    if out is None:
        base = run/'thermal_prefix'; base.mkdir(parents=True, exist_ok=True)
        for index in itertools.count(1):
            destination = base/f'export_{index:04d}'
            try: destination.mkdir(); break
            except FileExistsError: continue
    else:
        destination = Path(out).resolve()
        if destination.exists(): raise FileExistsError('Use a fresh thermal export directory')
        if destination.drive.lower() == 'c:': raise ValueError('Use D/E for study exports')
        destination.mkdir(parents=True)
    rows = []; errors = []; inputs = []; opened = set(); complete = 0
    for identity, scene, record in registrations:
        requested = int(scene['duration_steps'])*float(scene.get('step_minutes',15.))
        status = 'extracted'; payload = None; actual_sha = None; path = None
        try:
            if not record or not record.get('raw_path'): raise FileNotFoundError('No registered raw path in saved result')
            path = _under(record['raw_path'], run)
            if path in opened: raise ValueError('Raw path is claimed by more than one registered pair')
            opened.add(path)
            payload, actual_sha = _read_gzip_once(path, record.get('raw_sha256'))
            binding = payload.get('identity') or {}
            if binding.get('scenario_id') != identity['scenario_id'] or binding.get('policy_id') != identity['policy_id']:
                raise ValueError('Native thermal payload identity differs from registration')
            values = extract(payload, requested)
        except Exception as error:
            status = 'extraction_unknown'; values = extract(None, requested)
            errors.append(dict(identity, error_type=type(error).__name__, message=str(error)))
        complete += any(v['window']=='full' and v['complete_window'] for v in values)
        rows.extend(dict(identity, extraction_status=status, **value) for value in values)
        inputs.append(dict(identity, raw_path=str(path) if path else None,
            saved_raw_sha256=record.get('raw_sha256') if record else None, actual_raw_sha256=actual_sha,
            extraction_status=status))
        del payload
    csv_path = destination/'THERMAL_PREFIX.csv'
    with csv_path.open('x', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
    receipt = dict(schema=SCHEMA, created_utc=datetime.now(timezone.utc).isoformat(), output_directory=str(destination),
        registered_pairs=len(registrations), raw_files_opened=len(opened), complete_full_pairs=complete,
        unknown_full_pairs=len(registrations)-complete, csv_rows=len(rows), extraction_errors=errors,
        inputs=inputs, physical_episodes_started=0, each_compressed_raw_opened_at_most_once=True,
        raw_sha_verified_during_same_decompression_open=True,
        csv_sha256=hashlib.sha256(csv_path.read_bytes()).hexdigest(),
        program_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        semantics='Native segment temperature snapshots and original violation-flag duration; low-order proxies, not clinical outcomes. No threshold interpolation. Missing temperature/flags remain null. Unknown windows retain only observed prefix; full-window values are null. Explicit native false flags are preserved even when temperature is unavailable.',
        final12_gate='not_verified; unopened')
    with (destination/'THERMAL_PREFIX_RECEIPT.json').open('x',encoding='utf8') as stream:
        json.dump(receipt, stream, ensure_ascii=False, indent=2, allow_nan=False)
    return receipt


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,required=True);parser.add_argument('--output',type=Path)
    parser.add_argument('--all-stopped',action='store_true');parser.add_argument('--expected-pairs',type=int)
    args=parser.parse_args(argv)
    receipt=export(args.run,args.output,all_stopped=args.all_stopped,expected_pairs=args.expected_pairs)
    print(json.dumps({k:receipt[k] for k in ('output_directory','registered_pairs','raw_files_opened','complete_full_pairs','unknown_full_pairs','csv_rows')},ensure_ascii=False))
    return 0


if __name__=='__main__':raise SystemExit(main())
