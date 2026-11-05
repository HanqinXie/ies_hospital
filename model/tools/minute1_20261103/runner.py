"""One externally admitted minute case with complete actual/NL evidence.

All logical execution functions are source-defined in this module. The block
loop is a bounded development-time derivative of annual_readiness.runner; it
keeps its failure/partial-prefix and sidecar rules, adding the registered
post60 no-upper-command intervention and an explicit one-minute clock.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from copy import deepcopy
import copy
import math
import os
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
if str(ROOT/'src') not in sys.path:sys.path.insert(0,str(ROOT/'src'))

from hospital_ies.nonlinear import nonlinear_evidence_scope
from tools.annual_readiness import runner as native
from tools.annual_readiness.registration import load_registry
from tools.annual_readiness.journal import BlockJournal
from tools.annual_readiness.runner import (_read,_bytes,_write_new,_append,_error,
    _local_temp,_bindings,_bindings_unchanged,_terminal,_block_evaluation,_seal_solver_evidence)
from tools.annual_program.compiled_capture import capture_scope
from tools.annual_program.witness import compiled_origin
from tools.eight_fault_study.runtime import runtime_configuration
from tools.eight_fault_study.runner import _check_stopfile
from tools.sci_engineering.design import allowed_hours,digest,SEALED_DAYS
from tools.sci_engineering.solver_log import normalize_json,SolverRowSidecar
from tools.model_correction_v25.environment import MODEL_ID as CONSTRUCTOR_MODEL_ID
from tools.model_review_v30.evaluation import _load_frozen_accounting
from tools.single_fault_model_20261102.runtime import ModelSpec as StorageSpec
from tools.single_fault_fuel_20261102.runtime import ModelSpec as FuelSpec
from tools.single_fault_fuel_20261102.nl_store import NonlinearStore
from tools.minute1_20261103 import MODEL_ID
from tools.minute1_20261103.registration import validate_case,worker_registry
from tools.minute1_20261103.source import MinuteSource,sha256
from tools.minute1_20261103.runtime import MinuteSamplingSpec,validate_minute_scenario
from tools.minute1_20261103.environment import with_minute1,decision_snapshot
from tools.minute1_20261103.profile import read_source_admission,verify_source_admission

SCHEMA='hospital_ies.minute1.single_case_runner.v1'
OUTPUT_ROOT=Path('E:/hospital_ies_minute1_20261103_run01')
VARIANTS=('normal','unattended','responsive')


def _snapshot(env):
    return decision_snapshot(env)


def _checked_output(value):
    output=native._checked_output(value)
    if not output.is_relative_to(OUTPUT_ROOT.resolve()):
        raise ValueError('Minute research output must stay under its new E data root')
    return output


def _extra_inputs(case,registry,source_admission):
    rows={name:dict(value) for name,value in native._extra_inputs(case['minute1']['parent'],registry).items()}
    for name,value in case['minute1']['minute_source']['files'].items():
        path=Path(value['path']);path=path if path.is_absolute() else ROOT/path
        if sha256(path)!=value['sha256']:raise ValueError('Minute input changed: '+name)
        rows['minute1_'+name]=dict(path=str(path.resolve()),sha256=value['sha256'])
    admission=read_source_admission(source_admission)
    rows['minute1_source_admission']=deepcopy(source_admission)
    rows['minute1_source_review']=deepcopy(admission['review_receipt'])
    return rows


def _prepare_attempt(case,output,*,sampling_spec,source_admission,retain_history=False,case_input_binding=None):
    registry=load_registry();case=validate_case(case,registry)
    validate_minute_scenario(case['scenario'])
    bindings=_bindings()
    verify_source_admission(ROOT,source_admission,source_hashes=bindings['source'])
    working=worker_registry(case,registry)
    _extra_inputs(case,working,source_admission)
    output=_checked_output(output);output.mkdir(parents=True,exist_ok=False)
    (output/'temp').mkdir()
    _write_new(output/'CASE.json',dict(case=case,registry=working,retain_history=retain_history,
        case_input_binding=case_input_binding,source_admission=deepcopy(source_admission),sampling_spec=sampling_spec.as_dict()))
    _write_new(output/'BINDINGS.json',bindings)
    return case,output,bindings


def _build_runtime(case,registry,output,writer,*,sampling_spec,source_admission,bindings):
    trusted=load_registry();case=validate_case(case,trusted)
    if digest(registry)!=digest(worker_registry(case,trusted)):
        raise ValueError('Minute worker registry differs from its authenticated canonical parent')
    verify_source_admission(ROOT,source_admission,source_hashes=bindings['source'])
    parent=case['minute1']['parent'];meta=case['minute1']
    configuration=runtime_configuration(parent,registry)
    source=MinuteSource(ROOT,meta['minute_source'])
    adapter=with_minute1(meta['method'],minute_source=source,sampling_spec=sampling_spec,
        source_admission=source_admission,source_freeze=bindings['source'],variant=meta['variant'],
        requalification_minutes=meta['parameters']['requalification_minutes'],
        overlay_spec=parent.get('v27_load_overlay'),thermal_profile=parent['v28_thermal_profile'])
    hours=allowed_hours(dict(scenarios=[case['scenario']]))
    env=adapter.OperatingHospitalIESEnv(ROOT,allowed_hours=hours,sealed_days=SEALED_DAYS,
        model_id=CONSTRUCTOR_MODEL_ID,model_config=deepcopy(configuration['model_config']),
        operating_rule=deepcopy(configuration['operating_rule']),solver_row_sidecar=writer,
        solver_evidence_directory=output/'domain_evidence',enable_all_validation_rows=True)
    policy=configuration['policy'];policy_id=meta['method']+'_minute1_20261103'
    controller=adapter.controller_factory(policy['alias'],config=policy['config'],plant=env.plant,policy_id=policy_id)
    resolved=dict(model_config=deepcopy(env.operating_model_config),control_rule_id=env.control_rule_id,
        control_rule=deepcopy(env.control_rule),policy=dict(alias=policy['alias'],policy_id=getattr(controller,'policy_id',policy_id),config=deepcopy(controller.config)),
        execution_model_id=MODEL_ID,control_sampling_model_id=MODEL_ID,
        physical_model_spec=env.single_fault_fuel_spec.as_dict(),storage_model_spec=env.single_fault_model_spec.as_dict(),
        sampling_spec=sampling_spec.as_dict(),source_admission=deepcopy(source_admission),
        minute_source=deepcopy(meta['minute_source']),variant=meta['variant'],
        original_parent_case_id=parent['id'],original_parent_case_sha256=digest(parent),
        original_parent_unchanged=True,annual_numeric_grant=False,old_results_reused_as_new_outcomes=False)
    return env,controller,resolved


# The actual source-defined block loop follows. Its native accounting and
# journal helpers are imported with their original owner identities above.

def _execute_loop(env, controller, scenario, journal, output, *, retain_history=False,
                  accounting_parameters=None, tariffs_by_hour=None, on_reset=None,
                  variant='responsive',stopfile=None):
    """Execute once without constructing native state; usable with test doubles."""
    from tools.model_correctness_v23.policy import validate_action
    if variant not in VARIANTS:raise ValueError('Unknown registered minute intervention')
    output = Path(output)
    requested = float(scenario['duration_steps'])*float(scenario.get('step_minutes',1.))
    records, committed_end, solver_base, call_cursor, event_cursor, error_cursor = [], 0., 0, 0, 0, 0
    operating_fees, preparation_fee = [], None
    controller_calls=0
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
                    _check_stopfile(stopfile,'before minute native step')
                    if variant=='unattended' and block_start>=60.:
                        request=None
                        diagnostic=dict(upper_commands_suppressed=True,reason='registered_unattended_after60',pending_commands_retained=True)
                    else:
                        request,diagnostic=controller.decide(copy.deepcopy(records))
                        controller_calls+=1
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
        controller_call_count=controller_calls,variant=variant,upper_commands_disabled_at_minute=60. if variant=='unattended' else None,
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


def _execute_attempt(output,*,sampling_spec,stopfile=None):
    output=Path(output).resolve()
    if (output/'RESULT.json').exists() or (output/'SUMMARY.json').exists():
        raise ValueError('A fresh minute attempt without final outputs is required')
    try:
        saved=_read(output/'CASE.json');bindings=_read(output/'BINDINGS.json')
        registry=load_registry();case=validate_case(saved['case'],registry)
        if digest(saved['registry'])!=digest(worker_registry(case,registry)):
            raise ValueError('Saved minute worker registry differs from its canonical parent')
        if saved.get('sampling_spec')!=sampling_spec.as_dict():raise ValueError('Saved minute sampling specification changed')
    except Exception as exc:
        failure=dict(schema=SCHEMA,model_id=MODEL_ID,status='failed',actual_episode_attempts=0,
            case_id=None,preflight_case_validated=False,error=_error(exc),observed_until_minute=0.,
            unknown_tail_minutes=None,source_and_inputs_unchanged=None)
        _write_new(output/'RESULT.json',failure);_write_new(output/'SUMMARY.json',failure)
        return failure
    registry=saved['registry'];reference=saved['source_admission'];started=time.perf_counter()
    requested=float(case['scenario']['duration_steps'])*float(case['scenario']['step_minutes'])
    storage=StorageSpec(physical_max_segment_minutes=sampling_spec.physical_max_segment_minutes)
    fuel=FuelSpec(physical_max_segment_minutes=sampling_spec.physical_max_segment_minutes)
    result=dict(schema=SCHEMA,case_id=case['id'],model_id=MODEL_ID,status='failed',actual_episode_attempts=0,
        git_commit=bindings['git_commit'],source_sha256=digest(bindings['source']),input_sha256=deepcopy(bindings['inputs']),
        case_sha256=digest(case),source_admission=deepcopy(reference),sampling_spec=sampling_spec.as_dict(),
        physical_model_spec=fuel.as_dict(),storage_model_spec=storage.as_dict(),
        source_and_inputs_unchanged=None,error=None,chemical_diesel_fuel_liters=None,DC_energy_kWh=None)
    journal=BlockJournal(output/'evidence',identity=dict(case_id=case['id'],model_id=MODEL_ID,
        git_commit=bindings['git_commit'],source_sha256=result['source_sha256'],
        input_sha256=digest(bindings['inputs']),case_sha256=result['case_sha256']))
    writer=env=store=session=None;extras={}
    try:
        extras=_extra_inputs(case,registry,reference)
        if not _bindings_unchanged(bindings,extras):raise ValueError('Source/input/HEAD drift before minute construction')
        supplied=saved.get('case_input_binding')
        if supplied and sha256(supplied['path'])!=supplied['sha256']:raise ValueError('Submitted minute case file changed')
        verify_source_admission(ROOT,reference,source_hashes=bindings['source'])
        result['extra_input_bindings']=deepcopy(extras)
        _check_stopfile(stopfile,'before minute native construction')
        origin=compiled_origin(ROOT,bindings['source'],source_admission=reference)
        writer=SolverRowSidecar(output/'solver_rows_block_000000.jsonl.gz')
        store=NonlinearStore(output,bindings=bindings,case_id=case['id'],physical_model_spec=fuel.as_dict(),
            storage_model_spec=storage.as_dict(),defer_directory_initialization=True)
        with nonlinear_evidence_scope(store) as session,ExitStack() as capture:
            capture.enter_context(capture_scope(origin))
            env,controller,resolved=_build_runtime(case,registry,output,writer,sampling_spec=sampling_spec,
                source_admission=reference,bindings=bindings)
            _write_new(output/'SOLVER_CONTEXT.json',dict(enable_all_validation_rows=True,
                domain_evidence_directory=str(output/'domain_evidence'),writer_schema=writer.receipt()['schema'],
                local_indices_are_block_namespaced=not saved['retain_history'],
                normalization='existing normalize_json; nonfinite numbers remain null, never zero',
                source_admission=deepcopy(reference),compiled_source_origin=origin))
            support=sorted(allowed_hours(dict(scenarios=[case['scenario']])))
            parameters,tariffs,accounting_sources=_load_frozen_accounting(ROOT,bindings,[{'hour_of_year':h} for h in support])
            parameters['cold_pump_kW_per_kW']=env.sci_config.get('cold_pump_kW_per_kW')
            result['actual_episode_attempts']=1
            def record_resolved(actual):
                resolved['model_config']=deepcopy(actual.operating_model_config)
                _write_new(output/'RESOLVED_CASE.json',dict(case=case,**resolved,allowed_hours=support,
                    input_access_receipt=deepcopy(getattr(actual,'input_access_receipt',None)),
                    minute1_initialization_reference=deepcopy(getattr(actual,'minute1_initialization_reference',None))))
            execution=_execute_loop(env,controller,case['scenario'],journal,output,
                retain_history=saved['retain_history'],accounting_parameters=parameters,tariffs_by_hour=tariffs,
                on_reset=record_resolved,variant=case['minute1']['variant'],stopfile=stopfile)
            result.update(execution,configuration=case['configuration'],minute1=deepcopy(case['minute1']),
                resolved_model_config=deepcopy(env.operating_model_config),accounting_input_bindings=accounting_sources)
    except Exception as exc:
        result.update(status='failed',error=_error(exc))
        if 'summary' not in result:
            try:result['summary']=journal.close(status='failed',expected_end_minute=requested,error=result['error'])
            except Exception as second:result['journal_close_error']=_error(second)
        result.setdefault('observed_until_minute',0.);result.setdefault('unknown_tail_minutes',requested)
    finally:
        if store is not None:
            try:
                evidence=store.finish(session)
                if type(session) is not dict or session.get('status')!='closed_completed':
                    raise ValueError('Minute nonlinear core scope did not close completed')
            except Exception as exc:
                error=_error(exc)
                if result.get('error') is not None:error['native_error']=result['error']
                result.update(status='failed',error=error)
                try:evidence=store.receipt(session)
                except Exception:evidence=None
            result['sf2_nonlinear_evidence']=evidence
        if writer is not None:
            try:
                if result['status']!='completed':writer.body_exception=dict(type='MinuteRunFailed',message=str(result.get('error'))[:1000])
                writer.close();result['solver_row_sidecar']=writer.receipt()
                _write_new(output/'SOLVER_ROWS_RECEIPT.json',result['solver_row_sidecar'])
            except Exception as exc:result.update(status='failed',solver_row_evidence_error=_error(exc))
        domain=output/'domain_evidence'
        if domain.is_dir():
            result['domain_evidence']=dict(files={p.relative_to(output).as_posix():dict(sha256=sha256(p),bytes=p.stat().st_size)
                for p in sorted(domain.rglob('*')) if p.is_file()},enable_all_validation_rows=True)
        try:
            unchanged=_bindings_unchanged(bindings,extras)
            supplied=saved.get('case_input_binding')
            if supplied:unchanged=unchanged and sha256(supplied['path'])==supplied['sha256']
            verify_source_admission(ROOT,reference,source_hashes=bindings['source'])
            result['source_and_inputs_unchanged']=unchanged
            if not unchanged:result.update(status='failed',source_drift_error='Source/input/case/admission/HEAD changed during minute attempt')
        except Exception as exc:result.update(status='failed',source_drift_error=_error(exc),source_and_inputs_unchanged=False)
        result['elapsed_seconds']=time.perf_counter()-started
        _write_new(output/'RESULT.json',result)
        _write_new(output/'SUMMARY.json',{key:result.get(key) for key in ('schema','case_id','model_id','status',
            'actual_episode_attempts','git_commit','source_sha256','input_sha256','observed_until_minute','unknown_tail_minutes',
            'error','source_and_inputs_unchanged','summary','economics','sf2_nonlinear_evidence','sampling_spec',
            'physical_model_spec','storage_model_spec','controller_call_count','legal_observation_count','variant')})
    return result


def run_case(case,output,*,source_admission,physical_max_segment_minutes=1.,retain_history=False,
             case_input_binding=None,stopfile=None):
    sampling=MinuteSamplingSpec(physical_max_segment_minutes=physical_max_segment_minutes)
    validate_minute_scenario(case['scenario'])
    stopfile=Path(stopfile).resolve() if stopfile is not None else None
    case,output,bindings=_prepare_attempt(case,output,sampling_spec=sampling,source_admission=source_admission,
        retain_history=retain_history,case_input_binding=case_input_binding)
    _write_new(output/'INVOCATION.json',dict(mode='explicit_minute1_local_direct_callable',case_id=case['id'],
        runtime_builder=__name__+'._build_runtime',retain_history=retain_history,source_admission=deepcopy(source_admission),
        sampling_spec=sampling.as_dict(),stopfile=str(stopfile) if stopfile else None,
        stopfile_existence_only=True,stopfile_is_policy_input=False,annual_numeric_grant=False))
    _write_new(output/'PHYSICAL_MODEL_BINDING.json',dict(schema='hospital_ies.minute1.physical_model_binding.v1',
        physical_model_spec=FuelSpec(physical_max_segment_minutes=physical_max_segment_minutes).as_dict(),
        storage_model_spec=StorageSpec(physical_max_segment_minutes=physical_max_segment_minutes).as_dict(),
        sampling_spec=sampling.as_dict(),execution_model_id=MODEL_ID,
        scientific_source_binding=deepcopy(bindings['source']),source_admission=deepcopy(source_admission),
        old_source_byte_equivalence_claimed=False,original_canonical_parent_unchanged=True,
        control_interval_minutes=1.,load_interval_minutes=1.,command_queue_and_real_minute_delays_unchanged=True))
    with _local_temp(output):return _execute_attempt(output,sampling_spec=sampling,stopfile=stopfile)


def main(argv=None):
    import json
    parser=argparse.ArgumentParser(description='One explicitly admitted native-minute research case')
    parser.add_argument('--case-file',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--source-admission',type=Path,required=True);parser.add_argument('--source-admission-sha256',required=True)
    parser.add_argument('--physical-max-segment-minutes',type=float,choices=(1.,.5),default=1.)
    parser.add_argument('--retain-history',action='store_true');parser.add_argument('--stopfile',type=Path)
    args=parser.parse_args(argv);case_file=args.case_file.resolve()
    reference=dict(path=str(args.source_admission.resolve()),sha256=args.source_admission_sha256)
    result=run_case(_read(case_file),args.output,source_admission=reference,
        physical_max_segment_minutes=args.physical_max_segment_minutes,retain_history=args.retain_history,
        case_input_binding=dict(path=str(case_file),sha256=sha256(case_file)),stopfile=args.stopfile)
    print(json.dumps(dict(case_id=result['case_id'],status=result['status'],output=str(args.output.resolve()))))
    return 0 if result['status']=='completed' else 1


if __name__=='__main__':
    # Actual execution uses the same canonical module object that compiled
    # source attestation imports; __main__ never supplies a parallel runtime.
    from tools.minute1_20261103.runner import main as canonical_main
    raise SystemExit(canonical_main())
