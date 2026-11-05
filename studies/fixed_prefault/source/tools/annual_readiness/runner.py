"""One explicit, failure-preserving continuous research case; no campaign.

The physical environment is reset once. Complete legal observation history and
the original actual-segment prefix are retained: inherited snapshot consumers
recompute telemetry from that prefix. Only committed solver-call history is
released after a durable journal receipt; it may also be retained for a proof.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import copy
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import traceback

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT/'src')]

from tools.annual_readiness import MODEL_ID, CONTRACT_ID
from tools.annual_readiness.journal import BlockJournal
from tools.sci_engineering.solver_log import normalize_json

SCHEMA = MODEL_ID+'.single_case_runner.v1'


def _read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def _bytes(value):
    return (json.dumps(normalize_json(value), ensure_ascii=False, allow_nan=False,
        separators=(',', ':'))+'\n').encode('utf8')


def _write_new(path, value, *, compressed=False):
    data = _bytes(value)
    with Path(path).open('xb') as stream:
        stream.write(gzip.compress(data, mtime=0) if compressed else data)
        stream.flush()
        os.fsync(stream.fileno())


def _append(path, value):
    with Path(path).open('ab') as stream:
        stream.write(_bytes(value))
        stream.flush()
        os.fsync(stream.fileno())


def _error(exc):
    return normalize_json(dict(type=type(exc).__name__, message=str(exc), traceback=traceback.format_exc(),
        underlying_category=getattr(exc,'category',None),
        diagnostic=copy.deepcopy(getattr(exc,'simulation_diagnostic',None)),
        solver_backend_attempts=copy.deepcopy(getattr(exc,'solver_backend_attempts',None))))


@contextmanager
def _local_temp(output):
    directory = str(Path(output)/'temp')
    original = {name:os.environ.get(name) for name in ('TEMP','TMP')}
    cached = tempfile.tempdir
    try:
        os.environ.update(TEMP=directory,TMP=directory)
        tempfile.tempdir = directory
        yield
    finally:
        tempfile.tempdir = cached
        for name,value in original.items():
            if value is None:
                os.environ.pop(name,None)
            else:
                os.environ[name] = value


def _snapshot(env):
    from tools.model_correction_v25.environment import decision_snapshot
    return decision_snapshot(env)


def _terminal(env, end):
    from tools.model_review_v30.terminal import terminal_snapshot
    return terminal_snapshot(env, end)


def _bindings():
    from tools.sci_engineering.runner import source_binding, input_binding
    head = subprocess.run(['git','rev-parse','HEAD'], cwd=ROOT, capture_output=True, text=True, check=False)
    if head.returncode:
        raise RuntimeError('Cannot bind current Git HEAD: '+head.stderr)
    return dict(source=source_binding(ROOT), inputs=input_binding(ROOT), git_commit=head.stdout.strip())


def _bindings_unchanged(original, extras):
    from tools.sci_engineering.runner import sha
    return original == _bindings() and all(sha(row['path']) == row['sha256'] for row in extras.values())


def _checked_output(value):
    from tools.model_correctness_v23.validation import checked_output
    output = checked_output(value)
    if os.name == 'nt' and output.drive.upper() not in ('D:','E:'):
        raise ValueError('Scientific outputs must remain on D/E')
    immutable_roots = (Path('D:/hospital_ies_v22_20261025'),
        Path('E:/hospital_ies_engineering_basis_20261029'),
        Path('D:/云大文件/论文/医院ies/code_4_2'))
    for protected in immutable_roots:
        if output == protected.resolve() or output.is_relative_to(protected.resolve()):
            raise ValueError('Output cannot modify an immutable historical workspace')
    return output


def _extra_inputs(case, registry):
    from tools.sci_engineering.runner import sha
    rows = copy.deepcopy(case.get('v27_load_overlay', {}).get('files', {}))
    rows.update(copy.deepcopy(case['v28_thermal_profile']['files']))
    rows.update(copy.deepcopy(registry.get('source_quality', {}).get('files', {})))
    result = {}
    for name, row in rows.items():
        path = Path(row['path'])
        path = path if path.is_absolute() else ROOT/path
        if sha(path) != row['sha256']:
            raise ValueError('Registered extra input changed: '+name)
        result[name] = dict(path=str(path.resolve()), sha256=row['sha256'])
    return result


def _build_runtime(case, registry, output, writer):
    from tools.annual_readiness.environment import with_readiness
    from tools.model_correction_v25.environment import MODEL_ID as CONSTRUCTOR_MODEL_ID
    from tools.model_correctness_v23 import policy
    from tools.model_closure_v22.validation import runtime_policy
    from tools.sci_engineering.design import allowed_hours, SEALED_DAYS
    meta = case['annual_readiness']
    adapter = with_readiness(meta['method'],
        requalification_minutes=meta['parameters']['requalification_minutes'],
        overlay_spec=case.get('v27_load_overlay'), thermal_profile=case['v28_thermal_profile'])
    config = copy.deepcopy(registry['hardware_profiles'][case['configuration']])
    for key in ('model_closure_v22','model_correctness_v23','engineering_completion_v24','model_correction_v25'):
        config[key] = copy.deepcopy(case.get(key, registry[key]))
    rule_id = case.get('operating_rule_id', registry.get('default_operating_rule'))
    if meta['method'].endswith('_ready'):
        rule = dict(name='sustained_critical', design_minutes=60.)
    else:
        from tools.model_correction_v25.validation import resolve_operating_rule
        rule_id, rule = resolve_operating_rule(registry, case)
    env = adapter.OperatingHospitalIESEnv(ROOT,
        allowed_hours=allowed_hours(dict(scenarios=[case['scenario']])), sealed_days=SEALED_DAYS,
        model_id=CONSTRUCTOR_MODEL_ID, model_config=config, operating_rule=rule,
        solver_row_sidecar=writer, solver_evidence_directory=output/'domain_evidence',
        enable_all_validation_rows=True)
    if env.control_rule_id != rule_id:
        raise ValueError('Registered and actual operating-rule identities differ')
    spec = runtime_policy(case, registry, policy)
    policy_id = meta['method']+'_annual_readiness_20261030'
    controller = adapter.controller_factory(spec['alias'], config=spec['config'], plant=env.plant, policy_id=policy_id)
    return env, controller, dict(model_config=config, control_rule_id=rule_id,
        control_rule=copy.deepcopy(env.control_rule), policy=dict(alias=spec['alias'],
            policy_id=getattr(controller,'policy_id',policy_id), config=copy.deepcopy(controller.config)))


def _block_evaluation(segments, start, end, *, preparation, parameters, tariffs):
    from tools.model_correctness_v23.metrics import build_reliability_metrics
    from tools.model_review_v30.evaluation import independent_energy_audit
    payload = dict(segments=segments, continuation_start_minute=start,
        summary=dict(requested_end_minute=end), m21_initial_storage=preparation,
        accounting_parameters=parameters, audit_tariffs_by_hour=tariffs)
    native = build_reliability_metrics(payload)
    intervals = {p:{c:copy.deepcopy(row['intervals']) for c,row in carriers.items()}
        for p,carriers in native['carrier_service'].items()}
    return dict(native_material_intervals=intervals, native_material_definition=native['definition'],
        block_native_support_complete=native['support_complete'],
        native_recovery_metrics=None,
        native_recovery_metrics_reason='Block intervals use the unchanged native function; cross-block confirmation and recovery are not inferred.',
        economics=independent_energy_audit(payload))


def _row_references(value, location=()):
    if isinstance(value,(list,tuple)):
        for i,item in enumerate(value):
            yield from _row_references(item,location+(i,))
    elif isinstance(value,dict):
        for key,item in value.items():
            if key == 'rows_ref' and isinstance(item,dict):
                yield item,list(location)
            else:
                yield from _row_references(item,location+(key,))


def _seal_solver_evidence(env, block, output, *, call_cursor):
    """Close/fsync original writers and verify every referenced logging table."""
    from tools.sci_engineering.solver_log import SolverRowSidecar,REF_SCHEMA,iter_sidecar_entries
    from tools.model_correctness_v23.evidence import REFERENCE_SCHEMA,ALL_REFERENCE_SCHEMA,_row_chunks
    from tools.sci_engineering.runner import sha
    writer = getattr(env.core.solver_calls,'writer',None)
    receipt = None
    if isinstance(writer,SolverRowSidecar):
        writer.close()
        receipt = writer.receipt()
        if receipt['status'] != 'completed':
            raise RuntimeError('Original solver-row writer did not close successfully')
        # Windows _commit requires a write-capable descriptor; no bytes change.
        with writer.path.open('r+b') as stream:
            os.fsync(stream.fileno())
        expected = []
        for i,call in enumerate(block['solver_calls']):
            expected.extend((ref,location) for ref,location in _row_references(call,(call_cursor+i,))
                if ref.get('schema') == REF_SCHEMA)
        entries = iter(iter_sidecar_entries(writer.path))
        for ref,location in expected:
            entry = next(entries,None)
            if (entry is None or ref.get('file') != writer.path.name or entry.get('location') != location or
                    any(ref.get(k) != entry.get(k) for k in ('record_id','rows_sha256','row_count'))):
                raise ValueError('Solver-row block reference, location or SHA mismatch')
        if next(entries,None) is not None:
            raise ValueError('Unreferenced solver-row sidecar entry')
        if sha(writer.path) != receipt['gzip_sha256']:
            raise ValueError('Solver-row sidecar raw SHA mismatch')
        receipt['durable_and_stream_verified_before_block_ack'] = True
    domain = {}
    for ref,_ in _row_references(block):
        if ref.get('schema') not in (REFERENCE_SCHEMA,ALL_REFERENCE_SCHEMA):
            continue
        relative = Path(ref.get('directory',''))/ref.get('file','')
        path = (Path(output)/relative).resolve()
        if not path.is_relative_to((Path(output)/'domain_evidence').resolve()):
            raise ValueError('Domain evidence reference escaped its case directory')
        name = relative.as_posix()
        if name in domain:
            if any(ref.get(k) != domain[name][k] for k in ('rows_sha256','row_count')):
                raise ValueError('Conflicting domain evidence references')
            continue
        with path.open('r+b') as stream:
            os.fsync(stream.fileno())
        with gzip.open(path,'rt',encoding='utf8') as stream:
            rows = json.load(stream)
        digest = hashlib.sha256()
        for chunk in _row_chunks(rows):
            digest.update(chunk)
        if digest.hexdigest() != ref.get('rows_sha256') or len(rows) != ref.get('row_count'):
            raise ValueError('Domain evidence rows SHA/count mismatch')
        domain[name] = dict(rows_sha256=ref['rows_sha256'],row_count=ref['row_count'],
            gzip_sha256=sha(path),gzip_bytes=path.stat().st_size,
            durable_and_verified_before_block_ack=True)
        del rows
    return receipt,domain


def _execute_loop(env, controller, scenario, journal, output, *, retain_history=False,
                  accounting_parameters=None, tariffs_by_hour=None, on_reset=None):
    """Execute once without constructing native state; usable with test doubles."""
    from tools.model_correctness_v23.policy import validate_action
    output = Path(output)
    requested = float(scenario['duration_steps'])*float(scenario.get('step_minutes',15.))
    records, committed_end, solver_base, call_cursor, event_cursor, error_cursor = [], 0., 0, 0, 0, 0
    operating_fees, preparation_fee = [], None
    failure = None
    current = request = diagnostic = None
    core_end = 0.
    partial_context = None
    sidecars = []
    with (output/'ACTUAL_PREFIX.jsonl').open('xb') as prefix:
        def actual_segment(segment):
            prefix.write(_bytes(segment)); prefix.flush(); os.fsync(prefix.fileno())
        env.actual_segment_observer = actual_segment
        try:
            env.reset(scenario=copy.deepcopy(scenario), seed=int(scenario.get('initialization_seed',20261023)))
            if float(env.core.time) != 0.:
                raise ValueError('Continuous execution must start at actual minute zero')
            if on_reset is not None:
                on_reset(env)
            initial_preparation = {name:getattr(getattr(env.core,name,None),'initial_charged_input_kWh',None)
                for name in ('ups','cold_store')}
            while committed_end < requested:
                block_start = committed_end
                step_error = None
                info = {}
                current = request = diagnostic = None
                try:
                    current = normalize_json(_snapshot(env))
                    if records and float(records[-1]['decision_time_min']) >= float(current['decision_time_min']):
                        raise ValueError('Legal observation history is not strictly increasing')
                    if float(current['decision_time_min']) != block_start:
                        raise ValueError('Current observation does not match committed decision boundary')
                    records.append(copy.deepcopy(current))
                    request, diagnostic = controller.decide(copy.deepcopy(records))
                    validate_action(request)
                    returned = env.step(request)
                    if not isinstance(returned, tuple) or len(returned) != 5:
                        raise ValueError('Environment step must return its original five-field interface')
                    _, _, done, truncated, info = returned
                    segments = info['segments']
                except Exception as exc:
                    step_error = _error(exc)
                    segments = [s for s in env.core.segments if float(s['start_minute']) >= block_start]
                core_end = float(env.core.time)
                calls = list(env.core.solver_calls[call_cursor:])
                events = list(env.core.events[event_cursor:])
                native_errors = list(getattr(env.core,'errors',[])[error_cursor:])
                generated = [e.get('command') for e in events if e.get('kind') == 'command_generated'
                    and (not info or e.get('command',{}).get('id') == info.get('command_id'))]
                try:
                    terminal = _terminal(env,core_end)
                except Exception as exc:
                    terminal_error = _error(exc)
                    terminal = dict(completeness='unknown_terminal_export_error',error=terminal_error,
                        snapshot_time_minute=None,observed_until_minute=core_end)
                    step_error = step_error or terminal_error
                partial_context = dict(start_minute=block_start,
                    end_minute=segments[-1]['end_minute'] if segments else block_start,
                    segments=segments, solver_calls=calls, events=events, solver_errors=native_errors,
                    solver_index_base=solver_base, solver_call_local_start_index=call_cursor,
                    solver_index_semantics='global original list index' if retain_history else 'block local; combine with solver_index_base',
                    decision=dict(current_observation=current, requested_command=request,
                        command_generated=generated[-1] if generated else None, diagnostic=diagnostic),
                    terminal=terminal, error=step_error)
                if not segments:
                    if step_error is None:
                        step_error = dict(type='MissingActualSegments', message='A decision completed without actual intervals')
                        partial_context['error'] = step_error
                    _write_new(output/('FAILURE_CONTEXT_'+format(len(records),'06d')+'.json.gz'), partial_context, compressed=True)
                    failure = step_error
                    break
                end = float(segments[-1]['end_minute'])
                prep = initial_preparation if block_start == 0. else dict(ups=0.,cold_store=0.)
                partial_context['initial_preparation'] = copy.deepcopy(initial_preparation) if block_start == 0. else None
                partial_context['initial_preparation_added_again'] = False
                try:
                    partial_context.update(_block_evaluation(normalize_json(segments), block_start, end,
                        preparation=prep, parameters=accounting_parameters or {}, tariffs=tariffs_by_hour or {}))
                except Exception as exc:
                    evaluation_error = _error(exc)
                    step_error = step_error or evaluation_error
                    partial_context.update(native_material_intervals=None, native_evaluation_error=evaluation_error,
                        error=step_error,
                        economics=None, native_recovery_metrics=None)
                try:
                    sidecar,domain_receipts = _seal_solver_evidence(env,partial_context,output,call_cursor=call_cursor)
                    partial_context['solver_row_sidecar'] = sidecar
                    partial_context['domain_evidence_files'] = domain_receipts
                    if sidecar is not None:
                        sidecars.append(sidecar)
                    receipt = journal.append(normalize_json(partial_context))
                except Exception as exc:
                    partial_context['journal_append_error'] = _error(exc)
                    failed_writer = getattr(env.core.solver_calls,'writer',None)
                    if callable(getattr(failed_writer,'receipt',None)):
                        partial_context['solver_row_sidecar'] = failed_writer.receipt()
                    _write_new(output/('REJECTED_BLOCK_'+format(len(records),'06d')+'.json.gz'), partial_context, compressed=True)
                    failure = partial_context['journal_append_error']
                    break
                committed_end = end
                economics = partial_context.get('economics') or {}
                operating_fees.append(economics.get('observed_operating_fee_USD2001'))
                if block_start == 0.:
                    preparation_fee = economics.get('initial_preparation_fee_USD2001')
                solver_base += len(calls)
                event_cursor = len(env.core.events)
                error_cursor = len(getattr(env.core,'errors',[]))
                if retain_history:
                    call_cursor = len(env.core.solver_calls)
                else:
                    # Preserve original logging sink type/writer and live aliases.
                    env.core.solver_calls.clear()
                    # Original make_snapshot requires every completed segment
                    # from minute zero. Its absolute observer cursor is unchanged.
                    call_cursor = 0
                _append(output/'PROGRESS.jsonl', dict(block_count=receipt['index']+1,
                    observed_until_minute=committed_end, requested_end_minute=requested,
                    source_model_id=MODEL_ID, retained_solver_calls=len(env.core.solver_calls),
                    retained_segments=len(env.core.segments), legal_history_count=len(records)))
                partial_context = None
                if step_error is not None:
                    failure = step_error
                    break
                if (done or truncated) and committed_end != requested:
                    failure = dict(type='EarlyTermination', message='Environment stopped before the requested continuous horizon')
                    break
                if committed_end < requested and sidecar is not None:
                    from tools.sci_engineering.solver_log import SolverRowSidecar
                    fresh = SolverRowSidecar(output/('solver_rows_block_'+format(receipt['index']+1,'06d')+'.jsonl.gz'))
                    env.core.solver_calls.writer = fresh
                    env._solver_row_sidecar = fresh
        except Exception as exc:
            failure = _error(exc)
            if getattr(env,'core',None) is not None:
                core_end = float(env.core.time)
                context = partial_context or dict(start_minute=committed_end, end_minute=core_end,
                    segments=[s for s in env.core.segments if float(s['start_minute']) >= committed_end],
                    solver_calls=list(env.core.solver_calls[call_cursor:]), events=list(env.core.events[event_cursor:]),
                    solver_errors=list(getattr(env.core,'errors',[])[error_cursor:]),
                    decision=dict(current_observation=current, requested_command=request, diagnostic=diagnostic),
                    terminal=None, error=failure)
                _write_new(output/'FAILURE_CONTEXT_OUTER.json.gz', context, compressed=True)
        finally:
            env.actual_segment_observer = None
            from tools.sci_engineering.solver_log import SolverRowSidecar
            active_writer = getattr(getattr(getattr(env,'core',None),'solver_calls',None),'writer',None)
            if isinstance(active_writer,SolverRowSidecar):
                try:
                    if not active_writer.closed:
                        if failure is not None:
                            active_writer.body_exception = dict(type='ContinuousRunFailed',message=str(failure)[:1000])
                        active_writer.close()
                except Exception as exc:
                    failure = failure or _error(exc)
                active_receipt = active_writer.receipt()
                if isinstance(active_receipt,dict) and not any(row.get('path') == active_receipt.get('path') for row in sidecars):
                    sidecars.append(active_receipt)
    status = 'completed' if failure is None and committed_end == requested else 'failed'
    try:
        summary = journal.close(status=status, expected_end_minute=requested, error=failure)
    except Exception as exc:
        close_error = _error(exc)
        _write_new(output/'JOURNAL_CLOSE_ERROR.json', close_error)
        failure = failure or close_error
        status = 'failed'
        try:
            summary = journal.close(status='failed', expected_end_minute=requested, error=failure)
        except Exception as second:
            summary = None
            _write_new(output/'JOURNAL_CLOSE_RECOVERY_ERROR.json', _error(second))
    operating_fee = math.fsum(operating_fees) if operating_fees and all(isinstance(v,(int,float)) and math.isfinite(v) for v in operating_fees) else None
    actual_fee = operating_fee+preparation_fee if operating_fee is not None and preparation_fee is not None else None
    return dict(status=status, error=failure, summary=summary, observed_until_minute=committed_end,
        actual_core_until_minute=core_end, unknown_tail_minutes=requested-committed_end,
        requested_end_minute=requested, reset_count=1, legal_observation_count=len(records),
        retained_history=retain_history, solver_call_history_retained=retain_history,
        segment_history_mode='complete_original_prefix', bounded_segment_history=False,
        retained_actual_segment_count=len(getattr(getattr(env,'core',None),'segments',[])),
        economics=dict(observed_operating_fee_USD2001=operating_fee,
            initial_preparation_fee_USD2001=preparation_fee, actual_fee_USD2001=actual_fee,
            unobserved_tail_fee_USD2001=None, price_year=2001, currency='USD',
            source='native V30 independent_energy_audit on complete actual blocks; initial preparation once'),
        solver_errors=normalize_json(copy.deepcopy(getattr(getattr(env,'core',None),'errors',[]))),
        solver_row_sidecars=sidecars,
        native_cross_block_recovery=None, chemical_diesel_fuel_liters=None, DC_energy_kWh=None)


def _prepare_attempt(case, output, *, retain_history, case_input_binding=None):
    from tools.annual_readiness.registration import load_registry, validate_case, worker_registry
    from tools.model_review_v29.source_scope import window_scope
    registry = load_registry()
    case = validate_case(case, registry)
    window_scope(ROOT, case['scenario'])
    output = _checked_output(output)
    output.mkdir(parents=True, exist_ok=False)
    (output/'temp').mkdir()
    _write_new(output/'CASE.json', dict(case=case, registry=worker_registry(case,registry),
        retain_history=retain_history, case_input_binding=case_input_binding))
    bindings = _bindings()
    _write_new(output/'BINDINGS.json', bindings)
    return case, output, bindings


def _execute_attempt(output, *, runtime_builder=None):
    from tools.annual_readiness.registration import load_registry, validate_case, worker_registry
    from tools.sci_engineering.design import digest, allowed_hours
    from tools.sci_engineering.runner import sha
    from tools.sci_engineering.solver_log import SolverRowSidecar
    from tools.model_review_v30.evaluation import _load_frozen_accounting
    output = Path(output).resolve()
    saved = _read(output/'CASE.json')
    registry = load_registry()
    case = validate_case(saved['case'],registry)
    if digest(saved['registry']) != digest(worker_registry(case,registry)):
        raise ValueError('Saved worker registry differs from the trusted registration')
    registry = saved['registry']
    bindings = _read(output/'BINDINGS.json')
    started = time.perf_counter()
    result = dict(schema=SCHEMA, case_id=case['id'], model_id=MODEL_ID, contract_id=CONTRACT_ID,
        status='failed', actual_episode_attempts=0, git_commit=bindings['git_commit'],
        source_sha256=digest(bindings['source']), input_sha256=bindings['inputs'], case_sha256=digest(case),
        source_and_inputs_unchanged=None, error=None, chemical_diesel_fuel_liters=None, DC_energy_kWh=None)
    writer = env = None
    extras = {}
    requested = float(case['scenario']['duration_steps'])*float(case['scenario'].get('step_minutes',15.))
    journal = BlockJournal(output/'evidence', identity=dict(case_id=case['id'], model_id=MODEL_ID,
        git_commit=bindings['git_commit'], source_sha256=result['source_sha256'],
        input_sha256=digest(bindings['inputs']), case_sha256=result['case_sha256']))
    try:
        extras = _extra_inputs(case,registry)
        if not _bindings_unchanged(bindings,extras):
            raise ValueError('Source/input/HEAD drift before native construction')
        supplied = saved.get('case_input_binding')
        if supplied and sha(supplied['path']) != supplied['sha256']:
            raise ValueError('Submitted case file changed before execution')
        result['extra_input_bindings'] = extras
        writer = SolverRowSidecar(output/'solver_rows_block_000000.jsonl.gz')
        env, controller, resolved = (runtime_builder or _build_runtime)(case,registry,output,writer)
        _write_new(output/'SOLVER_CONTEXT.json', dict(enable_all_validation_rows=True,
            domain_evidence_directory=str(output/'domain_evidence'), writer_schema=writer.receipt()['schema'],
            local_indices_are_block_namespaced=not saved['retain_history'],
            normalization='existing normalize_json; nonfinite numbers remain null, never zero'))
        support = sorted(allowed_hours(dict(scenarios=[case['scenario']])))
        parameters, tariffs, accounting_sources = _load_frozen_accounting(ROOT,bindings,[{'hour_of_year':h} for h in support])
        parameters['cold_pump_kW_per_kW'] = env.sci_config.get('cold_pump_kW_per_kW')
        result['actual_episode_attempts'] = 1
        def record_resolved(actual):
            resolved['model_config'] = copy.deepcopy(actual.operating_model_config)
            _write_new(output/'RESOLVED_CASE.json', dict(case=case, **resolved, allowed_hours=support,
                input_access_receipt=copy.deepcopy(getattr(actual,'input_access_receipt',None))))
        execution = _execute_loop(env,controller,case['scenario'],journal,output,
            retain_history=saved['retain_history'],accounting_parameters=parameters,tariffs_by_hour=tariffs,
            on_reset=record_resolved)
        result.update(execution, configuration=case['configuration'], annual_readiness=case['annual_readiness'],
            resolved_model_config=copy.deepcopy(env.operating_model_config), accounting_input_bindings=accounting_sources)
    except Exception as exc:
        result.update(status='failed',error=_error(exc))
        if 'summary' not in result:
            try:
                result['summary'] = journal.close(status='failed',expected_end_minute=requested,error=result['error'])
            except Exception as second:
                result['journal_close_error'] = _error(second)
        result.setdefault('observed_until_minute',0.)
        result.setdefault('unknown_tail_minutes',requested)
    finally:
        if writer is not None:
            try:
                if result['status'] != 'completed':
                    writer.body_exception = dict(type='ContinuousRunFailed',message=str(result.get('error'))[:1000])
                writer.close()
                result['solver_row_sidecar'] = writer.receipt()
                _write_new(output/'SOLVER_ROWS_RECEIPT.json',result['solver_row_sidecar'])
            except Exception as exc:
                result.update(status='failed',solver_row_evidence_error=_error(exc))
        domain = output/'domain_evidence'
        if domain.is_dir():
            result['domain_evidence'] = dict(files={p.relative_to(output).as_posix():dict(sha256=sha(p),bytes=p.stat().st_size)
                for p in sorted(domain.rglob('*')) if p.is_file()}, enable_all_validation_rows=True)
        try:
            unchanged = _bindings_unchanged(bindings,extras)
            supplied = saved.get('case_input_binding')
            if supplied:
                unchanged = unchanged and sha(supplied['path']) == supplied['sha256']
            result['source_and_inputs_unchanged'] = unchanged
            if not unchanged:
                result.update(status='failed',source_drift_error='Source, input, case-file or HEAD bytes changed during this attempt')
        except Exception as exc:
            result.update(status='failed',source_drift_error=_error(exc),source_and_inputs_unchanged=False)
        result['elapsed_seconds'] = time.perf_counter()-started
        _write_new(output/'RESULT.json',result)
        _write_new(output/'SUMMARY.json', {k:result.get(k) for k in ('schema','case_id','model_id','status',
            'actual_episode_attempts','git_commit','source_sha256','input_sha256','observed_until_minute',
            'unknown_tail_minutes','error','source_and_inputs_unchanged','summary','economics',
            'native_cross_block_recovery','chemical_diesel_fuel_liters','DC_energy_kWh')})
    return result


def run_case(case, output, *, retain_history=False):
    """Run one canonical case into a fresh output; return its actual RESULT."""
    case, output, _ = _prepare_attempt(case,output,retain_history=retain_history)
    _write_new(output/'INVOCATION.json',dict(mode='direct_callable',case_id=case['id'],retain_history=retain_history))
    with _local_temp(output):
        return _execute_attempt(output)


def main(argv=None):
    from tools.sci_engineering.runner import sha
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case-file',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--retain-history',action='store_true',
        help='Also retain committed solver calls; actual segments and legal observations are always retained')
    parser.add_argument('--_worker',action='store_true',help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args._worker:
        result = _execute_attempt(args.output)
        return 0 if result['status'] == 'completed' else 1
    if args.case_file is None:
        parser.error('--case-file is required')
    case_file = args.case_file.resolve()
    case, output, _ = _prepare_attempt(_read(case_file),args.output,retain_history=args.retain_history,
        case_input_binding=dict(path=str(case_file),sha256=sha(case_file)))
    command = [sys.executable,'-I',str(Path(__file__).resolve()),'--_worker','--output',str(output)]
    _write_new(output/'INVOCATION.json',dict(command=command,cwd=str(ROOT),case_id=case['id'],
        retain_history=args.retain_history,runner_sha256=sha(__file__)))
    environment = dict(os.environ,TEMP=str(output/'temp'),TMP=str(output/'temp'),
        SCI_SOURCE_ROOT=str(ROOT),PYTHONPATH=os.pathsep.join((str(ROOT),str(ROOT/'src'))),
        PYTHONDONTWRITEBYTECODE='1',PYTHONIOENCODING='utf-8')
    with (output/'stdout.log').open('x',encoding='utf8') as out,(output/'stderr.log').open('x',encoding='utf8') as err:
        run = subprocess.run(command,cwd=ROOT,env=environment,stdout=out,stderr=err,check=False)
    _write_new(output/'EXIT.json',dict(exit_code=run.returncode))
    result = _read(output/'RESULT.json') if (output/'RESULT.json').is_file() else dict(status='failed_missing_result')
    print(json.dumps(dict(case_id=case['id'],status=result['status'],exit_code=run.returncode,output=str(output))))
    return run.returncode if run.returncode else 0 if result['status']=='completed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
