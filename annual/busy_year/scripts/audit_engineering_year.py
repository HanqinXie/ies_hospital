"""Independent annual/short engineering-ledger audit; no optimizer execution.

Reads one native journal block at a time, reuses the unchanged cooling-dynamics
arithmetic auditor, and preserves full source/producer byte inventories. This
owner is explicitly distinct from legacy compiled-flow/native-primal evidence.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import gzip
import hashlib
import importlib
import importlib.util
import inspect
import json
import math
import os
from pathlib import Path
import re
import sys
import time
from engineering_receipts import (validated_numerical_profile, validate_actual_owner, validate_attempt,
    registered_exact_planner_call,validate_exact_planner_attempt)

sys.dont_write_bytecode = True
MODEL_ID = 'annual_responsive_dynamic_bridge_20261104.v1'
OWNER_SCHEMA = 'hospital_ies.annual_dynamic.accepted_engineering_owner.v1'
JOURNAL_SCHEMA = 'hospital_ies.annual_readiness.block_journal.v1'
CAUSE_SCHEMA = 'hospital_ies.annual_dynamic.current_cause.v1'
REQUIRED_PINS = ('CASE.json', 'BINDINGS.json', 'RESOLVED_CASE.json',
                 'evidence/IDENTITY.json', 'evidence/SUMMARY.json', 'evidence/blocks.jsonl')
YEAR_MINUTES = 525600.
CLOCK_TOL = 1e-8  # Unchanged audit_campaign.CLOCK_TOL, not a new physical tolerance.


class AuditUnknown(ValueError):
    """A required observation is absent; its numeric value is not inferred."""


def encoded(value, *, newline=False):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                       allow_nan=False) + ('\n' if newline else '')).encode('utf8')


def digest(value, *, newline=False):
    return hashlib.sha256(encoded(value, newline=newline)).hexdigest()


def sha(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def read(path):
    def reject(value):
        raise AuditUnknown('Nonfinite JSON constant: ' + value)
    return json.loads(Path(path).read_text(encoding='utf-8-sig'), parse_constant=reject)


def write(path, value):
    with Path(path).open('xb') as stream:
        stream.write(encoded(value, newline=True))
        stream.flush(); os.fsync(stream.fileno())


def number(value, name):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise AuditUnknown('Missing/nonfinite ' + name)
    return float(value)


def require(value, *keys):
    for key in keys:
        if not isinstance(value, dict) or key not in value:
            raise AuditUnknown('Missing required field: ' + '.'.join(keys))
        value = value[key]
    return value


def safe_path(root, relative):
    if (not isinstance(relative, str) or not relative or '\\' in relative or ':' in relative
            or any(part in ('', '.', '..') for part in relative.split('/'))):
        raise ValueError('Ordinary relative evidence path required')
    root = Path(root).resolve(); path = root / relative
    if not path.resolve().is_relative_to(root) or path.is_symlink():
        raise ValueError('Evidence path escapes its bound root')
    return path


def inventory(root, destination):
    """Stream every file and directory, including .git and unbound extra files."""
    root = Path(root).resolve(); chain = hashlib.sha256(); files = directories = total = 0
    def fail(error):
        raise error
    with Path(destination).open('xb') as output:
        for current, dirs, names in os.walk(root, topdown=True, onerror=fail, followlinks=False):
            dirs.sort(); names.sort()
            for name, kind in [(name, 'directory') for name in dirs] + [(name, 'file') for name in names]:
                path = Path(current) / name
                if path.is_symlink() or not path.resolve().is_relative_to(root):
                    raise ValueError('Symlink or aliased external member is not an immutable input: ' + str(path))
                row = dict(path=path.relative_to(root).as_posix(), kind=kind)
                if kind == 'file':
                    size = path.stat().st_size
                    row.update(bytes=size, sha256=sha(path)); files += 1; total += size
                else:
                    directories += 1
                raw = encoded(row, newline=True); output.write(raw); chain.update(raw)
        output.flush(); os.fsync(output.fileno())
    return dict(root=str(root), file_count=files, directory_count=directories, total_file_bytes=total,
        inventory_sha256=chain.hexdigest(), coverage='all_files_and_directories_no_exclusions',
        includes_git_and_files_outside_scientific_binding=True)


def _git_head(source):
    git = source / '.git'
    if not git.is_dir() or git.is_symlink() or (git / 'commondir').exists():
        raise ValueError('Independent source Git directory required for source binding')
    value = (git / 'HEAD').read_text(encoding='ascii').strip()
    if value.startswith('ref: '):
        reference = value[5:]
        path = safe_path(git, reference)
        if path.is_file():
            value = path.read_text(encoding='ascii').strip()
        else:
            packed = git / 'packed-refs'; found = []
            if packed.is_file():
                found = [line.split()[0] for line in packed.read_text(encoding='ascii').splitlines()
                         if line and not line.startswith(('#', '^')) and line.split()[-1] == reference]
            if len(found) != 1:
                raise ValueError('Git source binding reference is unresolved')
            value = found[0]
    if re.fullmatch('[0-9a-f]{40}', value) is None:
        raise ValueError('Exact source Git commit binding required')
    return value


def _verify_bindings(source, bindings):
    if _git_head(source) != bindings.get('git_commit'):
        raise ValueError('Source Git commit binding differs')
    for field in ('source', 'inputs'):
        mapping = bindings.get(field)
        if not isinstance(mapping, dict) or not mapping:
            raise AuditUnknown('Missing source/input binding map: ' + field)
        for relative, expected in mapping.items():
            path = safe_path(source, relative)
            if (not isinstance(expected, str) or re.fullmatch('[0-9a-f]{64}', expected) is None
                    or not path.is_file() or sha(path) != expected):
                raise ValueError('Source/input binding differs: ' + relative)


def _validate_resolved(resolved, case):
    if (resolved.get('annual_dynamic_owner_contract') != OWNER_SCHEMA
            or resolved.get('annual_dynamic_model_id') != MODEL_ID or resolved.get('case') != case):
        raise ValueError('RESOLVED new engineering identity or exact CASE binding differs')
    fields = {'plant': ('steam_kWh_per_lb', 'exchanger_eff', 'abs_electric_kW_per_rt',
                       'ec_electric_kW_per_rt', 'abs_steam_lb_per_rt'),
        'physical_constants': ('RT_TO_KW',), 'sci_config': ('cold_pump_kW_per_kW',),
        'reset_physical_context': ('recovery_pump_kW_per_kW', 'recovery_fan_kW_per_kW'),
        'station_config': ('boiler_fixed_kW', 'boiler_kW_per_kWsteam'),
        'cooling_config': ('tower_support_tau_seconds', 'pump_support_tau_seconds')}
    for section, names in fields.items():
        for name in names:
            number(require(resolved, section, name), section + '.' + name)
    if not isinstance(require(resolved, 'operating_execution_contract', 'diesel_mode'), str):
        raise AuditUnknown('Missing operating_execution_contract.diesel_mode')
    taus = require(resolved, 'cooling_config', 'cooling_tau_seconds')
    if not isinstance(taus, list) or len(taus) != 4:
        raise AuditUnknown('Missing four cooling_config.cooling_tau_seconds')
    for value in taus:
        number(value, 'cooling tau')
    for store in ('ups', 'cold_store'):
        if require(resolved, 'storage_state_receipts', store, 'integration_contract') != 'single_fault.continuous_loss_held_flow.v1':
            raise ValueError('Missing accepted SF1 storage integration contract')
        for field in ('charge_efficiency', 'discharge_efficiency', 'loss_per_hour', 'energy_kWh', 'capacity_kWh', 'power_kW'):
            number(require(resolved, 'storage_state_receipts', store, field), store + '.' + field)


def _resolve_audit_context(source,bindings,resolved):
    """Expand recorded raw M22 parameters through the bound pure source API."""
    relative='tools/model_closure_v22/physical.py';path=safe_path(source,relative)
    expected=require(bindings,'source',relative)
    if (not isinstance(expected,str) or re.fullmatch('[0-9a-f]{64}',expected) is None
            or not path.is_file() or sha(path)!=expected):
        raise ValueError('Recovery context default source differs from its bound source binding')
    raw=require(resolved,'reset_physical_context')
    if not isinstance(raw,dict):raise AuditUnknown('Missing raw reset physical context dictionary')
    spec=importlib.util.spec_from_file_location('annual_audit_m22_current_context_'+expected,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    resolve=getattr(module,'current_context',None)
    if not callable(resolve):raise AuditUnknown('Missing bound pure current_context parameter resolver')
    canonical=resolve({'_model_closure_v22':deepcopy(raw)})
    if not isinstance(canonical,dict):raise AuditUnknown('Missing canonical current physical context')
    copied=deepcopy(resolved);copied['reset_physical_context']=canonical
    omitted=sorted(set(canonical)-set(raw))
    evidence=dict(schema='hospital_ies.annual_dynamic.audit_parameter_resolution.v1',
        source_relative_path=relative,source_path=str(path),source_sha256=expected,symbol='current_context',
        call_contract="current_context({'_model_closure_v22': recorded_raw_reset_context})",
        recorded_field='RESOLVED_CASE.json.reset_physical_context',raw_context_sha256=digest(raw),
        resolved_context_sha256=digest(canonical),originally_omitted_fields=omitted,
        source_default_values={key:canonical[key] for key in omitted},
        recovery_coefficients={key:canonical[key] for key in ('recovery_pump_kW_per_kW','recovery_fan_kW_per_kW')},
        scope='Audit-only canonical reset context for zero actual recovery; not an active per-segment recovery context',
        producer_modified=False,parameters_back_calculated=False,
        active_recovery_without_bound_per_segment_context='unknown_not_reset_default')
    return copied,evidence


def _segment_audit_context(segment,resolved):
    recovery=number(require(segment,'action','recovery_cooling_kW'),'actual recovery cooling kW')
    if recovery<0.:raise ValueError('Negative actual recovery cooling is outside its original domain')
    if recovery>0.:
        # m23_restoration currently records boiler restart state, not the
        # thermal prepared/context receipt. Never replace a possible active
        # M23 fan=.1 override by the reset DEFAULT_CURRENT fan=0 value.
        raise AuditUnknown('Missing bound per-segment prepared/current-context evidence for active recovery; reset defaults are insufficient')
    return resolved


def _header(source, producer):
    result = read(producer / 'RESULT.json'); case_document = read(producer / 'CASE.json')
    case = case_document.get('case', case_document)
    if not isinstance(case, dict):
        raise ValueError('Native CASE wrapper must contain its exact canonical case')
    bindings = read(producer / 'BINDINGS.json'); resolved = read(producer / 'RESOLVED_CASE.json')
    if (result.get('status') != 'completed' or result.get('error') is not None
            or type(result.get('reset_count')) is not int or result.get('reset_count') != 1
            or result.get('source_and_inputs_unchanged') is not True
            or result.get('execution_model_id') != MODEL_ID or result.get('annual_dynamic_owner_contract') != OWNER_SCHEMA):
        raise ValueError('Only a completed new-owner unchanged-source producer with one reset may be audited')
    end = number(result.get('requested_end_minute'), 'RESULT.requested_end_minute')
    if not 0. < end <= YEAR_MINUTES or end % 15.:
        raise ValueError('An explicit complete short/full-year 15-minute request is required')
    if result.get('observed_until_minute') != end or result.get('unknown_tail_minutes') != 0.:
        raise ValueError('Producer requested coverage is incomplete or has an unknown tail')
    pins = result.get('files')
    if not isinstance(pins, dict):
        raise AuditUnknown('Missing RESULT.files engineering evidence pins')
    for name in REQUIRED_PINS:
        path = safe_path(producer, name); pin = require(pins, name)
        if (not isinstance(pin, dict) or pin.get('bytes') != path.stat().st_size
                or pin.get('sha256') != sha(path)):
            raise ValueError('Producer metadata file pin differs: ' + name)
    revision = require(case, 'execution_revision')
    expected = dict(model_id=MODEL_ID, owner_contract=OWNER_SCHEMA, load_cadence='hourly_original_held',
                    dispatch_minutes=15, physical_max_segment_minutes=1)
    if any(revision.get(key) != value for key, value in expected.items()):
        raise ValueError('CASE annual dynamic cadence/owner registration differs')
    events = require(revision, 'declared_calendar')
    if not isinstance(events, list):
        raise AuditUnknown('Original declared cause calendar is required')
    previous = -1.
    for event in events:
        minute = number(require(event, 'time_minute'), 'declared event minute')
        values = require(event, 'availability')
        if minute <= previous or minute < 0. or not isinstance(values, dict) or not values:
            raise ValueError('Declared cause calendar is malformed')
        for key, value in values.items():
            if not isinstance(key, str) or not 0. <= number(value, 'declared availability') <= 1.:
                raise ValueError('Declared cause availability is malformed')
        previous = minute
    if (require(case, 'scenario', 'step_minutes') != 15.
            or number(require(case, 'scenario', 'duration_steps'), 'duration steps') * 15. != end):
        raise ValueError('CASE duration differs from RESULT requested coverage')
    _verify_bindings(source, bindings)
    resolved,parameter_resolution=_resolve_audit_context(source,bindings,resolved)
    _validate_resolved(resolved, case)
    resolved['_audit_parameter_resolution']=parameter_resolution
    validated_numerical_profile(case, result, resolved)
    identity = read(producer / 'evidence/IDENTITY.json'); summary = read(producer / 'evidence/SUMMARY.json')
    expected_identity = dict(case_id=case.get('id'), case_sha256=digest(case), git_commit=bindings['git_commit'],
        source_sha256=digest(bindings['source']), input_sha256=digest(bindings['inputs']))
    if any(identity.get(key) != value for key, value in expected_identity.items()):
        raise ValueError('Journal identity source/input/CASE binding differs')
    identity_sha = sha(producer / 'evidence/IDENTITY.json')
    observation = dict(observed_from_minute=0., observed_until_minute=end, observed_duration_minutes=end,
        requested_end_minute=end, unknown_tail_minutes=0., complete=True, contiguous=True,
        quantities_are_observed_prefix_only=True)
    if (summary.get('status') != 'completed' or summary.get('integrity_verified') is not True
            or summary.get('journal_write_error') or summary.get('identity') != identity
            or summary.get('identity_sha256') != identity_sha or summary.get('observation') != observation):
        raise ValueError('Closed journal SUMMARY or complete observation binding differs')
    return result, case, bindings, resolved, identity_sha, summary, end


def _owner_and_cause(row, events, *, numerical_profile=None):
    validate_actual_owner(row, numerical_profile)
    if 'annual_program_actual_witness' in row:
        raise ValueError('Legacy native witness cannot qualify the new engineering ledger')
    start = number(row.get('start_minute'), 'segment start')
    end = number(row.get('end_minute'), 'segment end')
    if (number(require(row, 'cooling_dynamics', 'initial_state', 'elapsed_minutes'), 'dynamic initial clock') != start
            or number(require(row, 'cooling_dynamics', 'final_state', 'elapsed_minutes'), 'dynamic final clock') != end):
        raise ValueError('Dynamic absolute clock differs from the actual segment clock')
    cause = require(row, 'annual_dynamic_causal_ledger')
    if (cause.get('schema') != CAUSE_SCHEMA or cause.get('observation_time_minute') != start
            or cause.get('available_time_minute') != start or cause.get('raw_compressor_damage_inferred') is not False
            or cause.get('hydraulic_measurement_claimed') is not False
            or cause.get('branch_lowering') != 'effective_EC1=raw_EC1*raw_EC1_PRIMARY_PUMP'):
        raise ValueError('Current cause ledger clock, semantics, or no-inference declaration differs')
    raw = require(cause, 'raw_component_availability')
    functional = require(cause, 'effective_functional_availability')
    hardware = require(row, 'hardware_availability')
    if not all(isinstance(value, dict) and value for value in (raw, functional, hardware)):
        raise AuditUnknown('Missing current raw/functional hardware availability maps')
    for mapping in (raw, functional, hardware):
        for value in mapping.values():
            if not 0. <= number(value, 'availability') <= 1.:
                raise ValueError('Current availability is outside its original domain')
    if any(hardware.get(key) != value for key, value in functional.items()):
        raise ValueError('Causal functional availability differs from actual segment hardware')
    declared = {}
    for event in events:
        if event['time_minute'] > start:
            break
        declared.update(event['availability'])
    if cause.get('active_declared_causes') != sorted(key for key, value in declared.items() if value < 1.):
        raise ValueError('Cause ledger active set differs from only the observed calendar prefix')
    if any(raw.get(key) != value for key, value in declared.items()):
        raise ValueError('Raw component cause differs from the declared current cause')
    if any('EC_1_PRIMARY_PUMP' in event['availability'] for event in events):
        compressor = declared.get('EC_1', 1.); pump = declared.get('EC_1_PRIMARY_PUMP', 1.)
        if (raw.get('EC_1') != compressor or raw.get('EC_1_PRIMARY_PUMP') != pump
                or functional.get('EC_1') != compressor * pump):
            raise ValueError('EC1 functional interlock differs from independent raw component causes')


def _load_audit_row(source, numerical_profile=None):
    expected = source / 'tools/cooling_dynamics_20261104/audit_campaign.py'
    sys.path[:0] = [str(source), str(source / 'src')]
    module = importlib.import_module('tools.cooling_dynamics_20261104.audit_campaign')
    if Path(module.__file__).resolve() != expected.resolve():
        raise ValueError('Native engineering audit dependency loaded from a different source')
    inspect.signature(module.audit_row).bind({}, {}, previous_stocks=None, previous_dynamic=None, residuals={})
    dependency=dict(mode='unchanged_source_dependency', path=str(expected), sha256=sha(expected))
    if numerical_profile is not None:
        adapter_path=Path(__file__).resolve().parent/'engineering_audit_rows.py'
        spec=__import__('importlib.util',fromlist=['util']).spec_from_file_location(
            'annual_engineering_numerical_audit_adapter_'+sha(adapter_path),adapter_path)
        adapter=__import__('importlib.util',fromlist=['util']).module_from_spec(spec);spec.loader.exec_module(adapter)
        dependency.update(mode='profile_aware_source_formula_adapter',numerical_profile=numerical_profile['profile'],
            adapter_path=str(adapter_path),adapter_sha256=sha(adapter_path),source_formula_sha256=adapter.SOURCE_FORMULA_SHA256,
            instantaneous_rate_tolerance=numerical_profile['tolerances']['physical_rows_bounds'],
            balance_rate_tolerance=module.RATE_TOL,inventory_tolerance_kWh=module.STOCK_TOL,clock_tolerance_minutes=module.CLOCK_TOL)
        policy_path=Path(__file__).resolve().parent/'engineering_receipts.py'
        dependency.update(policy_helper_path=str(policy_path),policy_helper_sha256=sha(policy_path))
        return adapter.make_auditor(module,numerical_profile),dependency
    return module.audit_row,dependency


def _engineering_solver_receipts(value,profile,counts,exact_planner=False):
    if isinstance(value,list):
        for item in value:_engineering_solver_receipts(item,profile,counts,exact_planner)
    elif isinstance(value,dict):
        if registered_exact_planner_call(value,profile):exact_planner=True
        if type(value.get('accepted')) is bool and 'raw_x' in value and isinstance(value.get('parameters'),dict):
            if value['accepted'] is True:
                if exact_planner:
                    validate_exact_planner_attempt(value,profile);counts['exact_planner_native_optimized']+=1
                else:
                    validate_attempt(value,profile)
                    counts['native_optimized' if value['optimizer_called'] else 'current_bound_certified_skip']+=1
            else:counts['exact_planner_failed_attempt_retained' if exact_planner else 'failed_attempt_retained']+=1
        for item in value.values():_engineering_solver_receipts(item,profile,counts,exact_planner)


def audit(source, producer, output, *, audit_row_fn=None):
    source, producer, output = [Path(path).resolve() for path in (source, producer, output)]
    if any(a == b or a.is_relative_to(b) or b.is_relative_to(a) for a, b in
           ((source, producer), (source, output), (producer, output))):
        raise ValueError('Audit output and immutable source/producer trees must be independent')
    if not source.is_dir() or not producer.is_dir():
        raise FileNotFoundError('Explicit source and completed producer directories are required')
    output.mkdir(parents=True, exist_ok=False); began = time.monotonic()
    result = dict(schema='hospital_ies.annual_dynamic.engineering_audit.v1', status='incomplete_or_failed',
        producer=str(producer), source=str(source), counts={key:{} for key in ('e','c','steam')},
        errors=[], error_count=0, original_bytes_unchanged=None, source_bytes_unchanged=None,
        physical_solver_calls=0, legacy_compiled_native_primal_qualified=False, whole_system_validated=False,
        OEM_startup_qualified=False, annual_run_qualified=False,
        qualification_scope='Actual engineering energy/stock/dynamic arithmetic and bound requested coverage only',
        carrier_count_semantics='Each passed carrier belongs to a fully passed joint engineering segment; failed/unknown segments never yield individual carrier passes')
    before = {}; after = {}; counters = {key:Counter() for key in ('e','c','steam')}
    requested = None; block_count = segment_count = 0; cursor = 0.; stocks = dynamic = None
    residuals = {}; totals = {}; chain_known = True; numerical_counts=Counter();parameter_usage=Counter()
    with (output / 'ERRORS.jsonl').open('xb') as error_stream, (output / 'AUDIT_ROWS.jsonl').open('xb') as rows_stream:
        def error(stage, exc, **context):
            item = dict(stage=stage, type=type(exc).__name__, message=str(exc), **context)
            error_stream.write(encoded(item, newline=True)); error_stream.flush()
            result['error_count'] += 1
            if len(result['errors']) < 20:
                result['errors'].append(item)
        try:
            for name, root in (('SOURCE', source), ('PRODUCER', producer)):
                before[name] = inventory(root, output / (name + '_BEFORE.jsonl'))
            producer_result, case, bindings, resolved, identity_sha, summary, requested = _header(source, producer)
            result['parameter_resolution']=deepcopy(resolved['_audit_parameter_resolution'])
            numerical = validated_numerical_profile(case, producer_result, resolved)
            if numerical is not None:
                result.update(numerical_profile=numerical['profile'], numerical_execution=numerical,
                    solver_numerical_tolerances=numerical['tolerances'], native_optimal_not_implied_by_success=True)
            if audit_row_fn is None:
                audit_row_fn, dependency = _load_audit_row(source,numerical_profile=numerical)
            else:
                dependency = dict(mode='injected_test_dependency', production_admission_eligible=False)
            result['audit_dependency'] = dependency
            result['binding'] = dict(git_commit=bindings['git_commit'], source_sha256=digest(bindings['source']),
                input_sha256=digest(bindings['inputs']), case_sha256=digest(case), identity_sha256=identity_sha,
                resolved_case_sha256=sha(producer / 'RESOLVED_CASE.json'), execution_model_id=MODEL_ID,
                owner_contract=OWNER_SCHEMA)
            records = summary.get('raw_files_sha256'); previous_digest = None; seen = set()
            if not isinstance(records, list) or not records:
                raise AuditUnknown('Missing journal raw block binding list')
            with (producer / 'evidence/blocks.jsonl').open(encoding='utf-8-sig') as index:
                for line in index:
                    if not line.endswith('\n'):
                        raise ValueError('Incomplete journal index record')
                    record = json.loads(line); name = 'blocks/block_' + str(block_count).zfill(6) + '.json.gz'
                    checksum = digest({key:value for key, value in record.items() if key != 'digest'}, newline=True)
                    if (record.get('schema') != JOURNAL_SCHEMA or record.get('index') != block_count
                            or record.get('file') != name or record.get('identity_sha256') != identity_sha
                            or record.get('previous_digest') != previous_digest or record.get('digest') != checksum):
                        raise ValueError('Journal block index identity/hash chain differs')
                    path = safe_path(producer / 'evidence', name)
                    if sha(path) != record.get('sha256') or block_count >= len(records) or records[block_count] != dict(file=name, sha256=record['sha256']):
                        raise ValueError('Journal raw block SHA or SUMMARY raw list differs')
                    with gzip.open(path, 'rt', encoding='utf8') as stream:
                        block = json.load(stream)
                    # Reject nonfinite values even outside the arithmetic fields.
                    encoded(block)
                    start = number(block.get('start_minute'), 'block start'); end = number(block.get('end_minute'), 'block end')
                    segments = require(block, 'segments'); calls = require(block, 'solver_calls')
                    if numerical is not None:_engineering_solver_receipts(calls,numerical,numerical_counts)
                    if (start != cursor or end != start + 15. or end > requested or not isinstance(segments, list)
                            or not segments or not isinstance(calls, list) or record.get('start_minute') != start
                            or record.get('end_minute') != end or record.get('segment_count') != len(segments)
                            or record.get('solver_call_count') != len(calls)):
                        raise ValueError('Journal 15-minute block coverage or count differs')
                    for segment in segments:
                        status = 'unknown'; row_start = row_end = None
                        try:
                            row_start = number(segment.get('start_minute'), 'segment start')
                            row_end = number(segment.get('end_minute'), 'segment end')
                            if row_start != cursor or not 0. < row_end-row_start <= 1.+CLOCK_TOL or row_end > end:
                                raise ValueError('Actual segment coverage gap/overlap or physical interval exceeds one minute')
                            _owner_and_cause(segment, case['execution_revision']['declared_calendar'], numerical_profile=numerical)
                            if not chain_known:
                                raise AuditUnknown('Previous failed/unknown segment prevents continuous stock/dynamic qualification')
                            recovery=number(require(segment,'action','recovery_cooling_kW'),'actual recovery cooling kW')
                            parameter_usage['zero_recovery_segments' if recovery==0. else 'active_recovery_segments']+=1
                            segment_resolved=_segment_audit_context(segment,resolved)
                            check = audit_row_fn(segment, segment_resolved, previous_stocks=stocks,
                                                 previous_dynamic=dynamic, residuals=residuals)
                            stocks, dynamic = check['stocks'], check['dynamic']
                            for key, value in check['energy_kWh'].items():
                                totals[key] = math.fsum((totals.get(key, 0.), number(value, 'audited energy')))
                            status = 'passed'
                        except Exception as exc:
                            status = 'unknown' if isinstance(exc, (AuditUnknown, KeyError, TypeError)) or 'Missing' in str(exc) or 'nonfinite' in str(exc) else 'failed'
                            chain_known = False; error('segment', exc, block_index=block_count, segment_index=segment_count,
                                                       start_minute=row_start, end_minute=row_end)
                        for counts in counters.values():
                            counts[status] += 1
                        rows_stream.write(encoded(dict(block_index=block_count, segment_index=segment_count,
                            start_minute=row_start, end_minute=row_end, status=status), newline=True))
                        segment_count += 1
                        if row_end is not None:
                            cursor = row_end
                    if cursor != end:
                        raise ValueError('Actual segments do not cover their complete journal block')
                    previous_digest = record['digest']; block_count += 1; seen.add(name)
                    del block
            actual_blocks = {path.relative_to(producer / 'evidence').as_posix() for path in (producer / 'evidence/blocks').iterdir()}
            if (cursor != requested or block_count != len(records) or block_count != summary.get('block_count')
                    or segment_count != summary.get('segment_count') or previous_digest != summary.get('last_digest')
                    or actual_blocks != seen):
                raise ValueError('Requested full-step journal coverage, terminal digest, or raw file coverage differs')
        except Exception as exc:
            error('producer', exc)
        finally:
            for name, root in (('SOURCE', source), ('PRODUCER', producer)):
                try:
                    after[name] = inventory(root, output / (name + '_AFTER.jsonl'))
                    unchanged = before.get(name) == after[name] if name in before else None
                    result['source_bytes_unchanged' if name == 'SOURCE' else 'original_bytes_unchanged'] = unchanged
                    if unchanged is not True:
                        error('byte_preservation', ValueError(name + ' full file/directory inventory changed or is unknown'))
                except Exception as exc:
                    error('byte_preservation', exc, tree=name)
            error_stream.flush(); os.fsync(error_stream.fileno())
            rows_stream.flush(); os.fsync(rows_stream.fileno())
    if 'parameter_resolution' in result:
        result['parameter_resolution'].update(row_usage=dict(parameter_usage),
            usage_scope='Segments reaching arithmetic parameter checks; failed/unknown segments are not imputed')
    result.update(counts={key:dict(value) for key, value in counters.items()},
        inventories=dict(before=before, after=after), coverage=dict(requested_end_minute=requested,
            observed_until_minute=cursor, block_count=block_count, segment_count=segment_count,
            is_full_year=requested == YEAR_MINUTES, requested_coverage_complete=bool(requested is not None and cursor == requested and block_count)),
        independently_computed_max_abs_residuals=residuals, known_prefix_integrated_energy_kWh=totals,
        engineering_solver_attempt_records=dict(numerical_counts),
        energy_totals_complete=bool(chain_known and requested is not None and cursor == requested and block_count),
        elapsed_seconds=time.monotonic()-began, script_sha256=sha(Path(__file__)))
    if (not result['error_count'] and requested is not None and cursor == requested and segment_count
            and all(value.get('passed') == segment_count and set(value) == {'passed'} for value in counters.values())
            and result['source_bytes_unchanged'] is True and result['original_bytes_unchanged'] is True):
        result['status'] = 'passed'
    write(output / 'RESULT.json', result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('source', 'producer', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args(argv)
    result = audit(args.source, args.producer, args.output)
    print(json.dumps({key:result[key] for key in ('status','counts','coverage','error_count')}, ensure_ascii=False))
    return 0 if result['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
