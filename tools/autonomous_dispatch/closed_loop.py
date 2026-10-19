"""Version-bound joint-policy batches reusing immutable original attempts."""
from __future__ import annotations
import copy
import json
import math
from functools import lru_cache
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from tools.dispatch_closeout import closed_loop as previous
from tools.state_dispatch import closed_loop as v1
from .contracts import JointDispatchPolicy
from .environment import SelectiveHospitalIESEnv
from .episode import run_policy_episode

SCHEMA = 'autonomous_dispatch.matched_full_feedback.v1'
AUDIT_SCHEMA = 'autonomous_dispatch.matched_batch_audit.v1'
ANALYSIS_SCHEMA = 'autonomous_dispatch.audited_saved_raw_analysis.v1'
SOURCE_FILES = tuple(sorted(set(previous.SOURCE_FILES) | {
    'src/hospital_ies/pipeline.py',
    'outputs/v43_p2b_20261013_run01/experiment/global_manifest.json',
} | {
    f'tools/autonomous_dispatch/{name}.py' for name in
    ('planner', 'contracts', 'environment', 'selective_data', 'episode', 'model_order', 'closed_loop')}))
PHASES = previous.PHASES
_sha, _read, _new_json, _isolated = previous._sha, previous._read, previous._new_json, previous._isolated
window_targets = previous.window_targets
_command_counts = previous._command_counts


def _source_binding():
    return {name: _sha(ROOT / name) for name in SOURCE_FILES}


def _factory(spec):
    if spec['kind'] == 'joint':
        return JointDispatchPolicy(copy.deepcopy(spec['config']), spec['policy_id'])
    return previous._factory(spec)


def _check_specs(policy_specs):
    specs = copy.deepcopy(list(policy_specs))
    ids = [s.get('policy_id') for s in specs]
    if not specs or len(specs) > 80 or len(ids) != len(set(ids)) or any(not isinstance(x, str) or not x for x in ids):
        raise ValueError('Distinct registered policy IDs required')
    secure = {'policy_id': 'secure_milp', 'kind': 'secure'}
    priority = {'policy_id': 'priority_milp', 'kind': 'priority'}
    if secure not in specs or priority not in specs:
        raise ValueError('Original priority and secure baselines required')
    for spec in specs:
        if spec['kind'] in {'priority', 'secure'}:
            if spec not in (priority, secure):
                raise ValueError('Original baseline identity changed')
        elif spec['kind'] in {'joint', 'state_dispatch'}:
            if set(spec) != {'policy_id', 'kind', 'config'} or spec['policy_id'] in {'priority_milp', 'secure_milp'}:
                raise ValueError('Versioned policy requires a distinct identity and explicit config')
            _factory(spec)
        else:
            raise ValueError('Unknown autonomous-dispatch policy kind')
    return specs


def allowed_hours_for(manifest, scenario_ids):
    ids = set(scenario_ids)
    scenes = [s for s in manifest['scenarios'] if s['id'] in ids]
    if len(scenes) != len(ids):
        raise ValueError('Registered scenario metadata missing')
    hours = set()
    for scene in scenes:
        first = int(scene['start_hour'])
        last = first + int(math.ceil(int(scene['duration_steps']) * float(scene.get('step_minutes', 15.)) / 60.))
        hours.update(range(first, last + 1))
    return sorted(hours)


@lru_cache(maxsize=1)
def _sealed_days():
    metadata = _read(ROOT / 'outputs/v43_p2b_20261013_run01/experiment/global_manifest.json')
    return sorted({int(row['time_block_id'].rsplit('_', 1)[1]) for row in metadata['p2b_time_blocks'] if row['reserved_only']})


def _environment_factory(plan, manifest):
    hours = allowed_hours_for(manifest, plan['scenario_ids'])
    sealed = _sealed_days()
    if any((h - 1) // 24 in sealed for h in hours):
        raise ValueError('Registered batch overlaps sealed final data')
    model_id = manifest.get('execution_model_id', 'M0')
    def create(root, *, execution_contract):
        return SelectiveHospitalIESEnv(root, execution_contract=execution_contract,
            allowed_hours=hours, sealed_days=sealed, model_id=model_id)
    return create


def _unbound_environment(*args, **kwargs):
    raise ValueError('Use a plan-bound selective environment factory')


def _payload_binding(payload, scenario, manifest):
    result = previous._payload_binding(payload, scenario, manifest)
    access = payload.get('input_access_receipt') or {}
    result['checks']['execution_model_equal'] = payload.get('execution_model_id') == manifest.get('execution_model_id', 'M0')
    result['checks']['selective_input_receipt_present'] = bool(access)
    sealed = _sealed_days()
    result['checks']['sealed_support_excluded'] = (access.get('sealed_numeric_rows_parsed') == 0
        and access.get('sealed_days') == sealed
        and not any((int(h) - 1) // 24 in sealed for h in access.get('allowed_hours', [])))
    result['passed'] = all(result['checks'].values())
    return result


def check_confirmation_freeze(plan, manifest, root=ROOT):
    if plan.get('phase') != 'confirmation':
        return
    preference_path = plan.get('preference_path')
    if not preference_path:
        raise ValueError('Confirmation requires a real policy freeze receipt')
    preference = _read(preference_path)
    gate = preference.get('confirmation_gate') or {}
    path_value = gate.get('policy_freeze_path')
    if not path_value or not gate.get('policy_freeze_sha256'):
        raise ValueError('Confirmation requires a bound policy freeze receipt')
    path = Path(path_value)
    if not path.is_absolute():
        path = Path(root) / path
    if not path.is_file() or _sha(path) != gate['policy_freeze_sha256']:
        raise ValueError('Policy freeze receipt missing or changed')
    freeze = _read(path)
    if (freeze.get('source_sha256') != _source_binding()
            or freeze.get('policy_specs') != plan.get('policy_specs')
            or freeze.get('execution_model_id') != manifest.get('execution_model_id', 'M0')
            or freeze.get('development_completed') is not True):
        raise ValueError('Confirmation method/source/model differs from completed-development freeze')


def _runner_namespace(environment=None):
    namespace = previous._runner_namespace()
    namespace.update(ROOT=ROOT, SCHEMA=SCHEMA, _check_specs=_check_specs, _source_binding=_source_binding,
        _factory=_factory, verify_regime_batch=verify_batch, _recover_interrupted=_recover_interrupted,
        _summary=_summary, HospitalIESEnv=environment or _unbound_environment,
        run_policy_episode=run_policy_episode, _payload_binding=_payload_binding)
    return namespace


def register_batch(manifest_path, plan_path, *, scenario_ids, policy_specs, preference_path, phase):
    manifest = _read(manifest_path)
    check_confirmation_freeze(dict(phase=phase, preference_path=str(preference_path), policy_specs=policy_specs), manifest)
    return _isolated(v1.regime_runner.register_regime_batch, _runner_namespace())(
        manifest_path, plan_path, scenario_ids=scenario_ids, policy_specs=policy_specs,
        preference_path=preference_path, phase=phase)


def verify_batch(plan_path):
    plan, manifest = _isolated(v1.regime_runner.verify_regime_batch, _runner_namespace())(plan_path)
    check_confirmation_freeze(plan, manifest)
    _environment_factory(plan, manifest)  # metadata-only sealed-support check
    return plan, manifest


def _recover_interrupted(*args, **kwargs):
    return _isolated(v1.regime_runner._recover_interrupted, _runner_namespace())(*args, **kwargs)


def _summary(*args, **kwargs):
    return _isolated(v1.regime_runner._summary, _runner_namespace())(*args, **kwargs)


def run_batch(plan_path, out_dir, *, max_new_pairs=None):
    plan, manifest = verify_batch(plan_path)
    return _isolated(v1.regime_runner.run_regime_batch, _runner_namespace(_environment_factory(plan, manifest)))(
        plan_path, out_dir, max_new_pairs=max_new_pairs)


def audit_batch(plan_path, results_dir, *, output_json=None, output_markdown=None, include_action_effect=False):
    if include_action_effect:
        raise ValueError('Old six-target projection audit is inapplicable to the versioned joint planner')
    plan, manifest = verify_batch(plan_path)
    # v1.audit_batch is itself an adapter; use its original scorer/auditor directly.
    original = v1.original_audit
    namespace = dict(original.audit_batch.__globals__, ROOT=ROOT, SCHEMA=AUDIT_SCHEMA,
        verify_batch=verify_batch, HospitalIESEnv=_environment_factory(plan, manifest),
        window_targets=window_targets, _payload_binding=_payload_binding,
        _brief_markdown=original._brief_markdown)
    return _isolated(original.audit_batch, namespace)(plan_path, results_dir,
        output_json=output_json, output_markdown=output_markdown, include_action_effect=False)


def analyze_batch(manifest, plan, results, audit, out_dir):
    from .analysis import analyze_batch as analyze
    verify_batch(plan)
    return analyze(manifest, plan, results, audit, out_dir)
