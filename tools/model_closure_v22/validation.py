"""One-case, failure-preserving V22 validation; scientific imports stay in a child.

Examples::
    python -m tools.model_closure_v22.validation --list
    python -m tools.model_closure_v22.validation --case boundary_067_600 --output E:/hospital_ies_v22_20261025/runs/067_new
    python -m tools.model_closure_v22.validation --case boundary_067_600 --legacy-root D:/hospital_ies_v21_20261023 --output E:/hospital_ies_v22_20261025/runs/067_legacy_new
    python -m tools.model_closure_v22.validation --case-file E:/hospital_ies_v22_20261025/recipes/winter.json --policy A --configuration H1 --output E:/hospital_ies_v22_20261025/runs/winter_A_H1_new
"""
from __future__ import annotations

import argparse
import copy
import csv
from datetime import datetime, timezone
import gzip
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[2]
CASES = ROOT / 'configs/model_closure_v22/cases.json'
OUTPUT_ROOT = Path('E:/hospital_ies_v22_20261025/runs')
RECIPES_ROOT = Path('E:/hospital_ies_v22_20261025/recipes')
REPO_RECIPES_ROOT = ROOT / 'configs/model_closure_v22/recipes'


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
        ensure_ascii=False, allow_nan=False).encode('utf-8')).hexdigest()


def sha(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def write(path, value):
    # Atomic replacement applies only inside this invocation's new E directory.
    path = Path(path)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    os.replace(temporary, path)


def selected_case(registry, identifier):
    found = [row for row in registry['cases'] if row['id'] == identifier]
    if len(found) != 1:
        raise ValueError('Unknown or duplicated case: ' + identifier)
    return copy.deepcopy(found[0])


def validate_exporter_metadata(scene):
    """Check real legacy exporter consumers before constructing a physical env.

    Native event schema validation does not require these provenance fields.
    They must be explicitly registered, never invented after an executed prefix.
    """
    required = ('id', 'family_id', 'parent_template_id', 'base_state_id',
        'exogenous_stream_id', 'distribution_id', 'design_family', 'sampling_cohort',
        'sampling_method', 'start_hour', 'duration_steps', 'stage', 'event_class')
    missing = [key for key in required if key not in scene]
    if missing:
        raise ValueError('Missing registered exporter metadata: ' + ', '.join(missing))


def load_case_file(value, registry):
    path = Path(value).resolve()
    allowed = (RECIPES_ROOT.resolve(), REPO_RECIPES_ROOT.resolve())
    if not any(path.is_relative_to(directory) for directory in allowed) or path.suffix.lower() != '.json':
        raise ValueError('External case must be a JSON file inside ' + ' or '.join(map(str, allowed)))
    case = read(path)
    if not isinstance(case, dict) or not {'id', 'scenario', 'configuration', 'policy'} <= set(case):
        raise ValueError('External case must contain id, complete scenario, registered configuration and policy')
    if case['configuration'] not in registry['hardware_profiles']:
        raise ValueError('External case must reference registered hardware; arbitrary capacity overrides are forbidden')
    if 'registry' in case or 'hardware_profiles' in case or 'model_config' in case:
        raise ValueError('External case cannot replace registered hardware or registry metadata')
    validate_exporter_metadata(case['scenario'])
    if 'model_closure_v22' in case:
        actual, registered = case['model_closure_v22'], registry['model_closure_v22']
        if not isinstance(actual, dict) or set(actual) != set(registered):
            raise ValueError('Attached model_closure_v22 must be an explicit complete closure configuration')
        for key, value in registered.items():
            if isinstance(value, dict) and (not isinstance(actual[key], dict) or set(actual[key]) != set(value)):
                raise ValueError('Attached model_closure_v22.' + key + ' must contain its complete registered field set')
    return copy.deepcopy(case), dict(path=str(path), sha256=sha(path), external_case_read_only=True)


def runtime_policy(case, registry, policy_module):
    original = case['policy']
    alias = case.get('policy_override_alias', original['alias'])
    if alias == original['alias']:
        return dict(alias=alias, config=copy.deepcopy(original['config']), selection_basis='original_case_config_preserved')
    specs = policy_module.policy_specs()
    if alias not in specs:
        raise ValueError('Runtime has no registered policy specification: ' + alias)
    config = copy.deepcopy(specs[alias]['config'])
    config.update(copy.deepcopy(registry.get('selected_policy_parameters', {}).get(alias, {})))
    return dict(alias=alias, config=config, selection_basis='runtime_policy_specs_and_frozen_development_parameters')


def checked_output(value):
    output = Path(value).resolve()
    if output == OUTPUT_ROOT.resolve() or not output.is_relative_to(OUTPUT_ROOT.resolve()):
        raise ValueError('Output must be a fresh child of ' + str(OUTPUT_ROOT))
    if output.exists():
        raise FileExistsError('Output already exists; each actual attempt requires a fresh directory')
    return output


def native_process(payload, path):
    """Keep original half-open intervals and both shortage kinds, including prefixes."""
    rows = []
    previous = 0.
    for segment in payload.get('segments', []):
        start, end = float(segment['start_minute']), float(segment['end_minute'])
        if not math.isfinite(start + end) or end <= start or abs(start - previous) > 1e-7:
            raise ValueError('Actual intervals overlap or contain an unknown gap')
        previous = end
        ledger = segment.get('service_request_ledger')
        if not isinstance(ledger, dict):
            raise ValueError('Missing service ledger; no zero replacement is allowed')
        keys = ('original_demand_kW', 'delivered_kW', 'active_cut_kW', 'connected_unserved_kW')
        arrays = [ledger.get(key) for key in keys]
        if any(not isinstance(a, list) or len(a) != 4 or any(len(row) != 3 for row in a) for a in arrays):
            raise ValueError('Actual service matrices must be 4 priorities by 3 carriers')
        row = dict(start_minute=start, end_minute=end, command_id=segment.get('command_id'),
            grid_bus_state=segment.get('grid_bus_state'), execution_mode=segment.get('execution_mode'))
        for group in range(4):
            for carrier, name in enumerate(('e', 'h', 'c')):
                request, delivered, cut, unmet = [float(a[group][carrier]) for a in arrays]
                values = (request, delivered, cut, unmet)
                if not all(math.isfinite(v) and v >= -1e-8 for v in values) or abs(request-delivered-cut-unmet) > 1e-7:
                    raise ValueError('Actual service ledger does not close')
                for label, value in zip(('requested', 'delivered', 'disconnected', 'connected_unserved'), values):
                    row[f'P{group}_{name}_{label}_kW'] = value
                row[f'P{group}_{name}_unserved_kW'] = cut + unmet
        for key, value in segment.get('action', {}).items():
            if isinstance(value, list):
                for index, item in enumerate(value):
                    if isinstance(item, (int, float)):
                        row[f'{key}_{index+1}'] = float(item)
            elif isinstance(value, (int, float)):
                row[key] = float(value)
        for name, receipt in segment.get('m21_storage', {}).items():
            for key, value in receipt.items():
                if isinstance(value, (int, float)) and 'kWh' in key:
                    row[name + '_' + key] = float(value)
        for side in ('pre_state', 'post_state'):
            for key in ('diesel_status', 'diesel_energy_remaining_kWh', 'last_diesel_kW'):
                row[side + '_' + key] = segment.get(side, {}).get(key)
        permissions = segment.get('arrived_source_permissions', {})
        row['arrived_diesel_ceiling_kW'] = permissions.get('diesel_kW')
        row['actual_segment_cost_USD'] = segment.get('cost_USD_step')
        retained = {'diesel_local_response', 'service_dependencies', 'station_supply'}
        retained.update(key for key in segment if key.startswith('m22_'))
        for key in sorted(retained):
            if key in segment:
                row[key + '_json'] = json.dumps(segment[key], ensure_ascii=False, separators=(',', ':'))
        rows.append(row)
    if not rows:
        return None
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with Path(path).open('x', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return dict(path=str(path), sha256=sha(path), native_intervals=len(rows), resampled=False,
        observed_until_minute=previous, failed_prefix_permitted=True)


def recover_execution_prefix(env, scene, model_id, error):
    """Recover recorded execution only when an episode wrapper lost its return value.

    This cannot recover exporter snapshots/labels or the initial physical state.
    It never retries a step, invents an interval, or marks the episode complete.
    """
    core = getattr(env, 'core', None)
    if core is None:
        return None
    segments = copy.deepcopy(getattr(core, 'segments', getattr(env, 'segment_history', [])))
    events = copy.deepcopy(getattr(core, 'events', getattr(env, 'event_history', [])))
    if not segments and not events:
        return None
    observed = 0.
    for segment in segments:
        start, end = segment.get('start_minute'), segment.get('end_minute')
        if (not isinstance(start, (int, float)) or not isinstance(end, (int, float))
                or not math.isfinite(start+end) or end <= start or abs(start-observed) > 1e-7):
            break
        observed = float(end)
    inventory = {}
    for name in ('ups', 'cold_store'):
        store = getattr(core, name, None)
        value = getattr(store, 'initial_charged_input_kWh', None)
        if isinstance(value, (int, float)):
            inventory[name] = float(value)
    requested = float(scene['duration_steps']) * float(scene.get('step_minutes', 15.))
    return dict(schema='hospital_ies.recovered_execution_prefix.v22.v1', execution_model_id=model_id,
        scenario=copy.deepcopy(scene), initial_state=None, segments=segments, events=events,
        decisions=[], zones=copy.deepcopy(getattr(env, 'zone_history', [])),
        execution_rows=copy.deepcopy(getattr(env, 'history', [])),
        solver_calls=copy.deepcopy(getattr(core, 'solver_calls', [])),
        solver_errors=copy.deepcopy(getattr(core, 'errors', [])), m21_initial_storage=inventory or None,
        error=copy.deepcopy(error), recovered_prefix=True,
        recovery_semantics='Recorded env.core segments/events only; raw core event IDs retained; exporter decisions, labels and initial state unavailable',
        unavailable_fields=['exporter_decisions', 'exporter_labels', 'initial_physical_state'],
        summary=dict(status='failed_unknown_recovered_prefix', completed=False,
            full_trajectory_outcome_valid=False, observed_until_minute=observed,
            requested_end_minute=requested, outcome_unknown_after_minute=observed))


def worker(case_path, runtime_root, output, legacy):
    """Fresh interpreter imports exactly one source root; never reset legacy files."""
    runtime_root, output = Path(runtime_root).resolve(), Path(output).resolve()
    if (not output.is_relative_to(OUTPUT_ROOT.resolve()) or output == OUTPUT_ROOT.resolve()
            or Path(case_path).resolve() != output / 'CASE.json'
            or not (output / 'INVOCATION.json').is_file() or (output / 'RESULT.json').exists()):
        raise ValueError('Worker requires its own fresh registered E output directory')
    sys.dont_write_bytecode = True
    sys.path[:0] = [str(runtime_root / 'src'), str(runtime_root)]
    registry, case = read(case_path)['registry'], read(case_path)['case']
    registration = read(case_path)
    original_case = registration.get('original_case', copy.deepcopy(case))
    invocation = read(output / 'INVOCATION.json')
    scene = copy.deepcopy(case['scenario'])
    requested = float(scene['duration_steps']) * float(scene.get('step_minutes', 15.))
    began = time.perf_counter()
    result = dict(schema='hospital_ies.model_closure_validation.v22.v1', case_id=case['id'],
        status='started', started_utc=now(), completed_utc=None, runtime_root=str(runtime_root),
        legacy_runtime=legacy, actual_episode_attempts=0, historical_attempts_added=0,
        evaluation_role='seen_development_or_regression', independent_validation=False,
        requested_end_minute=requested, observed_until_minute=None, unknown_tail_minutes=requested,
        metrics=None, error=None, raw_path=None, raw_sha256=None, process=None,
        temperature_scope='Exploratory diagnostic; cannot cancel cold ENS or select critical control')
    result['entrypoint_binding'] = {key:invocation.get(key) for key in (
        'runner_script_sha256', 'cases_registry_sha256', 'case_input_binding')}
    write(output / 'STATUS.json', result)
    env = payload = bindings = None
    try:
        if legacy and scene.get('model_closure_events'):
            raise ValueError('Legacy M21 does not support model_closure_events; use an original single-event case')
        if legacy and 'model_closure_v22' in case:
            raise ValueError('Legacy M21 cannot accept attached model_closure_v22 parameters; use a compatible ordinary case')
        if case.get('requires_event_adapter'):
            raise ValueError(case.get('not_executable_reason', 'Required causal event adapter is not bound'))
        validate_exporter_metadata(scene)
        package_name = 'tools.sci_engineering' if legacy else 'tools.model_closure_v22'
        package = importlib.import_module(package_name)
        environment = importlib.import_module(package_name + '.environment')
        policy_module = importlib.import_module(package_name + '.policy')
        from tools.sci_engineering.design import allowed_hours
        from tools.sci_engineering.runner import compact_metrics, input_binding, source_binding
        # Only compact digests are persisted; no freeze, budget or solver-matrix framework.
        bindings = dict(source=source_binding(runtime_root), inputs=input_binding(runtime_root))
        result.update(source_sha256=digest(bindings['source']), input_sha256=bindings['inputs'],
            case_sha256=digest(case), registry_sha256=digest(registry))
        git = subprocess.run(['git', '-C', str(runtime_root), 'rev-parse', 'HEAD'], capture_output=True, text=True, check=False)
        result['git_commit'] = git.stdout.strip() if git.returncode == 0 else None
        if case['configuration'] not in registry['hardware_profiles']:
            raise ValueError('Unknown registered hardware configuration')
        config = copy.deepcopy(registry['hardware_profiles'][case['configuration']])
        if not legacy:
            config['model_closure_v22'] = copy.deepcopy(case.get('model_closure_v22', registry.get('model_closure_v22', {})))
        result['model_config'] = config
        result['model_config_sha256'] = digest(config)
        result['model_id'] = package.MODEL_ID
        support = allowed_hours(dict(scenarios=[scene]))
        result['allowed_hours'] = sorted(support)
        # Native validation must run before an episode/reset attempt is counted.
        # A later wrapper error must not conceal an invalid shortened case.
        schema_scene = copy.deepcopy(scene)
        schema_scene.pop('model_closure_events', None)
        validator = getattr(environment, 'validate_scenario', None)
        if validator is None:
            validator = getattr(getattr(environment, 'legacy', None), 'validate_scenario', None)
        if validator is None:
            raise RuntimeError('Selected runtime does not expose its native scenario validator')
        validator(schema_scene)
        if scene.get('model_closure_events'):
            environment.validate_events(scene['model_closure_events'])
        result['scenario_preflight'] = 'native_schema_and_complete_support_validated'
        # Old final12 is now seen. These cases use seen, unsealed hours, and no release bypass.
        from tools.sci_engineering.design import SEALED_DAYS
        env = environment.OperatingHospitalIESEnv(runtime_root, allowed_hours=support,
            sealed_days=SEALED_DAYS, model_id=package.MODEL_ID, model_config=config)
        result['resolved_model_config'] = copy.deepcopy(getattr(env, 'operating_model_config', config))
        result['resolved_model_config_sha256'] = digest(result['resolved_model_config'])
        policy_spec = runtime_policy(case, registry, policy_module)
        policy_id = policy_spec['alias'] + ('_sci_v21' if legacy else '_model_closure_v22')
        controller = policy_module.SciPolicy(policy_spec['alias'], config=policy_spec['config'],
            policy_id=policy_id, plant=getattr(env, 'plant', None))
        result['policy'] = dict(policy_id=policy_id, alias=policy_spec['alias'], config=controller.config,
            selection_basis=policy_spec['selection_basis'])
        resolved_case = dict(case_id=case['id'], scenario=copy.deepcopy(scene), configuration=case['configuration'],
            policy=copy.deepcopy(result['policy']), model_config=copy.deepcopy(result['resolved_model_config']))
        result['case_binding'] = dict(original_case_sha256=digest(original_case), requested_case_sha256=digest(case),
            resolved_case_sha256=digest(resolved_case), scenario_before_sha256=digest(original_case['scenario']),
            scenario_after_sha256=digest(scene), case_input_binding=registration.get('case_input_binding'))
        write(output / 'RESOLVED_CASE.json', dict(resolved_case, case_binding=result['case_binding']))
        result['input_access_receipt'] = copy.deepcopy(getattr(env, 'input_access_receipt', None))
        manifest = dict(execution_contract_id=package.CONTRACT_ID, assumption_set_id=package.CONTRACT_ID,
            engineering_model_contract_id=package.CONTRACT_ID, study_contract_id=package.CONTRACT_ID,
            model_config_signature=result['resolved_model_config_sha256'],
            configuration_id=case['configuration'], execution_model_id=package.MODEL_ID,
            simulator_hash=result['source_sha256'], schemas=copy.deepcopy(registry['schemas']), splits={scene['id']:dict(
                group_id=scene['family_id'], split='development', fit_allowed=False, embargo_minutes=60.,
                grouping_basis='full source support; seen regression only')})
        if not legacy:
            manifest['schemas']['model_closure_v22'] = package.CONTRACT_ID
        manifest['signature'] = digest(manifest)
        result['manifest'] = copy.deepcopy(manifest)
        result['actual_episode_attempts'] = 1
        write(output / 'STATUS.json', result)
        payload = environment.run_episode(env, scene, controller, int(scene.get('initialization_seed', 20261023)), manifest)
        factor = registry.get('emissions_factor_registry', {})
        factor_path = runtime_root / factor.get('path', '__missing_factor_registry__')
        factors = None
        if factor_path.is_file() and sha(factor_path) == factor.get('sha256'):
            factors = read(factor_path)
            result['emissions_factor_binding'] = dict(path=str(factor_path), sha256=sha(factor_path))
        else:
            result['emissions_missing_reason'] = 'Registered factor file is missing or SHA differs; CO2 remains unknown'
        result['metrics'] = compact_metrics(payload, env=env, factor_registry=factors)
        result['process'] = native_process(payload, output / 'PROCESS.csv')
        result['error'] = copy.deepcopy(payload.get('error'))
        metrics = result['metrics']
        result.update(observed_until_minute=metrics['observed_until_minute'], unknown_tail_minutes=metrics['unknown_tail_minutes'])
        result['status'] = ('completed' if payload.get('summary', {}).get('status') == 'completed'
            and metrics['complete'] and result['error'] is None else 'failed_unknown')
    except Exception as exc:
        result.update(status='failed_unknown', error=dict(type=type(exc).__name__, message=str(exc),
            traceback=traceback.format_exc(), physical_error=payload.get('error') if payload else None))
        if payload is None and env is not None:
            try:
                payload = recover_execution_prefix(env, scene, result.get('model_id'), result['error'])
                if payload is not None:
                    result['recovered_prefix'] = True
                    result['recovery_semantics'] = payload['recovery_semantics']
                    result['observed_until_minute'] = payload['summary']['observed_until_minute']
                    result['unknown_tail_minutes'] = max(0., requested-result['observed_until_minute'])
                    if payload['segments']:
                        result['metrics'] = compact_metrics(payload, env=env, factor_registry=None)
                        result['metrics']['episode_completion_valid'] = False
                        result['metrics']['recovered_prefix_only'] = True
                        result['process'] = native_process(payload, output / 'PROCESS.csv')
            except Exception as recovery_error:
                result['prefix_recovery_error'] = dict(type=type(recovery_error).__name__, message=str(recovery_error))
    finally:
        if payload is not None:
            raw = output / 'trajectory.json.gz'
            try:
                with raw.open('xb') as binary:
                    with gzip.GzipFile(fileobj=binary, mode='wb', mtime=0) as compressed:
                        compressed.write(json.dumps(payload, ensure_ascii=False, allow_nan=False,
                            separators=(',', ':')).encode('utf-8'))
                result.update(raw_path=str(raw), raw_sha256=sha(raw), raw_bytes=raw.stat().st_size)
                # compact_metrics derives the actual contiguous prefix from segments.
                # An absent optional summary field must not erase that known extent.
                observed = result.get('observed_until_minute')
                summary_observed = payload.get('summary', {}).get('observed_until_minute')
                if observed is None and isinstance(summary_observed, (int, float)) and not isinstance(summary_observed, bool) and math.isfinite(summary_observed):
                    observed = float(summary_observed)
                    result['observed_until_minute'] = observed
                if isinstance(observed, (int, float)) and not isinstance(observed, bool) and math.isfinite(observed):
                    result['unknown_tail_minutes'] = max(0., requested-observed)
            except Exception as exc:
                result.update(status='failed_unknown', evidence_error=dict(type=type(exc).__name__, message=str(exc)))
        if bindings is not None:
            try:
                unchanged = bindings == dict(source=source_binding(runtime_root), inputs=input_binding(runtime_root))
                result['source_and_inputs_unchanged'] = unchanged
                if not unchanged:
                    result['status'] = 'failed_unknown'
                    result['source_drift_error'] = 'Source or input bytes changed during this actual attempt'
            except Exception as exc:
                result.update(status='failed_unknown', source_check_error=str(exc))
        result.update(completed_utc=now(), elapsed_seconds=time.perf_counter()-began)
        write(output / 'RESULT.json', result)
        write(output / 'STATUS.json', {key:result[key] for key in ('case_id', 'status', 'started_utc',
            'completed_utc', 'actual_episode_attempts', 'observed_until_minute', 'unknown_tail_minutes')})
    print(json.dumps({key:result[key] for key in ('case_id', 'status', 'actual_episode_attempts', 'unknown_tail_minutes')}))
    return 0 if result['status'] == 'completed' else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument('--case')
    selection.add_argument('--case-file', help='Complete case JSON inside the new E recipes or versioned repository recipes directory')
    selection.add_argument('--list', action='store_true')
    parser.add_argument('--output')
    parser.add_argument('--legacy-root')
    parser.add_argument('--policy', choices=('R', 'M', 'F', 'C', 'A'))
    parser.add_argument('--configuration', choices=('H0', 'H1', 'H2'))
    parser.add_argument('--_worker', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--_case-file', help=argparse.SUPPRESS)
    parser.add_argument('--_runtime-root', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args._worker:
        return worker(args._case_file, args._runtime_root, args.output, bool(args.legacy_root))
    registry = read(CASES)
    if args.list:
        if args.output or args.legacy_root or args.policy or args.configuration:
            parser.error('--list is read-only and cannot be combined with execution arguments')
        for case in registry['cases']:
            suffix = ' [V22 events; legacy refused]' if case['scenario'].get('model_closure_events') else ''
            print(case['id'] + '\t' + case['purpose'] + suffix)
        return 0
    if not (args.case or args.case_file) or not args.output:
        parser.error('Execution requires --case or --case-file and a fresh --output directory')
    if args.case_file:
        case, case_input_binding = load_case_file(args.case_file, registry)
    else:
        case = selected_case(registry, args.case)
        case_input_binding = dict(path=str(CASES), sha256=sha(CASES), registered_case_id=args.case)
    original_case = copy.deepcopy(case)
    if args.policy:
        case['policy_override_alias'] = args.policy
    if args.configuration:
        case['configuration'] = args.configuration
    output = checked_output(args.output)
    runtime_root = Path(args.legacy_root).resolve() if args.legacy_root else ROOT
    if not (runtime_root / 'src/hospital_ies').is_dir():
        raise FileNotFoundError('Runtime source root lacks src/hospital_ies')
    output.mkdir(parents=True, exist_ok=False)
    write(output / 'CASE.json', dict(registry={key:value for key,value in registry.items() if key != 'cases'},
        original_case=original_case, case=case, case_input_binding=case_input_binding))
    command = [sys.executable, '-I', str(Path(__file__).resolve()), '--_worker',
        '--_case-file', str(output / 'CASE.json'), '--_runtime-root', str(runtime_root), '--output', str(output)]
    if args.legacy_root:
        command.extend(['--legacy-root', str(runtime_root)])
    write(output / 'INVOCATION.json', dict(command=command, cwd=str(runtime_root), started_utc=now(),
        runner_script_sha256=sha(Path(__file__)), cases_registry_sha256=sha(CASES), case_input_binding=case_input_binding,
        legacy_root_read_only=bool(args.legacy_root), physical_attempt_count_source='RESULT.actual_episode_attempts'))
    child_env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONIOENCODING='utf-8',
        TEMP=str(output / 'temp'), TMP=str(output / 'temp'))
    (output / 'temp').mkdir()
    with (output / 'stdout.log').open('x', encoding='utf-8') as stdout, (output / 'stderr.log').open('x', encoding='utf-8') as stderr:
        done = subprocess.run(command, cwd=runtime_root, env=child_env, stdout=stdout, stderr=stderr, check=False)
    invocation = read(output / 'INVOCATION.json')
    invocation.update(finished_utc=now(), exit_code=done.returncode)
    write(output / 'INVOCATION.json', invocation)
    if (output / 'RESULT.json').is_file():
        result = read(output / 'RESULT.json')
        print(json.dumps(dict(case_id=case['id'], status=result['status'], output=str(output),
            actual_episode_attempts=result['actual_episode_attempts'], exit_code=done.returncode)))
    else:
        print(json.dumps(dict(case_id=case['id'], status='failed_unknown', output=str(output),
            actual_episode_attempts=None, error='Child ended without RESULT; inspect STATUS and logs', exit_code=done.returncode)))
    return done.returncode


if __name__ == '__main__':
    raise SystemExit(main())
