"""Bounded V20 register/run; immutable raw and CSV, compact producer records."""
from __future__ import annotations

import argparse
from collections import Counter
import copy
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[2]
RUN = ROOT / 'outputs/v20_load_accounting_20261019_run01'
MODEL_ID = 'M20_load_accounting'
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
from tools.operating_response import runner as old
from .data_design import build_manifest, digest, SEALED_DAYS, RESOURCE_LIMITS, allowed_hours, runtime_input_inventory

read, sha, write, journal, matching_evidence = old.read, old.sha, old.write, old.journal, old.matching_evidence


def now():
    return datetime.now(timezone.utc).isoformat()


def model_contract(model_id, model_config):
    from .environment import model_contract as resolve
    return resolve(model_id, model_config)


def OperatingHospitalIESEnv(*args, **kwargs):
    from .environment import OperatingHospitalIESEnv as environment
    return environment(*args, **kwargs)


def run_episode(*args, **kwargs):
    from .environment import run_episode as execute
    return execute(*args, **kwargs)


def analyze_payload(*args, **kwargs):
    from .analysis import analyze_payload as analyze
    return analyze(*args, **kwargs)


def check_output_path(path):
    value = Path(path).resolve()
    if value.drive.lower() == 'c:':
        raise ValueError('Research data must use D/E, not C')
    return value


def _experiment_path(path):
    directory = check_output_path(path)
    canonical = (RUN / 'experiments').resolve()
    if directory == canonical or not directory.is_relative_to(canonical):
        raise ValueError('Registration/run must use the canonical v20 experiments directory')
    return directory


def source_binding():
    """Bind current production bytes, explicit authority and runtime registry."""
    paths=[*(ROOT/'src').rglob('*.py'),*(ROOT/'tools').rglob('*.py'),*(ROOT/'configs').glob('*.json'),
           *(ROOT/'scripts').glob('v20*.py')]
    paths += [RUN/'references/EMISSIONS_FACTOR_REGISTRY.json',
              ROOT/'outputs/v43_p2b_20261013_run01/experiment/global_manifest.json']
    paths += list(ROOT.glob('*.py'))
    if (ROOT/'pytest.ini').is_file():paths.append(ROOT/'pytest.ini')
    # Optional old P3 provenance receipts are actually inspected by inherited
    # run_episode, so bind them if present, without copying a baseline CSV.
    paths += list((ROOT/'outputs/v15_engineering_operation_20261017_run01/source_data').glob('*/P3_SOURCE_RECEIPT.json'))
    return dict(sorted((p.relative_to(ROOT).as_posix(),sha(p)) for p in paths
        if '__pycache__' not in p.parts and p.name!='bootstrap_planners.py'))


def policy_specs():
    """Same original four preferences under explicitly new M20 identities."""
    definitions = dict(
        E=('endurance_restore_p0_ready_M20_v1','endurance_restore_p0_ready_v14r6','engineering',
           dict(diagnose_full_supply=True,diagnose_hold_combination=True,mode='endurance_restore',
                grid_headroom_preference=True,execution_aware_commitment_preference=True,p0_preparation_before_commitment=True)),
        K1=('readiness_handoff_K1_M20_v1','readiness_handoff_K1_v15','operating_response',
            dict(preparation_profile='K1',phase_constraints_enabled=True,continuity=True)),
        C19=('C19_critical_reserve_M20_v1','C19_critical_reserve_v19r1','state_coordinated',
             dict(response_minutes=30.,support_minutes=60.,delay_bound_minutes=30.,cooling_preparation=True,
                  time_energy_preparation=True,execution_recovery=True,critical_reserve=True)),
        C18_M20=('C18_service_rich_M20compat_v1','C18_service_rich_M19compat','state_coordinated',
                 dict(response_minutes=30.,support_minutes=60.,delay_bound_minutes=30.,cooling_preparation=True,
                      time_energy_preparation=True,execution_recovery=True,critical_reserve=False)))
    return {alias:dict(policy_id=identity,kind=kind,config=config,display_alias=alias,
                       source_policy_id=previous,preference_config_sha256=digest(config))
            for alias,(identity,previous,kind,config) in definitions.items()}


def factory(spec):
    pool={s['policy_id']:s for s in policy_specs().values()}
    if spec.get('policy_id') not in pool or digest(spec)!=digest(pool[spec['policy_id']]):
        raise ValueError('Frozen M20 policy cannot accept a changed preference/configuration')
    from tools.engineering_model.policy import EngineeringPolicy
    from tools.operating_response.policy import OperatingPolicy
    from tools.model_closeout.policy import CoordinatedPolicy
    classes=dict(engineering=EngineeringPolicy,operating_response=OperatingPolicy,state_coordinated=CoordinatedPolicy)
    return classes[spec['kind']](copy.deepcopy(spec['config']),spec['policy_id'])


def _snapshot(binding):
    """One source copy per binding, shared by batches; never copy historical raw."""
    directory = RUN / 'source_snapshots' / digest(binding)
    for name, expected in binding.items():
        destination = directory / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            if sha(destination) != expected:
                raise ValueError('Existing immutable source snapshot differs')
        else:
            content = (ROOT / name).read_bytes()
            if hashlib.sha256(content).hexdigest() != expected:
                raise ValueError('Source drift during snapshot')
            with destination.open('xb') as stream:
                stream.write(content)
    return directory

def validate_final_freeze(frozen, binding, model_id, config, specs, *, sensitivity=None, runtime_inputs=None):
    unsigned={k:v for k,v in frozen.items() if k!='signature'}
    if (frozen.get('kind')!='v20_final_shared_model_freeze'
            or frozen.get('signature')!=digest(unsigned)):
        raise ValueError('Invalid final freeze signature or kind')
    if frozen.get('source_sha256')!=binding or frozen.get('model_id')!=model_id:
        raise ValueError('Current source/model differs from final freeze')
    if runtime_inputs is not None and frozen.get('runtime_inputs')!=runtime_inputs:
        raise ValueError('Runtime input bytes differ from M20 freeze')
    pool={s['policy_id']:s for s in frozen.get('policies',{}).values()}
    if any(s.get('policy_id') not in pool or digest(s)!=digest(pool[s['policy_id']]) for s in specs):
        raise ValueError('Requested policy differs from frozen policy')
    base=frozen.get('model_config')
    if config==base and sensitivity is None:
        return
    if not isinstance(sensitivity,dict):
        raise ValueError('Changed model configuration requires a signed sensitivity preregistration')
    reg={k:v for k,v in sensitivity.items() if k!='signature'}
    if (sensitivity.get('schema')!='hospital_ies.v20.assumption_preregistration.v1'
            or sensitivity.get('signature')!=digest(reg)
            or sensitivity.get('source_freeze_signature')!=frozen['signature']
            or sensitivity.get('source_sha256')!=binding
            or sensitivity.get('model_id')!=model_id or sensitivity.get('model_config')!=config
            or digest(sensitivity.get('policy_specs'))!=digest(specs)
            or not isinstance(sensitivity.get('reason'),str) or not sensitivity['reason'].strip()):
        raise ValueError('Invalid source-bound sensitivity preregistration')
    if {k:v for k,v in config.items() if k!='execution_recovery'}!={k:v for k,v in base.items() if k!='execution_recovery'}:
        raise ValueError('Sensitivity configuration may change only declared M20 clocks')
    clocks = {'grid_open_transfer_minutes', 'local_contact_restore_delay_minutes'}
    current, original = config.get('execution_recovery', {}), base.get('execution_recovery', {})
    changed = {k for k in set(current)|set(original) if current.get(k)!=original.get(k)}
    if not changed or not changed <= clocks:
        raise ValueError('Sensitivity contains an undeclared recovery parameter')



def freeze_file(value=None):
    """Select one immutable freeze in this run, retaining the old default."""
    candidate=Path(value) if value is not None else Path('MODEL_FREEZE.json')
    path=(candidate if candidate.is_absolute() else RUN/candidate).resolve()
    if path.parent!=RUN.resolve() or path.suffix.lower()!='.json':
        raise ValueError('Final freeze must be a direct JSON file in the current run')
    return path


def attempt_accounting():
    """Count every actual start, including failures and missing terminal records."""
    registered=sum(int(read(p)['registered_pairs']) for p in (RUN/'experiments').rglob('plan.json'))
    starts={};finishes={};times=[]
    for path in sorted((RUN/'experiments').rglob('ATTEMPTS.jsonl')):
        for line in path.read_text(encoding='utf8').splitlines():
            row=json.loads(line)
            if row.get('event') not in ('started','finished'):continue
            key=(str(path.parent),row['scenario_id'],row['policy_id'])
            target=starts if row['event']=='started' else finishes
            if key in target:raise ValueError('Duplicate immutable attempt journal event')
            target[key]=row
            if row['event']=='started':times.append(datetime.fromisoformat(row['utc']))
    if set(finishes)-set(starts):raise ValueError('Terminal attempt has no actual start record')
    first=min(times) if times else None
    return dict(registered_pairs=registered,actual_started_attempts=len(starts),
        terminal_attempts=len(finishes),started_without_terminal_record=len(set(starts)-set(finishes)),
        terminal_status_counts=dict(Counter(row.get('status','unknown') for row in finishes.values())),
        first_actual_started_utc=first.isoformat() if first else None,
        actual_experiment_wall_hours=(datetime.now(timezone.utc)-first).total_seconds()/3600. if first else 0.)


def _enforce_actual_budget():
    counts=attempt_accounting()
    if (counts['actual_started_attempts']>=RESOURCE_LIMITS['max_actual_attempts']
            or counts['actual_experiment_wall_hours']>=RESOURCE_LIMITS['max_experiment_wall_hours']):
        raise ValueError('Predeclared V20 engineering bound reached; retain failed and unknown attempts')
    return counts


def register(proposal, directory, specs, *, model_id, model_config, scenario_ids=None, phase='development', freeze_path=None):
    directory = _experiment_path(directory)
    if directory.exists():
        raise FileExistsError('Use fresh registration; previous attempts are immutable')
    if model_id != 'M20_load_accounting':
        raise ValueError('Unknown explicit V20 comparison model')
    _, resolved = model_contract(model_id, model_config)
    specs = copy.deepcopy(list(specs))
    ids = [s['policy_id'] for s in specs]
    if not specs or len(specs) > 8 or len(ids) != len(set(ids)):
        raise ValueError('Distinct finite policy panel of at most eight required')
    for spec in specs:
        factory(spec)
    proposal = copy.deepcopy(proposal)
    if proposal.get('signature') != digest({k:v for k,v in proposal.items() if k!='signature'}):
        raise ValueError('Proposal changed before registration')
    if scenario_ids is not None:
        requested = list(scenario_ids)
        if len(requested) != len(set(requested)):
            raise ValueError('Duplicate requested scenario identity')
        proposal['scenarios'] = [s for s in proposal['scenarios'] if s['id'] in set(requested)]
        if len(proposal['scenarios']) != len(requested):
            raise ValueError('Missing registered specification')
        proposal['parent_proposal_signature'] = proposal.pop('signature', None)
        proposal['signature'] = digest(proposal)
    if not proposal['scenarios']:
        raise ValueError('Empty specification panel')
    count = len(proposal['scenarios']) * len(specs)
    existing = sum(int(read(path)['registered_pairs']) for path in (RUN / 'experiments').rglob('plan.json'))
    if existing + count > int(RESOURCE_LIMITS['max_registered_pairs']):
        raise ValueError('v20 registered pair budget ceiling exceeded; keep prior evidence')
    binding = source_binding()
    freeze_reference=None
    if any(s.get('requires_policy_freeze') for s in proposal['scenarios']):
        selected_freeze=freeze_file(freeze_path)
        frozen_bytes=selected_freeze.read_bytes()
        frozen=json.loads(frozen_bytes.decode('utf8'))
        freeze_reference=dict(path=str(selected_freeze),sha256=hashlib.sha256(frozen_bytes).hexdigest())
        if proposal.get('freeze_signature')!=frozen.get('signature'):
            raise ValueError('Proposal does not bind the final freeze')
        validate_final_freeze(frozen,binding,model_id,resolved,specs,
            sensitivity=proposal.get('sensitivity_registration'), runtime_inputs=runtime_input_inventory(ROOT))
    manifest = build_manifest(proposal, binding, model_id, resolved, root=ROOT, policies=ids)
    manifest.pop('signature', None)
    if freeze_reference is not None:
        manifest['source_freeze_artifact']=freeze_reference
    manifest['policy_specs_sha256'] = digest(specs)
    manifest['signature'] = digest(manifest)
    snapshot = _snapshot(binding)
    plan = dict(schema='hospital_ies.execution_recovery_batch.v20.v1', registered_utc=now(), phase=phase,
                model_id=model_id, model_config=copy.deepcopy(resolved), policy_specs=specs,
                scenario_ids=[s['id'] for s in manifest['scenarios']], registered_pairs=count,
                source_sha256=binding, manifest_signature=manifest['signature'],
                manifest_sha256=None, source_snapshot=str(snapshot), canonical_run=str(RUN.resolve()),
                resource_limits=copy.deepcopy(RESOURCE_LIMITS), original_final12='not_verified; unopened',
                no_figures=True, no_papers=True, no_training=True)
    directory.mkdir(parents=True)
    write(directory / 'manifest.json', manifest)
    plan['manifest_sha256'] = sha(directory / 'manifest.json')
    write(directory / 'plan.json', plan)
    if freeze_reference is not None and sha(freeze_reference['path'])!=freeze_reference['sha256']:
        raise ValueError('Final freeze changed during registration; retain partial registration')
    if source_binding() != binding:
        raise ValueError('Source changed during registration; retain partial registration')
    return plan


def compact_error(value):
    if value is None:
        return None
    result = copy.deepcopy(value)
    diagnostic = result.get('diagnostic') if isinstance(result, dict) else None
    if isinstance(diagnostic, dict):
        removed = {key: diagnostic.pop(key) for key in ('completed_segments', 'events', 'command', 'scenario')
                   if key in diagnostic}
        if removed:
            result['full_details_in_raw'] = True
            result['raw_detail_fields'] = ['diagnostic.' + key for key in removed]
    return result


def compact_analysis(value):
    result = copy.deepcopy(value)
    rows = result.pop('timelines', {})
    result['timeline_row_counts'] = {key: len(items) for key, items in rows.items()}
    engineering = result.get('engineering', {})
    transitions = engineering.pop('gt_transitions', None)
    if transitions is not None:
        engineering['gt_transition_row_count'] = len(transitions)
    for state in engineering.get('gt', {}).values():
        runs = state.pop('runs', None)
        if runs is not None:
            state['run_row_count'] = len(runs)
    domain = result.get('model_domain', {})
    if domain.get('termination') is not None:
        domain['termination'] = compact_error(domain['termination'])
    stages = result.get('v18', {}).get('recovery_stages', {})
    stages['exported_row_counts'] = {key:len(stages.pop(key))
        for key in ('actual_source_intervals', 'ordinary_command_lifecycle') if key in stages}
    if stages:stages['full_rows_csv'] = 'V18_RECOVERY_STAGES.csv'
    result['storage_schema'] = 'hospital_ies.execution_recovery_compact_analysis.v20.v1'
    result['full_timelines_in_raw_and_csv'] = True
    return result


def _compact_record(record):
    result = {key: copy.deepcopy(value) for key, value in record.items() if key != 'analysis'}
    result['error'] = compact_error(result.get('error'))
    result['analysis'] = compact_analysis(record['analysis']) if record.get('analysis') is not None else None
    return result


def export_rows(records, out):
    """Retain original CSVs and append V20 carrier/energy/recovery tables."""
    from .analysis import export_rows as export_v20_rows
    return export_v20_rows(records, check_output_path(out))

def _complete(record, window):
    return bool(record.get('analysis') and record['analysis']['windows'][window]['complete'])


def save_raw(path, payload):
    """Stream JSON/gzip; retain exact JSON values without full duplicate strings."""
    import io
    with Path(path).open('xb') as raw_stream:
        with gzip.GzipFile(filename='', fileobj=raw_stream, mode='wb', mtime=0) as compressed:
            with io.TextIOWrapper(compressed, encoding='utf8', newline='') as stream:
                json.dump(payload, stream, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
    return sha(Path(path))


def _validate_registered_freeze(plan, manifest):
    if not any(s.get('requires_policy_freeze') for s in manifest['scenarios']):
        return
    reference = manifest.get('source_freeze_artifact')
    if not isinstance(reference, dict):
        raise ValueError('Final registration has no freeze artifact binding')
    path = freeze_file(reference['path'])
    if sha(path) != reference['sha256']:
        raise ValueError('Final freeze artifact changed after registration')
    frozen = read(path)
    if manifest.get('freeze_signature') != frozen.get('signature'):
        raise ValueError('Registered freeze signature differs')
    validate_final_freeze(frozen, plan['source_sha256'], plan['model_id'],
        plan['model_config'], plan['policy_specs'], sensitivity=manifest.get('sensitivity_registration'),
        runtime_inputs=runtime_input_inventory(ROOT))


def run_batch(directory):
    directory = _experiment_path(directory)
    plan, manifest = read(directory / 'plan.json'), read(directory / 'manifest.json')
    if sha(directory / 'manifest.json') != plan['manifest_sha256'] or source_binding() != plan['source_sha256']:
        raise ValueError('Changed registration or source; never mix versions')
    unsigned = {key: value for key, value in manifest.items() if key != 'signature'}
    if (manifest.get('signature') != digest(unsigned)
            or plan.get('manifest_signature') != manifest.get('signature')
            or plan.get('source_sha256') != manifest.get('source_sha256')
            or plan.get('model_id') != manifest.get('execution_model_id')
            or digest(plan.get('model_config')) != digest(manifest.get('model_config'))
            or digest(manifest.get('model_config')) != manifest.get('model_config_signature')
            or digest(plan.get('policy_specs')) != manifest.get('policy_specs_sha256')
            or plan.get('scenario_ids') != [scene['id'] for scene in manifest['scenarios']]
            or plan.get('registered_pairs') != len(manifest['scenarios']) * len(plan.get('policy_specs', []))):
        raise ValueError('Plan/manifest registration binding changed; never execute different physics or policies')
    if manifest.get('files') != runtime_input_inventory(ROOT):
        raise ValueError('Registered runtime input bytes changed')
    _, resolved = model_contract(plan['model_id'], plan['model_config'])
    if digest(resolved) != manifest['model_config_signature']:
        raise ValueError('Plan/manifest resolved model configuration binding differs')
    if any((directory / name).exists() for name in ('BATCH_RESULT.json', 'ATTEMPTS.jsonl', 'pairs')):
        raise FileExistsError('Completed/partial evidence is immutable; register a fresh attempt')
    _validate_registered_freeze(plan, manifest)
    _enforce_actual_budget()
    env = OperatingHospitalIESEnv(ROOT, allowed_hours=allowed_hours(manifest), sealed_days=SEALED_DAYS,
                                  model_id=plan['model_id'], model_config=plan['model_config'])
    records, matches = [], {}
    started = time.perf_counter()
    for scene in manifest['scenarios']:
        matching_payloads = []
        for spec in plan['policy_specs']:
            if source_binding() != plan['source_sha256']:
                raise ValueError('Source changed while a physical campaign was active')
            _validate_registered_freeze(plan, manifest)
            _enforce_actual_budget()
            pair = directory / 'pairs' / (scene['id'] + '__' + spec['policy_id'])
            pair.mkdir(parents=True, exist_ok=False)
            journal(directory / 'ATTEMPTS.jsonl', event='started', scenario_id=scene['id'],
                    model_id=env.model_id, policy_id=spec['policy_id'])
            began = time.perf_counter()
            analysis = raw_sha = raw = analysis_error = None
            try:
                payload = run_episode(env, scene, factory(spec), int(scene.get('initialization_seed', 20261017)), manifest)
            except Exception as exc:
                failure = dict(type=type(exc).__name__, message=str(exc), traceback=traceback.format_exc(),
                               phase='unhandled_run_episode', observed_until_minute=getattr(getattr(env, 'core', None), 'time', None),
                               requested_end_minute=scene['duration_steps'] * scene.get('step_minutes', 15.),
                               physical_tail='unknown; no substitute zero trajectory generated')
                write(pair / 'UNHANDLED_ATTEMPT_ERROR.json', failure)
                matching_payloads.append(dict(initial_state=None, scenario=scene, segments=[]))
                status, error = 'runner_unhandled_exception', failure
            else:
                matching_payloads.append(dict(initial_state=payload.get('initial_state'), scenario=payload['scenario'],
                    segments=[{key: segment.get(key) for key in ('start_minute', 'end_minute', 'pre_state', 'post_state')}
                              for segment in payload.get('segments', [])]))
                raw = pair / 'trajectory.json.gz'
                raw_sha = save_raw(raw, payload)
                status, error = payload['summary']['status'], payload.get('error')
                try:
                    analysis = analyze_payload(payload, env)
                except Exception as exc:
                    analysis_error = dict(type=type(exc).__name__, message=str(exc), traceback=traceback.format_exc(),
                                          raw_sha256=raw_sha, physical_status=status,
                                          observed_until_minute=payload['summary']['observed_until_minute'])
                    write(pair / 'analysis_error.json', analysis_error)
            record = dict(scenario_id=scene['id'], analysis_class=scene['analysis_class'], model_id=env.model_id,
                          policy_id=spec['policy_id'], status=status, source_day_index=scene.get('source_day_index'),
                          raw_path=str(raw) if raw else None, raw_sha256=raw_sha, elapsed_seconds=time.perf_counter() - began,
                          manifest_signature=manifest['signature'], model_config_signature=manifest['model_config_signature'],
                          error=error, analysis_error=analysis_error, analysis=analysis)
            write(pair / 'RESULT.json', _compact_record(record))
            records.append(record)
            journal(directory / 'ATTEMPTS.jsonl', event='finished', scenario_id=scene['id'], policy_id=spec['policy_id'],
                    status=status, analysis_status='saved' if analysis is not None else 'unknown_or_failed',
                    observed_until=analysis['observed_until_minute'] if analysis else None, raw_sha256=raw_sha,
                    result_sha256=sha(pair / 'RESULT.json'), elapsed_seconds=record['elapsed_seconds'])
            print(json.dumps(dict(completed=len(records), registered=plan['registered_pairs'], scenario=scene['id'],
                                  policy=spec['policy_id'], model=env.model_id, status=status,
                                  analysis_available=analysis is not None)), flush=True)
            # Long normal windows retain large solver diagnostics in raw. They
            # are not needed while constructing the next policy's episode.
            if 'payload' in locals():
                del payload
            import gc
            gc.collect()
        matches[scene['id']] = matching_evidence(matching_payloads)
    if source_binding() != plan['source_sha256']:
        raise ValueError('Source drift after final physical episode')
    analyzed = [record for record in records if record['analysis'] is not None]
    receipt = export_rows(analyzed, directory / 'analysis')
    result = dict(schema='hospital_ies.execution_recovery_batch_result.v20.v1', registered_pairs=plan['registered_pairs'],
                  actual_attempts=len(records), complete_full=sum(_complete(r, 'registered_full') for r in records),
                  unknown_full=sum(not _complete(r, 'registered_full') for r in records),
                  complete_legacy_full600=sum(_complete(r, 'full') for r in records),
                  unknown_legacy_full600=sum(not _complete(r, 'full') for r in records),
                  analysis_failure_or_unavailable_count=len(records) - len(analyzed),
                  status_counts=dict(Counter(record['status'] for record in records)),
                  elapsed_seconds=time.perf_counter() - started, source_unchanged=True, matching_evidence=matches,
                  same_initial_state_policy_matching=all(m['same_initial_state'] for m in matches.values()),
                  same_exogenous_stream_policy_matching=all(m['same_exogenous_definition'] for m in matches.values()),
                  policy_dependent_continuous_prefix_injection_states_are_not_required_equal=True,
                  plan_sha256=sha(directory / 'plan.json'), manifest_sha256=sha(directory / 'manifest.json'),
                  analysis_receipt=receipt, all_registered_outcomes_retained=True, physical_replay_certification=False,
                  completion_semantics='complete_full is entire registered [0,end); legacy full600 is separate',
                  pair_index=[{key: value for key, value in _compact_record(record).items() if key != 'analysis'} for record in records])
    result['run_attempt_accounting']=attempt_accounting()
    write(directory / 'BATCH_RESULT.json', result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    reg = sub.add_parser('register')
    reg.add_argument('--proposal', type=Path, required=True)
    reg.add_argument('--directory', type=Path, required=True)
    reg.add_argument('--policies', required=True)
    reg.add_argument('--model', choices=('M20_load_accounting',), required=True)
    reg.add_argument('--model-config', type=Path)
    reg.add_argument('--scenario-id', action='append')
    reg.add_argument('--phase', default='development')
    reg.add_argument('--freeze', type=Path)
    run = sub.add_parser('run')
    run.add_argument('--directory', type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == 'register':
        pool = policy_specs()
        result = register(read(args.proposal), args.directory, [pool[key] for key in args.policies.split(',')],
                          model_id=args.model, model_config=read(args.model_config) if args.model_config else {},
                          scenario_ids=args.scenario_id, phase=args.phase, freeze_path=args.freeze)
        print(json.dumps({key: result[key] for key in ('registered_pairs', 'model_id', 'scenario_ids')}, ensure_ascii=False))
    else:
        result = run_batch(args.directory)
        print(json.dumps({key: result[key] for key in ('registered_pairs', 'actual_attempts', 'complete_full',
                                                      'unknown_full', 'status_counts')}, ensure_ascii=False))
    return 0 if args.command == 'register' or (result['unknown_full'] == 0 and result['analysis_failure_or_unavailable_count'] == 0) else 2


if __name__ == '__main__':
    raise SystemExit(main())
