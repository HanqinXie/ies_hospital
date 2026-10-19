"""Read-only final-campaign qualification for original-root publication.

This publication helper does not register, simulate, open trajectories, parse
annual values, change scores, or certify physical realism. Fresh clones verify
the saved gate proof and file selection instead of invoking this campaign gate.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import copy
from datetime import datetime, timezone
import hashlib
import importlib
import json
import math
from pathlib import Path, PurePosixPath
import re
import sys
from types import SimpleNamespace

RUN_REL = 'outputs/v20_load_accounting_20261019_run01'
FREEZE_NAME = 'MODEL_FREEZE_ACCOUNTING01.json'
MODEL = 'M20_load_accounting'
COHORT_BATCHES = dict(main=80, long=8, normal=12, compat=7, fixed8=32, propagation=6, open1=2, contact025=1)
COHORT_PAIRS = dict(main=240, long=24, normal=12, compat=7, fixed8=96, propagation=18, open1=6, contact025=3)
PRIORITIES = ('P0', 'P1', 'P2', 'P3')
CARRIERS = ('e', 'h', 'c')
SERVICE_FIELDS = ('requested_kWh', 'delivered_kWh', 'ENS_kWh', 'disconnected_ENS_kWh', 'connected_unserved_ENS_kWh')
SOURCE_UNITS = dict(GT_electric_kWh='kWh_e', GRID_electric_kWh='kWh_e', DG_electric_kWh='kWh_e',
    ABS_cooling_kWh='kWh_c', EC_cooling_kWh='kWh_c', TOWER_rejection_kWh='kWh_h', HRSG_steam_lb='lb', BOILER_steam_lb='lb')
# Same arithmetic endpoint coverage epsilon as frozen service_ledger.TIME_EPS;
# this is not a service/failure threshold or an expanded comparison tolerance.
COVERAGE_EPS_MINUTES = 1e-8
ECON_FIELDS = ('actual_energy_fee_USD', 'accounting_cost_USD', 'GT_gas_MMBtu', 'boiler_gas_MMBtu',
    'natural_gas_total_MMBtu', 'grid_purchase_kWh', 'grid_fee_USD', 'GT_gas_fee_USD', 'boiler_gas_fee_USD',
    'DG_electric_work_kWh', 'DG_shaft_work_kWh', 'DG_loss_work_kWh', 'DG_rotor_delta_kWh',
    'DG_fuel_budget_debit_kWh_equivalent', 'DG_original_electric_fee_USD', 'DG_shaft_equivalent_fee_USD')
STREAM_FIELDS = {'start_hour', 'duration_steps', 'step_minutes', 'faults', 'shock', 'shock_by_priority',
    'exogenous_events', 'enable_propagation', 'propagation_enabled', 'grid_limit_kW',
    'pre_event_grid_limit_kW', 'pre_event_warmup_steps', 'response_delay_minutes',
    'automatic_fast_shedding', 'initialization_seed'}
COMPACT_EXPORTS = ('V20_RESOURCES.csv', 'V20_EMISSIONS.csv', 'V20_EPISODE_AUDIT.json',
    'V18_CARRIER_SERVICE.csv', 'V18_ECONOMICS.csv', 'GT_RUNS.csv', 'GT_TRANSITIONS.csv',
    'MODEL_DOMAIN.csv', 'THERMAL_COVERAGE.csv')


def require(value, message):
    if not value:
        raise ValueError(message)


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def sha(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=True, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def safe(root, name):
    require(isinstance(name, str) and bool(name) and ':' not in name and '\\' not in name, 'Unsafe repository-relative evidence path')
    parts = PurePosixPath(name)
    require(not parts.is_absolute() and not any(p in ('.', '..') for p in parts.parts), 'Unsafe repository-relative evidence path')
    value = (Path(root) / name).resolve()
    require(value.is_relative_to(Path(root).resolve()), 'Evidence path escapes current scientific root')
    return value


def finite(value, label):
    require(type(value) in (int, float) and math.isfinite(value), 'Unobserved or nonfinite ' + label)
    return float(value)


def is_sha(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None


def stream_hash(scene):
    # Exactly the frozen operating_response.runner.matching_evidence subset.
    return digest({k: v for k, v in scene.items() if k in STREAM_FIELDS or k.startswith(('fault_', 'shock_'))})


def load_api(root):
    """Import only the current root's frozen validators; never create an env."""
    root = Path(root).resolve(strict=True)
    for path in (str(root / 'src'), str(root)):
        if path not in sys.path:
            sys.path.insert(0, path)
    names = dict(runner='tools.load_accounting.runner', design='tools.load_accounting.data_design',
                 campaign='scripts.v20_campaign', report='scripts.v20_report', gate='tools.load_accounting.gate',
                 legacy_freeze='tools.model_closeout.freeze', matching='tools.execution_recovery.summarize')
    modules = {key: importlib.import_module(name) for key, name in names.items()}
    for name, module in list(sys.modules.items()):
        if name == 'tools' or name.startswith(('tools.', 'hospital_ies', 'scripts.v20')):
            origin = getattr(module, '__file__', None)
            require(origin is None or Path(origin).resolve().is_relative_to(root), 'Project validator import escaped current root: ' + name)
    require(all(Path(m.__file__).resolve().is_relative_to(root) for m in modules.values()), 'Validator root differs')
    require(Path(modules['runner'].ROOT).resolve() == root and Path(modules['campaign'].ROOT).resolve() == root,
            'Frozen validator ROOT differs from requested root')
    return SimpleNamespace(**modules)


def qualify_census(jobs):
    """Validate planned arithmetic and backgrounds, without claiming execution."""
    require(isinstance(jobs, list) and len(jobs) == 148, 'Final census requires exactly 148 canonical batches')
    require(len({j['name'] for j in jobs}) == 148, 'Duplicate canonical batch identity')
    batches = Counter(j['cohort'] for j in jobs)
    pairs = Counter()
    for j in jobs:
        cohort = j['cohort']
        expected = ['C18_M20'] if cohort == 'compat' else None if cohort == 'normal' else ['E', 'K1', 'C19']
        require((j['policies'] == expected) if expected else
                (len(j['policies']) == 1 and j['policies'][0] in ('E', 'K1', 'C19')), 'Canonical policy denominator differs')
        require(j.get('normal_serial') is (cohort == 'normal'), 'Canonical normal-serial role differs')
        require(j.get('config_variant') == (cohort if cohort in ('open1', 'contact025') else 'base'), 'Canonical configuration variant differs')
        require(isinstance(j['scene'].get('id'), str) and bool(j['scene']['id']), 'Missing canonical scene identity')
        pairs[cohort] += len(j['policies'])
    require(dict(batches) == COHORT_BATCHES and dict(pairs) == COHORT_PAIRS, 'Final cohort batch/pair denominators differ')
    identities = [(j['name'], j['scene']['id'], a) for j in jobs for a in j['policies']]
    require(len(identities) == 406 and len(set(identities)) == 406, 'Final census requires 406 unique canonical pairs')
    class_counts = {}
    for cohort, backgrounds, per_class in (('main', 20, 20), ('fixed8', 8, 8)):
        selected = [j['scene'] for j in jobs if j['cohort'] == cohort]
        require(len({s['id'] for s in selected}) == len(selected), 'Duplicate ' + cohort + ' mechanism specification')
        counts = Counter(s.get('canonical_class') for s in selected)
        require(dict(counts) == {c: per_class for c in ('L1', 'L2', 'L3', 'L4')}, cohort + ' canonical class denominator differs')
        by_day = defaultdict(list)
        for scene in selected:
            require(type(scene.get('source_day_index')) is int, 'Unknown source background')
            by_day[scene['source_day_index']].append(scene['canonical_class'])
        require(len(by_day) == backgrounds and all(sorted(v) == ['L1', 'L2', 'L3', 'L4'] for v in by_day.values()),
                cohort + ' background-by-mechanism coverage differs')
        class_counts[cohort] = dict(counts)
    normal = defaultdict(list)
    for j in jobs:
        if j['cohort'] == 'normal':
            scene = j['scene']; normal[scene['id']].append(j)
            minutes = finite(scene['duration_steps'], 'normal steps') * finite(scene.get('step_minutes', 15.), 'normal step minutes')
            require(finite(scene.get('normal_duration_hours'), 'normal hours') * 60. == minutes, 'Normal hours and actual registered duration differ')
    require(len(normal) == 4 and all(sorted(j['policies'][0] for j in group) == ['C19', 'E', 'K1'] for group in normal.values()),
            'Normal four-window / three-single-policy coverage differs')
    hours = []
    for group in normal.values():
        require(all(j['scene'] == group[0]['scene'] for j in group), 'Normal policy panels have different full registered scenes')
        hours.append(group[0]['scene']['normal_duration_hours'])
    require(sorted(hours) == [24., 24., 48., 72.], 'Normal registered window durations differ')
    return dict(batches=148, pairs=406, cohort_batches=dict(batches), cohort_pairs=dict(pairs),
                canonical_specification_counts=class_counts, main_backgrounds=20, fixed_backgrounds=8,
                fixed_mechanism_specifications=32, normal_windows=4, normal_window_hours_separate=sorted(hours))


def qualify_complete_window(record, end):
    end = finite(end, 'registered end')
    require(end > 0, 'Nonpositive registered interval')
    analysis = record.get('analysis') or {}
    require(record.get('model_id') == analysis.get('execution_model_id') == MODEL, 'Saved metric model identity differs')
    require(analysis.get('observed_until_minute') == end and analysis.get('requested_end_minute') == end
            and analysis.get('unknown_tail_minutes') is None, 'Saved analysis does not cover complete registered duration')
    window = analysis.get('v18', {}).get('windows', {}).get('registered_full') or {}
    ledger = analysis.get('v20', {}).get('service_ledgers', {}).get('registered_full') or {}
    for label, value in (('service window', window), ('service ledger', ledger)):
        require(value.get('complete') is True and value.get('window_minutes') == [0., end]
                and value.get('unknown_tail_minutes') is None and isinstance(value.get('all_registered'), dict)
                and bool(value['all_registered']), 'Incomplete actual registered ' + label)
        require(value.get('observed_prefix') == value['all_registered'], 'Complete window contains a different prefix-only ' + label)
    require(ledger.get('observed_finish_minute') == end, 'Actual ledger finish differs from registered end')
    scalars = 0
    for p in PRIORITIES:
        for c in CARRIERS:
            values = ledger['all_registered'].get(p, {}).get(c, {})
            for field in SERVICE_FIELDS:
                finite(values.get(field), 'actual ' + p + '/' + c + '/' + field); scalars += 1
            service = window['all_registered'].get('carrier_service', {}).get(p, {}).get(c, {})
            for field in ('ENS_kWh', 'disconnected_ENS_kWh', 'connected_unserved_ENS_kWh', 'material_minutes', 'longest_material_minutes'):
                finite(service.get(field), 'saved carrier ' + p + '/' + c + '/' + field)
            require(isinstance(service.get('loss_runs'), list), 'Unknown actual carrier interruption intervals')
            for run in service['loss_runs']:
                a, b = finite(run.get('start_minute'), 'loss start'), finite(run.get('end_minute'), 'loss end')
                require(0. <= a <= b <= end, 'Carrier interruption interval escapes registered window')
    economics = window['all_registered'].get('economics') or {}
    for field in ECON_FIELDS:
        finite(economics.get(field), 'saved economics/' + field)
    sources = ledger.get('actual_source_integrals') or {}
    for field, unit in SOURCE_UNITS.items():
        value = sources.get(field) or {}
        finite(value.get('value'), 'actual source/' + field)
        require(value.get('unit') == unit
                and abs(finite(value.get('covered_minutes'), 'actual source coverage/' + field) - end) <= COVERAGE_EPS_MINUTES,
                'Actual source integral unit or whole-window coverage differs: ' + field)
    for field in ('DG_budget_start_kWh_equivalent', 'DG_budget_finish_kWh_equivalent'):
        finite(ledger.get(field), 'actual shaft-equivalent budget/' + field)
    require(ledger.get('DG_budget_is_chemical_fuel') is False, 'Shaft-equivalent budget mislabeled as chemical fuel')
    # This is coverage, not a new service threshold, balance tolerance, CO2
    # objective, diesel chemical-fuel conversion, or zero-failure requirement.
    return dict(complete=True, window_minutes=[0., end], service_scalar_count=scalars,
                economics_scalar_count=len(ECON_FIELDS), actual_source_scalar_count=len(SOURCE_UNITS),
                unknown_diesel_chemical_co2_is_not_imputed=True)


def qualify_matching(evidence, scene, count):
    require(isinstance(evidence, dict) and evidence.get('same_initial_state') is True
            and evidence.get('same_exogenous_definition') is True, 'Actual reset/exogenous matching is unknown or failed')
    for field in ('initial_state_sha256', 'applied_exogenous_definition_sha256'):
        values = evidence.get(field)
        require(isinstance(values, list) and len(values) == count and all(is_sha(v) for v in values)
                and len(set(values)) == 1, 'Actual matching fingerprint incomplete or unequal: ' + field)
    expected = stream_hash(scene)
    require(evidence['applied_exogenous_definition_sha256'] == [expected] * count, 'Actual applied stream differs from registered definition')
    injection = evidence.get('injection_state_sha256')
    require(isinstance(injection, list) and len(injection) == count, 'Actual injection evidence coverage missing')
    no_event = scene.get('first_event_minute') is None
    require(all(v is None or is_sha(v) for v in injection) if no_event else all(is_sha(v) for v in injection),
            'Unknown or malformed event injection evidence')
    return dict(actual_stream_recomputed=True, registered_stream_sha256=expected,
                actual_initial_state_sha256=evidence['initial_state_sha256'][0],
                injection_state_sha256=injection, injection_not_applicable=no_event,
                policy_dependent_injection_equality_is_not_required=True)


def expected_registration(root, job, frozen, api):
    root = Path(root); run = root / RUN_REL
    path = safe(root, RUN_REL + '/provenance/v19_design/' + job['source_proposal'])
    specs = [api.runner.policy_specs()[alias] for alias in job['policies']]
    proposal = api.design.panel(job['name'], [job['scene']],
        bindings={path.relative_to(root).as_posix(): dict(sha256=sha(path))}, freeze_signature=frozen['signature'],
        evaluation_role='seen_fixed_background' if job['cohort'] == 'fixed8' else 'seen_regression')
    config = copy.deepcopy(frozen['model_config'])
    if job['config_variant'] != 'base':
        name = 'M20_OPEN1_CONFIG.json' if job['cohort'] == 'open1' else 'M20_CONTACT025_CONFIG.json'
        config = api.runner.model_contract(MODEL, read(run / 'design' / name))[1]
        sensitivity = dict(schema='hospital_ies.v20.assumption_preregistration.v1', source_freeze_signature=frozen['signature'],
            source_sha256=frozen['source_sha256'], model_id=MODEL, model_config=config, policy_specs=specs,
            reason='Repeat the originally registered ' + job['cohort'] + ' execution-clock assumption under corrected V20 loads; no new parameter search')
        sensitivity['signature'] = digest(sensitivity)
        proposal.pop('signature'); proposal['sensitivity_registration'] = sensitivity; proposal['signature'] = digest(proposal)
    return proposal, config


def qualify_registration(plan, manifest, job, frozen, freeze_sha256, proposal, config, manifest_sha256, api):
    specs = [frozen['policies'][alias] for alias in job['policies']]
    require(plan.get('model_id') == manifest.get('execution_model_id') == frozen.get('model_id') == MODEL, 'Registered model differs')
    require(plan.get('source_sha256') == manifest.get('source_sha256') == frozen['source_sha256'], 'Registered source differs')
    require(plan.get('model_config') == manifest.get('model_config') == config
            and manifest.get('model_config_signature') == digest(config), 'Registered model configuration differs')
    require(manifest.get('files') == frozen['runtime_inputs'], 'Registered runtime input inventory differs')
    require(plan.get('policy_specs') == specs and manifest.get('policies') == [s['policy_id'] for s in specs]
            and manifest.get('policy_specs_sha256') == digest(specs), 'Registered policy panel differs')
    require(manifest.get('signature') == digest({k: v for k, v in manifest.items() if k != 'signature'})
            and plan.get('manifest_signature') == manifest['signature'] and plan.get('manifest_sha256') == manifest_sha256,
            'Plan/manifest signature or byte binding differs')
    require(manifest.get('scenarios') == proposal['scenarios'], 'Registered full scene differs from canonical proposal')
    require(plan.get('scenario_ids') == [s['id'] for s in proposal['scenarios']]
            and plan.get('registered_pairs') == len(specs) and plan.get('phase') == job['cohort'], 'Registered pair/phase identity differs')
    require(manifest.get('source_proposal_signature') == proposal['signature']
            and manifest.get('source_metadata_bindings') == proposal['source_metadata_bindings']
            and manifest.get('roots') == proposal['roots'], 'Registered canonical source proposal binding differs')
    require(manifest.get('freeze_signature') == frozen['signature']
            and manifest.get('source_freeze_artifact', {}).get('sha256') == freeze_sha256, 'Registered final freeze artifact differs')
    require(manifest.get('sensitivity_registration') == proposal.get('sensitivity_registration'), 'Registered clock sensitivity differs')
    for key in ('evaluation_role', 'fixed_background_count', 'confirmation_background_count',
                'new_independent_background_count', 'historical_unseen_claim', 'original_final12_evaluated',
                'original_final_release_authorized', 'sealed_final_days'):
        require(manifest.get(key) == proposal.get(key), 'Registered evaluation boundary differs: ' + key)
    api.runner.validate_final_freeze(frozen, plan['source_sha256'], MODEL, config, specs,
        sensitivity=manifest.get('sensitivity_registration'), runtime_inputs=manifest['files'])
    contract, resolved = api.runner.model_contract(MODEL, config)
    require(resolved == config and manifest.get('execution_contract') == contract
            and manifest.get('simulator_hash') == digest(frozen['source_sha256']), 'Registered execution contract differs')
    return specs


def qualify_terminal_structure(terminal, scene, specs, journal):
    count = len(specs)
    require(type(terminal.get('registered_pairs')) is int and terminal['registered_pairs'] == count
            and terminal.get('actual_attempts') == count and terminal.get('complete_full') == count
            and terminal.get('unknown_full') == 0 and terminal.get('analysis_failure_or_unavailable_count') == 0
            and terminal.get('status_counts') == {'completed': count} and terminal.get('source_unchanged') is True,
            'Actual terminal counts/status/source are incomplete')
    expected = {(scene['id'], s['policy_id'], event) for s in specs for event in ('started', 'finished')}
    require(isinstance(journal, list) and len(journal) == 2 * count
            and all(isinstance(row, dict) for row in journal), 'Actual attempt journal is not uniquely complete')
    observed = [(r.get('scenario_id'), r.get('policy_id'), r.get('event')) for r in journal]
    require(set(observed) == expected and len(set(observed)) == len(observed), 'Actual journal contains duplicate, unknown, or missing attempts')
    require(set(terminal.get('matching_evidence', {})) == {scene['id']}, 'Actual terminal matching scene denominator differs')
    index = terminal.get('pair_index')
    require(isinstance(index, list) and len(index) == count
            and [(r.get('scenario_id'), r.get('policy_id')) for r in index] == [(scene['id'], s['policy_id']) for s in specs],
            'Actual terminal pair index denominator/order differs')


def qualify_compact_exports(root, directory, terminal):
    """Bind selected compact exports to their genuine terminal SHA receipts."""
    root = Path(root).resolve(); directory = Path(directory)
    receipt = terminal.get('analysis_receipt') or {}
    inherited = receipt.get('compatibility_export_receipt') or {}
    declared = {}
    for scope, key in ((inherited, 'output_sha256'), (inherited, 'v18_output_sha256'), (receipt, 'output_sha256')):
        for name, value in (scope.get(key) or {}).items():
            require(name not in declared or declared[name] == value, 'Inconsistent terminal compact export SHA')
            declared[name] = value
    rows = []
    for name in COMPACT_EXPORTS:
        path = safe(root, (directory / 'analysis' / name).relative_to(root).as_posix())
        require(is_sha(declared.get(name)) and sha(path) == declared[name], 'Saved compact export differs from actual terminal receipt: ' + name)
        rows.append(dict(path=path.relative_to(root).as_posix(), bytes=path.stat().st_size, sha256=declared[name]))
    require(receipt.get('registered_trajectory_count') == terminal['registered_pairs']
            and inherited.get('registered_trajectory_count') == terminal['registered_pairs'], 'Compact export registered denominator differs')
    return rows


def qualify_cross_panel_batches(jobs, batches):
    """Actual joins for four normal scenes and seven C18 fixed explanations."""
    lookup = {b['batch']: b for b in batches}
    require(len(lookup) == len(batches) == len(jobs) and set(lookup) == {j['name'] for j in jobs}, 'Cross-panel canonical batch census differs')
    by_scene = defaultdict(list)
    for j in jobs:
        by_scene[(j['cohort'], j['scene']['id'])].append((j, lookup[j['name']]))
    fields = ('registered_scene_sha256', 'source_binding_sha256', 'model_config_sha256', 'runtime_inputs_sha256')

    def match(left, right):
        require(all(is_sha(left.get(k)) and left[k] == right.get(k) for k in fields), 'Cross-panel registered scene/source/configuration/input identity differs')
        for key in ('actual_initial_state_sha256', 'registered_stream_sha256'):
            a, b = left['matching'].get(key), right['matching'].get(key)
            require(is_sha(a) and a == b, 'Cross-panel actual reset/applied stream differs: ' + key)

    normal_count = 0; compatibility_count = 0; rows = []
    for (cohort, scene_id), group in by_scene.items():
        if cohort != 'normal':
            continue
        require(len(group) == 3 and sorted(j['policies'][0] for j, b in group) == ['C19', 'E', 'K1'], 'Cross-panel normal policy coverage differs')
        for i, (_, left) in enumerate(group):
            for _, right in group[i + 1:]:
                match(left, right); normal_count += 1
                rows.append(dict(cohort='normal', scenario_id=scene_id, left_batch=left['batch'], right_batch=right['batch']))
    for job in jobs:
        if job['cohort'] != 'compat':
            continue
        candidates = [(j, b) for c in ('main', 'long') for j, b in by_scene.get((c, job['scene']['id']), []) if 'C19' in j['policies']]
        require(len(candidates) == 1, 'Compatibility explanation lacks a unique canonical C19 counterpart')
        left, right = candidates[0][1], lookup[job['name']]
        match(left, right); compatibility_count += 1
        rows.append(dict(cohort='compat', scenario_id=job['scene']['id'], left_batch=left['batch'], right_batch=right['batch'],
                         scope='Seven fixed case explanations; not an algorithm-wide superiority denominator'))
    require(normal_count == 12 and compatibility_count == 7, 'Required final cross-panel matching denominator differs')
    return dict(normal_policy_pairs=normal_count, compatibility_policy_pairs=compatibility_count,
                policy_panels_remain_distinct=True, rows=rows)


def qualify_registered_batch(root, job, frozen, freeze_sha256, *, api=None):
    """One canonical batch; safe to use on saved main000 as a real test."""
    root = Path(root).resolve(strict=True); api = api or load_api(root)
    directory = safe(root, RUN_REL + '/experiments/' + job['name'])
    files = []

    def indexed(path):
        path = safe(root, Path(path).relative_to(root).as_posix())
        content = path.read_bytes()
        files.append(dict(path=path.relative_to(root).as_posix(), bytes=len(content), sha256=hashlib.sha256(content).hexdigest()))
        return json.loads(content.decode('utf-8-sig'))

    plan = indexed(directory / 'plan.json'); manifest = indexed(directory / 'manifest.json')
    plan_sha, manifest_sha = files[0]['sha256'], files[1]['sha256']
    proposal, config = expected_registration(root, job, frozen, api)
    specs = qualify_registration(plan, manifest, job, frozen, freeze_sha256, proposal, config, manifest_sha, api)
    terminal = indexed(directory / 'BATCH_RESULT.json')
    journal_path = safe(root, (directory / 'ATTEMPTS.jsonl').relative_to(root).as_posix())
    journal_bytes = journal_path.read_bytes()
    files.append(dict(path=journal_path.relative_to(root).as_posix(), bytes=len(journal_bytes), sha256=hashlib.sha256(journal_bytes).hexdigest()))
    journal = [json.loads(line) for line in journal_bytes.decode('utf-8-sig').splitlines() if line.strip()]
    scene = manifest['scenarios'][0]
    qualify_terminal_structure(terminal, scene, specs, journal)
    require(terminal.get('plan_sha256') == plan_sha and terminal.get('manifest_sha256') == manifest_sha,
            'Actual terminal plan/manifest byte binding differs')
    issues = api.campaign.terminal_issues(directory, terminal)
    require(not issues, 'Frozen terminal qualification failed: ' + str(issues))
    matching = qualify_matching(terminal['matching_evidence'][scene['id']], scene, len(specs))
    require(api.matching.valid_matching_evidence(terminal['matching_evidence'][scene['id']], len(specs)), 'Frozen matching qualification failed')
    end = finite(scene['duration_steps'], 'duration steps') * finite(scene.get('step_minutes', 15.), 'step minutes')
    pair_proofs = []
    expected_dirs = {scene['id'] + '__' + spec['policy_id'] for spec in specs}
    require({p.name for p in (directory / 'pairs').iterdir() if p.is_dir()} == expected_dirs, 'Unexpected actual pair directory retained in canonical batch')
    for index, spec in enumerate(specs):
        path = directory / 'pairs' / (scene['id'] + '__' + spec['policy_id']) / 'RESULT.json'
        record = indexed(path)
        require(record.get('scenario_id') == scene['id'] and record.get('policy_id') == spec['policy_id']
                and record.get('model_id') == MODEL and record.get('manifest_signature') == manifest['signature']
                and record.get('model_config_signature') == manifest['model_config_signature']
                and record.get('analysis_class') == scene.get('analysis_class')
                and record.get('source_day_index') == scene.get('source_day_index') and is_sha(record.get('raw_sha256')),
                'Actual RESULT identity/configuration/raw fingerprint differs')
        require(record.get('error') is None and record.get('analysis_error') is None, 'Actual RESULT retains failure')
        qualified, reasons = api.report.execution_qualification(record, journal, path, audit_key='v20')
        require(qualified, 'Frozen actual execution qualification failed: ' + str(reasons))
        require(terminal['pair_index'][index] == {k: v for k, v in record.items() if k != 'analysis'}, 'Terminal pair index differs from saved RESULT')
        window_proof = qualify_complete_window(record, end)
        pair_proofs.append(dict(alias=job['policies'][index], policy_id=spec['policy_id'], result_sha256=files[-1]['sha256'],
                                raw_sha256=record['raw_sha256'], **window_proof))
    files.extend(qualify_compact_exports(root, directory, terminal))
    return dict(qualified=True, batch=job['name'], cohort=job['cohort'], scenario_id=scene['id'], pairs=len(specs),
                window_minutes=[0., end], terminal_sha256=files[2]['sha256'],
                registered_scene_sha256=digest(scene), source_binding_sha256=digest(plan['source_sha256']),
                model_config_sha256=digest(plan['model_config']), runtime_inputs_sha256=digest(manifest['files']),
                metadata_sha256=digest(files), pair_proofs_sha256=digest(pair_proofs), matching=matching,
                raw_files_opened=0, evidence_files=files)


def qualify_final_campaign(root):
    """Return a JSON-safe passed proof only after all real 148/406 qualify.

    Raises on every missing, failed, partial, duplicated, changed, or unknown
    required evidence. Does not write a marker or give partial results a pass.
    """
    root = Path(root).resolve(strict=True); run = root / RUN_REL
    api = load_api(root)
    freeze_path = safe(root, RUN_REL + '/' + FREEZE_NAME)
    frozen = read(freeze_path); freeze_sha256 = sha(freeze_path)
    for name in frozen['source_sha256']:
        safe(root, name)
    source = api.runner.source_binding(); tests = api.gate.test_binding()
    inventory = api.design.runtime_input_inventory(root)
    require(source == frozen['source_sha256'], 'Current source differs from frozen scientific source')
    require(len(source) == 366 and len(tests) == 241 and len(inventory) == 28, 'Frozen production/test/runtime input closure denominator differs')
    api.legacy_freeze.validate_test_gate(frozen['source_bound_test_gate'], source, tests)
    api.runner.validate_final_freeze(frozen, source, MODEL, frozen['model_config'], list(api.runner.policy_specs().values()), runtime_inputs=inventory)
    require(frozen.get('original_final12') == 'unopened' and frozen.get('new_independent_background_count') == 0
            and frozen.get('fixed_eight_backgrounds_are_seen_rechecks') is True, 'Frozen scientific evaluation boundary differs')
    provenance = frozen.get('original_provenance') or {}
    provenance_path = safe(root, provenance['path'])
    require(sha(provenance_path) == provenance.get('sha256') and read(provenance_path) == provenance.get('record'), 'Starting-point provenance differs from final freeze')
    evidence = []
    for row in provenance['record']['source_files']:
        path = safe(root, row['repository_path'])
        require(path.stat().st_size == row['bytes'] and sha(path) == row['sha256'], 'Canonical source metadata differs from STARTING_POINT: ' + row['repository_path'])
        evidence.append(dict(path=row['repository_path'], bytes=row['bytes'], sha256=row['sha256']))
    jobs = api.campaign.campaign_jobs(run / 'provenance/v19_design')
    census = qualify_census(jobs)
    campaign_path = safe(root, RUN_REL + '/design/CAMPAIGN_final01.json'); registration = read(campaign_path)
    require(registration.get('schema') == 'hospital_ies.v20.campaign_registration.v1'
            and registration.get('label') == 'final01' and registration.get('freeze') == FREEZE_NAME
            and registration.get('freeze_sha256') == freeze_sha256
            and registration.get('registered_batches') == 148 and registration.get('registered_pairs') == 406
            and registration.get('no_policy_search') is True and registration.get('original_final12_unopened') is True
            and registration.get('jobs') == [{k: v for k, v in j.items() if k != 'scene'} for j in jobs],
            'Canonical campaign registration differs from exact regenerated census')
    for path in (freeze_path, provenance_path, campaign_path):
        evidence.append(dict(path=path.relative_to(root).as_posix(), bytes=path.stat().st_size, sha256=sha(path)))
    batches = []
    for job in jobs:
        try:
            result = qualify_registered_batch(root, job, frozen, freeze_sha256, api=api)
        except Exception as exc:
            raise ValueError('Final campaign is not qualified at ' + job['name'] + ': ' + str(exc)) from exc
        evidence.extend(result.pop('evidence_files'))
        batches.append(result)
    cross_panel = qualify_cross_panel_batches(jobs, batches)
    # The frozen reporting eligibility is an additional cross-check, not a
    # substitute for exact terminal, registration, duration and ledger checks.
    report = api.report.collect_campaign(run, jobs=jobs, baseline_root=None)
    rows = report['status']
    expected_keys = {(j['name'], j['scene']['id'], a) for j in jobs for a in j['policies']}
    require(len(rows) == 406 and {(r['batch'], r['scenario_id'], r['alias']) for r in rows} == expected_keys,
            'Frozen collector canonical pair denominator differs')
    flags = ('registration_valid', 'result_bound', 'batch_terminal_bound', 'execution_qualified', 'comparison_eligible',
             'actual_started', 'actual_finished', 'registered_full_complete')
    require(all(r.get('status') == 'completed' and all(r.get(k) is True for k in flags)
                and r.get('start_event_count') == r.get('finish_event_count') == 1
                and r.get('episode_audit_status') == 'passed' and r.get('error') is None and r.get('analysis_error') is None
                for r in rows), 'Frozen reporting qualification contains incomplete or ineligible actual pairs')
    require(source == api.runner.source_binding() and tests == api.gate.test_binding()
            and inventory == api.design.runtime_input_inventory(root), 'Scientific source/tests/inputs changed during publication qualification')
    deduplicated = {}
    for row in evidence:
        if row['path'] in deduplicated:
            require(row == deduplicated[row['path']], 'Evidence changed during repeated reads')
        deduplicated[row['path']] = row
    evidence = [deduplicated[key] for key in sorted(deduplicated)]
    for row in evidence:
        path = safe(root, row['path'])
        require(path.stat().st_size == row['bytes'] and sha(path) == row['sha256'], 'Compact evidence changed during qualification: ' + row['path'])
    proof = dict(schema='hospital_ies.v20.publication_final_campaign_qualification.v1', status='passed',
        qualified_utc=datetime.now(timezone.utc).isoformat(), scientific_root=str(root), model_id=MODEL,
        gate_sha256=sha(Path(__file__)), freeze_sha256=freeze_sha256, freeze_signature=frozen['signature'],
        source_binding_sha256=digest(source), test_binding_sha256=digest(tests), runtime_inputs_sha256=digest(inventory),
        actual_complete_batches=148, actual_complete_pairs=406, actual_started_attempts=406, actual_finished_attempts=406,
        failed_or_unknown_canonical_pairs=0, exact_registered_duration_and_service_ledger=True,
        actual_reset_matching=True, actual_applied_streams_recomputed=True,
        policy_dependent_injection_states_preserved=True, normal_injection_not_applicable_preserved=True,
        census=census, batch_proofs=batches, cross_panel_matching=cross_panel,
        evidence_files=evidence, evidence_census_sha256=digest(evidence),
        raw_files_opened=0, annual_values_parsed=0, physical_episodes_started=0,
        sealed_final12='unopened', fixed_eight_backgrounds='seen_fixed_background_recheck',
        different_normal_durations_are_not_pooled=True, zero_service_failure_is_not_a_publication_requirement=True,
        scope='Canonical 148 batches / 406 pairs only; metadata qualification, not a new physical replay or post-push proof')
    proof['signature'] = digest(proof)
    return proof
