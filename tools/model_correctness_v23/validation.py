"""Failure-preserving one-case M23 entry point; no experiment product grid."""
from __future__ import annotations
import argparse
import copy
import csv
import gzip
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback

ROOT=Path(__file__).resolve().parents[2]
CASES=ROOT/'configs/model_correctness_v23/cases.json'


def checked_output(value):
    path=Path(value).resolve()
    if path.exists():raise FileExistsError('Each attempt requires a fresh output directory: '+str(path))
    if path==ROOT or path in ROOT.parents:raise ValueError('Output cannot contain the source tree')
    for protected in ('outputs','inputs','data','src','tools','configs','docs','tests'):
        if path.is_relative_to(ROOT/protected):raise ValueError('Output cannot modify protected project inputs/source')
    return path


def recovery_references(payload):
    """Evaluator-only facts, derived after execution, never fed to a controller.

    Actual power restoration is measured from non-UPS delivered source power.
    The boiler/GT-start branch only qualifies the heat-source reference. The
    external clock is the last observed exogenous clearing, including repeats.
    """
    scene=payload.get('scenario',{})
    segs=payload.get('segments',[])
    observed=max((float(s['end_minute']) for s in segs),default=0.)
    clear=[];pending=False;occurred=False
    def numbers(value):
        if isinstance(value,dict):return [n for v in value.values() for n in numbers(v)]
        if isinstance(value,(list,tuple)):return [n for v in value for n in numbers(v)]
        return [float(value)] if isinstance(value,(float,int)) and not isinstance(value,bool) else []
    fault_values=numbers(scene.get('faults',{}))+numbers(scene.get('auxiliary_faults',{}))
    shock_values=sum((numbers(scene.get(key,{})) for key in ('shock','shock_by_priority','shock_by_zone')),[])
    for prefix,active in [('fault',any(v<1.-1e-9 for v in fault_values)),('shock',any(abs(v)>1e-9 for v in shock_values))]:
        start=float(scene.get(prefix+'_start_minute',0.))
        if active and start<observed-1e-9:
            occurred=True;end=start+float(scene.get(prefix+'_duration_minutes',0.))
            if 0.<end<=observed+1e-9:clear.append(end)
            else:pending=True
    previous={}
    for event in sorted(scene.get('model_closure_events',[]),key=lambda e:e['time_minute']):
        minute=float(event['time_minute'])
        if minute>observed+1e-9:continue
        for key,value in event.get('availability',{}).items():
            if float(value)<1.-1e-9:occurred=True
            if previous.get(key,1.)<1.-1e-9 and float(value)>=1.-1e-9:clear.append(minute)
            previous[key]=float(value)
    pending=pending or any(v<1.-1e-9 for v in previous.values())
    restoration={}
    source_events={}
    for carrier in 'ehc':
        was=True;interrupted=False;returns=[]
        for s in segs:
            action=s.get('action',{})
            power=float(action.get('grid_import_kW',0.))+float(action.get('diesel_kW',0.))+sum(action.get('gt_electric_kW',[]))
            available=power>1e-6
            if carrier=='h':
                hardware=s.get('hardware_availability',s.get('availability',{}))
                available=available and float(hardware.get('STATION_AC',hardware.get('BOILER_GT_START_AC',1.)))>=1.-1e-9
            if not available:interrupted=True
            if available and not was:returns.append(float(s['start_minute']))
            was=available
        restoration[carrier]=(returns[-1] if returns and was else None) if interrupted else 'not_applicable'
        source_events[carrier]=dict(interrupted=interrupted,actual_restore_minutes=returns,
            source_restoration_observed=bool(returns and was),last_observed_source_available=was)
    external=None if pending else max(clear) if clear else 'not_applicable'
    return dict(external_fault_clear_minute=external,
        actual_source_restore_minute=restoration),dict(reference_information_is_offline_only=True,
        observed_until_minute=observed,external_observed_clear_minutes=clear,actual_non_UPS_source_facts=source_events,
        external_clear_status='not_observed' if pending else 'observed' if occurred else 'not_applicable',
        branch_scope='STATION_AC means boiler/GT-start AC; EC and tower use the actual common non-UPS source bus')


def _inventory(env,payload):
    core=env.core
    result={}
    for key,attr in [('ups','ups'),('cold_store','cold_store')]:
        store=getattr(core,attr)
        segments=payload.get('segments',[])
        initial=(segments[0].get('m21_storage',{}).get(key,{}) if segments else {}).get('initial_kWh')
        source='first_actual_inventory_receipt'
        if initial is None:
            initial=store.config['capacity_kWh']*store.config['initial_soc'];source='registered_reset_capacity_times_SOC'
        result[key]=dict(initial_kWh=initial,initial_source=source,scale_kWh=store.config['capacity_kWh'],final_kWh=store.energy_kWh,
            initial_paid_input_kWh=payload.get('m21_initial_storage',{}).get(key))
    scene=payload['scenario'];segments=payload.get('segments',[])
    declared=scene.get('initial_shaft_budget_kWh_equivalent',scene.get('initial_reserve_kWh'))
    initial=(segments[0].get('pre_state',{}) if segments and abs(float(segments[0].get('start_minute',0.)))<1e-7 else {}).get('diesel_energy_remaining_kWh')
    source='first_actual_pre_state'
    if initial is None:
        initial=(payload.get('initial_state') or {}).get('state',{}).get('diesel_energy_remaining_kWh');source='recorded_reset_state'
    if initial is None:initial=declared;source='explicit_scenario_budget' if declared is not None else 'missing'
    for value in (initial,declared):
        if value is not None and (isinstance(value,bool) or not math.isfinite(float(value)) or float(value)<0.):
            raise ValueError('Invalid recorded or declared DG budget')
    if initial is not None and declared is not None and abs(float(initial)-float(declared))>1e-7:
        raise ValueError('Recorded initial DG budget conflicts with explicit scenario budget')
    initial=float(initial) if initial is not None else None
    result['DG_shaft_budget']=dict(initial_kWh=initial,initial_source=source,scale_kWh=initial,
        final_kWh=float(core.state.diesel_energy_remaining_kWh))
    return result


def actual_process(payload,path):
    from tools.model_closure_v22.validation import native_process,sha
    path=Path(path)
    base=path.with_name('BASE_PROCESS.csv')
    receipt=native_process(payload,base)
    if receipt is None:return None
    with base.open(encoding='utf-8-sig',newline='') as stream:rows=list(csv.DictReader(stream))
    for row,segment in zip(rows,payload['segments']):
        for key in sorted(segment):
            if key.startswith('m23_') or key in ('hardware_availability','execution_limits'):
                row[key+'_json']=json.dumps(segment[key],ensure_ascii=False,separators=(',',':'))
    with path.open('x',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(dict.fromkeys(k for row in rows for k in row)))
        writer.writeheader();writer.writerows(rows)
    return dict(receipt,path=str(path),sha256=sha(path),includes_actual_M23_restart_event_domain_and_temperature_receipts=True)


def write_trajectory(payload,path):
    """Bounded JSON encoding; never materialize a second full RAW string/bytes."""
    encoder=json.JSONEncoder(ensure_ascii=False,allow_nan=False,separators=(',',':'))
    with Path(path).open('xb') as binary:
        with gzip.GzipFile(fileobj=binary,mode='wb',mtime=0) as zipped:
            pieces=[];size=0
            for piece in encoder.iterencode(payload):
                pieces.append(piece);size+=len(piece)
                if size>=65536:
                    zipped.write(''.join(pieces).encode('utf8'));pieces.clear();size=0
            if pieces:zipped.write(''.join(pieces).encode('utf8'))


def record_actual_prefix(segment,stream):
    """Write already committed native data, excluding repeated solver matrices."""
    keys=('start_minute','end_minute','step','hour_of_year','command_id','execution_mode','availability',
        'hardware_availability','execution_limits','execution_model_id','pre_state','post_state','action',
        'loads_kW','connected_loads_kW','disconnected_loads_kW','unserved_kW','service_request_ledger',
        'cost_USD_step','energy_balance_residuals','steam_balance_residual_lbph','m21_storage',
        'service_dependencies','m22_station','m23_restoration','m23_event_closure','m23_temperature_diagnostic')
    stream.write(json.dumps({k:segment[k] for k in keys if k in segment},ensure_ascii=False,allow_nan=False,separators=(',',':'))+'\n')
    stream.flush()


def worker(output):
    sys.dont_write_bytecode=True
    sys.path[:0]=[str(ROOT/'src'),str(ROOT)]
    from tools.model_closure_v22.validation import read,write,sha,digest,now,native_process,recover_execution_prefix,validate_exporter_metadata,runtime_policy
    from tools.model_correctness_v23 import environment,policy,MODEL_ID,CONTRACT_ID
    from tools.model_correctness_v23.metrics import build_reliability_metrics
    from tools.model_correctness_v23.comparison import with_comparison_inputs
    from tools.sci_engineering.runner import compact_metrics,input_binding,source_binding
    from tools.sci_engineering.design import allowed_hours,SEALED_DAYS
    output=Path(output).resolve()
    if not (output/'INVOCATION.json').is_file() or (output/'RESULT.json').exists():raise ValueError('Worker needs its own new invocation')
    registration=read(output/'CASE.json');registry=registration['registry'];case=registration['case'];scene=copy.deepcopy(case['scenario'])
    requested=float(scene['duration_steps'])*float(scene.get('step_minutes',15.))
    began=time.perf_counter();env=payload=bindings=rows_writer=prefix_stream=None
    result=dict(schema='hospital_ies.model_correctness_validation.v23.v1',case_id=case['id'],model_id=MODEL_ID,
        status='started',started_utc=now(),actual_episode_attempts=0,requested_end_minute=requested,
        observed_until_minute=None,unknown_tail_minutes=requested,metrics=None,error=None,
        evaluation_role=case.get('evaluation_role','seen_regression'),independent_validation=False,
        temperature_scope='Exploratory; original precision gate failed; cannot cancel service ENS or establish clinical safety')
    write(output/'STATUS.json',result)
    try:
        validate_exporter_metadata(scene)
        schema_scene=copy.deepcopy(scene);schema_scene.pop('model_closure_events',None)
        environment.validate_scenario(schema_scene);environment.validate_events(scene.get('model_closure_events',[]))
        bindings=dict(source=source_binding(ROOT),inputs=input_binding(ROOT))
        result.update(source_sha256=digest(bindings['source']),input_sha256=bindings['inputs'],case_sha256=digest(case))
        write(output/'BINDINGS.json',bindings)
        git=subprocess.run(['git','rev-parse','HEAD'],cwd=ROOT,text=True,capture_output=True,check=False)
        result['git_commit']=git.stdout.strip() if git.returncode==0 else None
        config=copy.deepcopy(registry['hardware_profiles'][case['configuration']])
        config['model_closure_v22']=copy.deepcopy(case.get('model_closure_v22',registry['model_closure_v22']))
        config['model_correctness_v23']=copy.deepcopy(case.get('model_correctness_v23',registry['model_correctness_v23']))
        result['extra_input_bindings']={}
        thermal=config['model_correctness_v23'].get('thermal_coupling')
        if thermal:
            for key in ('reference_csv','parameters_json'):
                path=Path(thermal[key]);path=path if path.is_absolute() else ROOT/path
                result['extra_input_bindings'][key]=dict(path=str(path),sha256=sha(path))
        from tools.sci_engineering.solver_log import SolverRowSidecar
        rows_writer=SolverRowSidecar(output/'solver_rows.jsonl.gz')
        env=environment.OperatingHospitalIESEnv(ROOT,allowed_hours=allowed_hours(dict(scenarios=[scene])),sealed_days=SEALED_DAYS,
            model_id=MODEL_ID,model_config=config,solver_row_sidecar=rows_writer,
            solver_evidence_directory=output/'domain_evidence',enable_all_validation_rows=True)
        prefix_stream=(output/'ACTUAL_PREFIX.jsonl').open('x',encoding='utf8')
        env.actual_segment_observer=lambda segment:record_actual_prefix(segment,prefix_stream)
        result['model_config']=config;result['resolved_model_config']=copy.deepcopy(env.operating_model_config)
        result['model_config_sha256']=digest(result['resolved_model_config'])
        spec=runtime_policy(case,registry,policy)
        controller=policy.SciPolicy(spec['alias'],config=spec['config'],plant=env.plant)
        result['policy']=dict(alias=spec['alias'],policy_id=controller.policy_id if hasattr(controller,'policy_id') else spec['alias']+'_model_correctness_v23',config=controller.config)
        result['scenario_sha256']=digest(scene);result['configuration']=case['configuration']
        result['input_access_receipt']=copy.deepcopy(getattr(env,'input_access_receipt',None))
        manifest=dict(execution_contract_id=CONTRACT_ID,assumption_set_id=CONTRACT_ID,engineering_model_contract_id=CONTRACT_ID,
            study_contract_id=CONTRACT_ID,model_config_signature=result['model_config_sha256'],configuration_id=case['configuration'],
            execution_model_id=MODEL_ID,simulator_hash=result['source_sha256'],schemas=copy.deepcopy(registry['schemas']),
            splits={scene['id']:dict(group_id=scene['family_id'],split='development',fit_allowed=False,embargo_minutes=60.,grouping_basis='full original source support')})
        manifest['schemas']['model_correctness_v23']=CONTRACT_ID;manifest['signature']=digest(manifest)
        result['manifest']=manifest
        write(output/'RESOLVED_CASE.json',dict(case=case,model_config=result['resolved_model_config'],policy=result['policy'],manifest=manifest))
        result['actual_episode_attempts']=1;write(output/'STATUS.json',result)
        payload=environment.run_episode(env,scene,controller,int(scene.get('initialization_seed',20261023)),manifest)
        factor=registry['emissions_factor_registry'];factor_path=ROOT/factor['path']
        factors=read(factor_path) if factor_path.is_file() and sha(factor_path)==factor['sha256'] else None
        result['emissions_factor_binding']=dict(path=str(factor_path),sha256=sha(factor_path) if factor_path.is_file() else None,registered_match=factors is not None)
        result['metrics']=compact_metrics(payload,env=env,factor_registry=factors)
        result['error']=copy.deepcopy(payload.get('error'))
        result['status']='completed' if payload.get('summary',{}).get('status')=='completed' and result['metrics']['complete'] and result['error'] is None else 'failed_unknown'
    except Exception as exc:
        result.update(status='failed_unknown',error=dict(type=type(exc).__name__,message=str(exc),traceback=traceback.format_exc()))
        if payload is None and env is not None:payload=recover_execution_prefix(env,scene,MODEL_ID,result['error'])
        if payload and payload.get('segments'):
            try:result['metrics']=compact_metrics(payload,env=env,factor_registry=None)
            except Exception as second:result['prefix_metrics_error']=str(second)
    finally:
        if prefix_stream is not None:
            prefix_stream.close()
            prefix_path=output/'ACTUAL_PREFIX.jsonl'
            result['native_prefix']=dict(path=str(prefix_path),sha256=sha(prefix_path),bytes=prefix_path.stat().st_size,
                semantics='Committed native intervals recorded after each original advance; no solver matrices or reconstructed zero values')
        if rows_writer is not None:
            try:
                if result['status']!='completed':rows_writer.body_exception=dict(type='EpisodeNotCompleted',message=str(result.get('error'))[:1000])
                rows_writer.close();result['solver_row_sidecar']=rows_writer.receipt()
            except Exception as exc:result.update(status='failed_unknown',solver_row_evidence_error=dict(type=type(exc).__name__,message=str(exc)))
        evidence_dir=output/'domain_evidence'
        if evidence_dir.is_dir():
            result['domain_evidence']=dict(schema='hospital_ies.domain_evidence_manifest.v23.v1',
                files={p.relative_to(output).as_posix():dict(sha256=sha(p),bytes=p.stat().st_size) for p in sorted(evidence_dir.rglob('*')) if p.is_file()})
        if payload is not None:
            try:
                raw=output/'trajectory.json.gz'
                write_trajectory(payload,raw)
                result.update(raw_path=str(raw),raw_sha256=sha(raw),raw_bytes=raw.stat().st_size)
                result['process']=actual_process(payload,output/'PROCESS.csv')
                result['recovery_references'],result['recovery_reference_facts']=recovery_references(payload)
                result['reliability_v23']=build_reliability_metrics(payload,references=result['recovery_references'])
                if result['metrics'] and env is not None:
                    result['inventories']=_inventory(env,payload)
                    result['comparison_inputs']=with_comparison_inputs(result['metrics'],result['reliability_v23'],result['inventories'])
                    result['observed_until_minute']=result['metrics']['observed_until_minute']
                    result['unknown_tail_minutes']=max(0.,requested-result['observed_until_minute'])
            except Exception as exc:
                result.update(status='failed_unknown',evidence_error=dict(type=type(exc).__name__,message=str(exc),traceback=traceback.format_exc()))
        if bindings is not None:
            unchanged=bindings==dict(source=source_binding(ROOT),inputs=input_binding(ROOT))
            for binding in result.get('extra_input_bindings',{}).values():unchanged=unchanged and sha(binding['path'])==binding['sha256']
            result['source_and_inputs_unchanged']=unchanged
            if not unchanged:result.update(status='failed_unknown',source_drift_error='Source or input bytes changed during this attempt')
        result.update(completed_utc=now(),elapsed_seconds=time.perf_counter()-began)
        write(output/'RESULT.json',result)
        write(output/'STATUS.json',{key:result[key] for key in ('case_id','status','actual_episode_attempts','observed_until_minute','unknown_tail_minutes')})
    print(json.dumps({key:result[key] for key in ('case_id','status','actual_episode_attempts','unknown_tail_minutes')}))
    return 0 if result['status']=='completed' else 1


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case');parser.add_argument('--list',action='store_true');parser.add_argument('--case-file',type=Path)
    parser.add_argument('--policy',choices=list('RMFCA'));parser.add_argument('--configuration')
    parser.add_argument('--output',type=Path);parser.add_argument('--_worker',action='store_true',help=argparse.SUPPRESS)
    args=parser.parse_args(argv)
    if args._worker:return worker(args.output)
    sys.path[:0]=[str(ROOT),str(ROOT/'src')]
    from tools.model_closure_v22.validation import read,write,sha,now,selected_case,validate_exporter_metadata
    registry=read(CASES)
    if args.list:
        for case in registry['cases']:print(case['id']+'\t'+case['purpose'])
        return 0
    if not args.output or bool(args.case)==bool(args.case_file):parser.error('Use exactly one --case/--case-file and a fresh --output')
    case=read(args.case_file) if args.case_file else selected_case(registry,args.case)
    if not {'id','scenario','configuration','policy'}<=set(case):raise ValueError('Complete registered case required')
    if args.policy:case['policy_override_alias']=args.policy
    if args.configuration:case['configuration']=args.configuration
    if case['configuration'] not in registry['hardware_profiles']:raise ValueError('Unregistered hardware profile')
    if 'model_config' in case or 'hardware_profiles' in case:raise ValueError('No arbitrary physical capacity override')
    validate_exporter_metadata(case['scenario'])
    output=checked_output(args.output);output.mkdir(parents=True,exist_ok=False)
    write(output/'CASE.json',dict(registry={k:v for k,v in registry.items() if k!='cases'},case=case,
        case_input_binding=dict(path=str(args.case_file or CASES),sha256=sha(args.case_file or CASES))))
    command=[sys.executable,'-I',str(Path(__file__).resolve()),'--_worker','--output',str(output)]
    write(output/'INVOCATION.json',dict(command=command,cwd=str(ROOT),started_utc=now(),runner_script_sha256=sha(Path(__file__)),registry_sha256=sha(CASES)))
    (output/'temp').mkdir()
    child_env=dict(os.environ,TEMP=str(output/'temp'),TMP=str(output/'temp'),PYTHONDONTWRITEBYTECODE='1',PYTHONIOENCODING='utf-8')
    with (output/'stdout.log').open('x',encoding='utf8') as stdout,(output/'stderr.log').open('x',encoding='utf8') as stderr:
        done=subprocess.run(command,cwd=ROOT,env=child_env,stdout=stdout,stderr=stderr,check=False)
    invocation=read(output/'INVOCATION.json');invocation.update(finished_utc=now(),exit_code=done.returncode);write(output/'INVOCATION.json',invocation)
    result=read(output/'RESULT.json') if (output/'RESULT.json').is_file() else dict(status='failed_unknown',actual_episode_attempts=None)
    print(json.dumps(dict(case_id=case['id'],status=result['status'],actual_episode_attempts=result['actual_episode_attempts'],output=str(output),exit_code=done.returncode)))
    return done.returncode

if __name__=='__main__':raise SystemExit(main())
