"""Small optional selective physical replay; independent extraction is not yet certified.

This helper is outside the frozen runtime map. It reuses the original package
reader/run completeness rules, never an original absolute plan or annual env.
"""
from __future__ import annotations
import copy
from pathlib import Path
import shutil
import sys
from types import FunctionType

from tools.state_dispatch import portable_replay as previous
from hospital_ies.dataset_manifest import build_manifest, digest

ROOT = previous.ROOT
_backend, _write_new = previous._backend, previous._write_new
ROUTES = {schema: 'tools.autonomous_dispatch.' + module for schema, module in (
    ('autonomous_dispatch.matched_full_feedback.v1', 'closed_loop'),
    ('autonomous_dispatch.contacts.matched_full_feedback.v2', 'closed_loop_v2'),
    ('autonomous_dispatch.margin.matched_full_feedback.v3', 'closed_loop_v3'),
    ('autonomous_dispatch.unscaled.matched_full_feedback.v4', 'closed_loop_v4'),
    ('autonomous_dispatch.standby.matched_full_feedback.v5', 'closed_loop_v5'))}


def _isolated(function, **changes):
    result = FunctionType(function.__code__, dict(function.__globals__, **changes),
                          function.__name__, function.__defaults__, function.__closure__)
    result.__kwdefaults__ = function.__kwdefaults__
    return result


def replay_request(extracted_root, batch_name, *, scenario_ids=None):
    captured = {}
    def preference(package, batch, plan, root):
        captured['package_entries'] = package['entries']
        return previous._preference(package, batch, plan, root)
    request = _isolated(previous.replay_request, ROUTES=ROUTES, _preference=preference)(
        extracted_root, batch_name, scenario_ids=scenario_ids)
    return dict(request, **captured)


def _local_preference(request, backend, output):
    source = Path(request['preference_path']); pref = previous._read(source); frozen_receipt = None
    if request['phase'] == 'confirmation':
        gate = pref.get('confirmation_gate') or {}; expected = gate.get('policy_freeze_sha256')
        candidates = sorted(row['archive_path'] for row in request['package_entries']
                            if expected and row['sha256'] == expected)
        if not candidates:
            raise FileNotFoundError('Original freeze bytes are absent from verified package entries')
        root = Path(request['root'])
        frozen = previous._within(root / previous._archive_name(candidates[0]), root)
        if previous._sha(frozen) != expected:
            raise ValueError('Packaged freeze SHA differs')
        freeze = previous._read(frozen); old = request['source_manifest']
        if (freeze.get('source_sha256') != backend._source_binding()
                or freeze.get('policy_specs') != request['policy_specs']
                or freeze.get('development_completed') is not True
                or freeze.get('execution_model_id') != old.get('execution_model_id', 'M0')
                or freeze.get('numerical_contract_id') != old.get('numerical_contract_id')
                or freeze.get('standby_contract_id') != old.get('standby_contract_id')):
            raise ValueError('Frozen source/spec/model/numerical/standby contract differs')
        local_freeze = output / 'ORIGINAL_POLICY_FREEZE.json'
        shutil.copyfile(frozen, local_freeze)
        if previous._sha(local_freeze) != expected:
            raise ValueError('Copied freeze SHA differs')
        frozen_receipt = dict(packaged_path=candidates[0], local_path=str(local_freeze),
                              original_sha256=expected, copied_sha256=previous._sha(local_freeze))
        pref['confirmation_gate']['policy_freeze_path'] = str(local_freeze)
    local = output / 'REPLAY_PREFERENCE.json'; _write_new(local, pref)
    return local, frozen_receipt


def prepare_replay(extracted_root, output_dir, batch_name, *, scenario_ids=None):
    root, output = Path(extracted_root).resolve(), Path(output_dir).resolve()
    if output.is_relative_to(root):
        raise ValueError('Physical replay output must be outside the immutable extracted package')
    if sys.platform == 'win32' and output.drive.upper() not in {'D:', 'E:'}:
        raise ValueError('Physical replay output must be on D/E')
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise FileExistsError('Portable replay requires a new or empty output directory')
    request = replay_request(root, batch_name, scenario_ids=scenario_ids)
    backend = _backend(request['runner_module'], root)
    old, scenes = request['source_manifest'], request['scenarios']
    manifest = build_manifest(root, scenes, [s['policy_id'] for s in request['policy_specs']],
        copy.deepcopy(old['execution_contract']), copy.deepcopy(old['schemas']), copy.deepcopy(old['options']),
        {s['id']: copy.deepcopy(old['splits'][s['id']]) for s in scenes})
    if (manifest['simulator_hash'] != old['simulator_hash']
            or manifest['execution_contract_id'] != old['execution_contract_id']):
        raise ValueError('Current package physics or execution contract differs from frozen source')
    manifest.pop('signature')
    for key in ('execution_model_id', 'numerical_contract_id', 'standby_contract_id', 'planning_interface_id', 'sealed_final_days'):
        if key in old:
            manifest[key] = copy.deepcopy(old[key])
    manifest.update(manifest_version='autonomous_dispatch.portable_replay.manifest.v1',
        policy_search_final_12_backgrounds_unopened=True, strict_final_gate='not_verified',
        original_final12_evaluated=False, final_evaluation_generated=False,
        portable_origin_manifest_sha256=request['source_manifest_sha256'],
        portable_origin_plan_sha256=request['source_plan_sha256'])
    manifest['signature'] = digest(manifest)
    backend._environment_factory(dict(scenario_ids=request['scenario_ids']), manifest)  # metadata-only seal check
    output.mkdir(parents=True, exist_ok=True)
    preference, freeze_copy = _local_preference(request, backend, output)
    mp, pp = output / 'global_manifest.json', output / 'registered_plan.json'; _write_new(mp, manifest)
    plan = backend.register_batch(mp, pp, scenario_ids=request['scenario_ids'], policy_specs=request['policy_specs'],
        preference_path=preference, phase=request['phase'])
    receipt = dict(root=str(root), batch=batch_name, runner_module=request['runner_module'],
        manifest_path=str(mp), plan_path=str(pp), results_path=str(output / 'results'),
        scenario_ids=request['scenario_ids'], policy_specs=request['policy_specs'], phase=request['phase'],
        preference_path=str(preference), preference_sha256=previous._sha(preference),
        origin_preference_sha256=request['preference_sha256'],
        copied_policy_freeze=freeze_copy,
        origin_manifest_sha256=request['source_manifest_sha256'], origin_plan_sha256=request['source_plan_sha256'],
        manifest_sha256=previous._sha(mp), plan_sha256=previous._sha(pp), plan_signature=plan['signature'],
        registered_pair_count=plan['registered_pair_count'], execution_model_id=manifest.get('execution_model_id', 'M0'),
        numerical_contract_id=manifest.get('numerical_contract_id'), standby_contract_id=manifest.get('standby_contract_id'), episodes_started=0,
        original_absolute_plan_is_provenance_only=True, runtime_rebound_to_current_installation=True,
        resampled_scenarios=False, frozen_physics_hash=manifest['simulator_hash'])
    _write_new(output / 'REPLAY_REGISTRATION.json', receipt)
    return receipt


def run_replay(*args, **kwargs):
    return _isolated(previous.run_replay, prepare_replay=prepare_replay, _backend=_backend)(*args, **kwargs)


def main(argv=None):
    return _isolated(previous.main, ROOT=ROOT, prepare_replay=prepare_replay, run_replay=run_replay)(argv)


if __name__ == '__main__':
    raise SystemExit(main())
