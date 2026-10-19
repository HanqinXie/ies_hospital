"""Register a fixed seen-background energy comparison; never run physics.

Default/--check is read-only. Only --register calls the existing immutable
runner.register. Source, model and policy bindings are checked before any write.
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[3]
RUN = ROOT / 'outputs/v19_model_closeout_20261018_run01'
REGISTRY = RUN / 'design/ENERGY_TRADEOFF_PREREGISTRATION.json'
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]

from tools.model_closeout import data_design, runner
from tools.state_coordinated.data_design import seal_scene
from tools.engineering_model.data_design import PHYSICAL_FIELDS

DURATIONS = (('short', 120.), ('medium', 360.), ('long', 780.))
SOURCES = ((40, 0), (100, 4))
POLICIES = ('C19', 'C18_M19')
FAULT_TARGETS = {'GRID', 'GT_1', 'GT_2', 'GT_3'}


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def digest(value):
    return data_design.digest(value)


def physical(scene):
    return {key: scene.get(key) for key in PHYSICAL_FIELDS}


def build_preregistration():
    """Build metadata only; no annual rows, result files or trajectories read."""
    config_path = RUN / 'M19_MODEL_CONFIG.json'
    config = read(config_path)
    _, resolved = runner.model_contract(runner.MODEL_ID, config)
    specs = [copy.deepcopy(runner.policy_specs()[key]) for key in POLICIES]
    scenes, rows, bindings = [], [], {}
    for day, index in SOURCES:
        path = ROOT / ('outputs/v18_execution_recovery_20261018_run01/'
            f'experiments/long_final_r1_{index:03d}/manifest.json')
        source_manifest = read(path)
        original = copy.deepcopy(source_manifest['scenarios'][0])
        assert original['source_day_index'] == day
        assert original['diesel_design'] == 'all_GT_lost'
        assert set(original['faults']) == FAULT_TARGETS
        assert original['fault_start_minute'] == 32.5
        assert original['fault_duration_minutes'] == 780.
        assert original['duration_steps'] * original['step_minutes'] == 1065.
        relative = path.relative_to(ROOT).as_posix()
        bindings[relative] = dict(sha256=sha(path), signature=source_manifest['signature'])
        for label, duration in DURATIONS:
            scene = copy.deepcopy(original)
            scene['fault_duration_minutes'] = duration
            event_changes = []
            for number, event in enumerate(scene['exogenous_events']):
                if event['kind'] == 'device_fault':
                    assert event['device'] in FAULT_TARGETS and event['availability'] == 0.
                    assert event['start_minute'] == 32.5 and event['end_minute'] == 812.5
                    event['end_minute'] = 32.5 + duration
                    if duration != 780.:
                        event_changes.append(dict(index=number, device=event['device'],
                            field='end_minute', original=812.5, new=32.5 + duration))
            # Existing demand-shock timing and magnitude are intentionally fixed.
            assert [e for e in scene['exogenous_events'] if e['kind'] != 'device_fault'] == [
                e for e in original['exogenous_events'] if e['kind'] != 'device_fault']
            altered_physics = {k for k in PHYSICAL_FIELDS if scene.get(k) != original.get(k)}
            assert altered_physics <= {'fault_duration_minutes', 'exogenous_events'}
            scene.update(experiment_role='development', requires_policy_freeze=False,
                selection_allowed=True, fit_allowed=False, development_background=True,
                design_variant=f'v19_energy_duration_d{day:03d}_{int(duration)}min',
                v19_source_scene_id=original['id'], v19_seen_regression=False)
            scene = seal_scene(scene)
            data_design.validate_sealed_support(scene)
            assert scene['full_source_support'] == original['full_source_support']
            assert physical(scene) == physical({**original,
                'fault_duration_minutes': duration, 'exogenous_events': scene['exogenous_events']})
            last_event = max(e['end_minute'] for e in scene['exogenous_events'])
            assert 1065. - last_event >= 240.
            key = f'd{day:03d}_{label}_{int(duration)}'
            scenes.append(scene)
            rows.append(dict(key=key, day=day, duration_label=label,
                outage_duration_minutes=duration, fault_interval_minutes=[32.5, 32.5 + duration],
                all_exogenous_events_end_minute=last_event,
                tail_after_fault_minutes=1065. - (32.5 + duration),
                tail_after_all_exogenous_events_minutes=1065. - last_event,
                retained_demand_shock=copy.deepcopy(original['shock']),
                retained_demand_shock_interval_minutes=[original['shock_start_minute'],
                    original['shock_start_minute'] + original['shock_duration_minutes']],
                source_manifest=relative, source_manifest_sha256=sha(path),
                original_scene_id=original['id'], original_scene_sha256=digest(original),
                new_scene_id=scene['id'], new_scene_sha256=digest(scene),
                physical_scene_sha256=digest(physical(scene)),
                changed_physical_fields=sorted(altered_physics), event_changes=event_changes,
                metadata_fields_changed=sorted(k for k in set(original) | set(scene)
                    if original.get(k) != scene.get(k) and k not in altered_physics),
                reuse_candidate=('outputs/v19_model_closeout_20261018_run01/'
                    'experiments/dev_long_r1_000') if day == 40 and duration == 780. else None))
    value = dict(schema='hospital_ies.v19.energy_tradeoff_preregistration.v1',
        created_utc=datetime.now(timezone.utc).isoformat(),
        status='preregistered_metadata_only_no_new_run_results',
        script_sha256=sha(__file__), model_id=runner.MODEL_ID, model_config=resolved,
        model_config_file=config_path.relative_to(ROOT).as_posix(),
        model_config_file_sha256=sha(config_path), policy_keys=list(POLICIES), policy_specs=specs,
        policy_specs_sha256=digest(specs), design_source_binding=runner.source_binding(),
        design_source_binding_semantics='Authoring snapshot only. Registration requires explicit current source digest; reuse requires exact current source equality. No policy/model configuration changes allowed.',
        source_metadata_bindings=bindings, scenes=scenes, comparisons=rows,
        comparison_window=dict(name='registered_full', interval_minutes=[0., 1065.],
            semantics='All energy/service/cost/emissions comparisons use this same complete window; legacy primary/full600 labels are not comparison windows.'),
        scope=dict(backgrounds='day40/day100 disclosed seen engineering backgrounds',
            no_independence_or_blind_claim=True, original_final12_unopened=True,
            changed_exogenous_parameters='Only common GRID+GT1/2/3 fault duration/end. Day100 demand shock remains e+.9,c+.25 during32.5..452.5; all other physical/init/permission/protection/load support unchanged.',
            no_future_outage_endpoint_in_policy=True, no_new_policy_or_parameter=True,
            full_support_and_initialization_unchanged=True, minimum_recovery_tail_minutes=240.,
            new_physical_episodes_started_by_preregistration=0),
        attempt_plan=dict(total_comparison_pairs=12, maximum_new_actual_attempts=12,
            maximum_reused_pairs=2, fewer_new_attempts_only_if_strict_reuse_qualifies=True,
            failures_and_unknowns_retained=True, quotas_are_not_targets=True),
        reuse_preconditions=['Only day40 duration780 candidate dev_long_r1_000; day100 dev_long_r1_001 retains oneGT and is ineligible.',
            'Exact current source binding, resolved model config, policy config, physical scene projection, input/config byte inventory and1065min window.',
            'Terminal status completed, no analysis_error/error, observed_until1065, unknown_tail null/zero, immutable RESULT SHA matches finished attempt journal.',
            'Recorded raw exists and raw SHA is present; no raw opened by this registration script. Keep original attempt and cite result/manifest hashes; do not clone or relabel raw.',
            'A missing/failed/incomplete/different-source candidate causes a fresh immutable registration for that requested policy; never upgrades old attempt status.'],
        runtime_prerequisites=['Complete and validate the common-core regression gate before physical runs.',
            'Run --check after any source change, then pass its source_binding_digest explicitly to --register.',
            'Use a fresh registration prefix; existing batch directories or receipt paths are rejected.',
            'Root executes registration and physical runner separately; this script never calls run_batch or constructs an environment.'],
        requested_metrics=['P0/P1 e,h,c ENS and material/longest/recurrence',
            'P2/P3 actual service and ENS by carrier', 'critical support and exhaustion times',
            'DG actual electric/shaft/loss/rotor ledger and unchanged costs',
            'same registered operational CO2 accounting with conditional DG limits'])
    value['signature'] = digest(value)
    return value


def validate_registration_inputs(value, expected_source_digest=None):
    assert value['signature'] == digest({k:v for k,v in value.items() if k != 'signature'})
    assert value['script_sha256'] == sha(__file__)
    assert value['model_config_file_sha256'] == sha(ROOT/value['model_config_file'])
    _, config = runner.model_contract(value['model_id'], read(ROOT/value['model_config_file']))
    assert config == value['model_config']
    assert [runner.policy_specs()[key] for key in POLICIES] == value['policy_specs']
    for relative, reference in value['source_metadata_bindings'].items():
        assert sha(ROOT/relative) == reference['sha256']
    for row, scene in zip(value['comparisons'], value['scenes']):
        assert row['new_scene_id'] == scene['id'] and row['new_scene_sha256'] == digest(scene)
        assert row['physical_scene_sha256'] == digest(physical(scene))
        data_design.validate_sealed_support(scene)
    binding = runner.source_binding()
    if expected_source_digest is not None and digest(binding) != expected_source_digest:
        raise ValueError('Current source differs from explicit registration digest; keep prior attempts')
    return binding


def reusable_result(row, scene, spec, binding, config):
    if row['reuse_candidate'] is None:
        return None
    directory = ROOT/row['reuse_candidate']
    if not all((directory/name).exists() for name in ('plan.json','manifest.json','ATTEMPTS.jsonl')):
        return None
    plan, manifest = read(directory/'plan.json'), read(directory/'manifest.json')
    if (plan.get('source_sha256') != binding or plan.get('model_id') != runner.MODEL_ID
            or plan.get('model_config') != config or spec not in plan.get('policy_specs',[])
            or sha(directory/'manifest.json') != plan.get('manifest_sha256')):
        return None
    old_scene = next((s for s in manifest['scenarios'] if physical(s) == physical(scene)), None)
    if old_scene is None:
        return None
    for entry in manifest['files']:
        if entry['path'].startswith(('inputs/','configs/')) and sha(ROOT/entry['path']) != entry['sha256']:
            return None
    result_path = directory/'pairs'/(old_scene['id']+'__'+spec['policy_id'])/'RESULT.json'
    if not result_path.exists():
        return None
    result = read(result_path); analysis = result.get('analysis') or {}
    if (result.get('status') != 'completed' or result.get('error') or result.get('analysis_error')
            or analysis.get('observed_until_minute') != 1065.
            or analysis.get('requested_end_minute') != 1065.
            or analysis.get('unknown_tail_minutes') not in (None,0,0.,[])
            or result.get('manifest_signature') != manifest['signature']
            or result.get('model_config_signature') != manifest['model_config_signature']
            or not result.get('raw_sha256') or not result.get('raw_path')
            or not Path(result['raw_path']).is_file()):
        return None
    terminal = [json.loads(line) for line in (directory/'ATTEMPTS.jsonl').read_text(encoding='utf-8').splitlines()
        if line.strip()]
    terminal = [r for r in terminal if r.get('event') == 'finished'
        and r.get('scenario_id') == old_scene['id'] and r.get('policy_id') == spec['policy_id']]
    if (len(terminal) != 1 or terminal[0].get('status') != 'completed'
            or terminal[0].get('result_sha256') != sha(result_path)):
        return None
    return dict(kind='strict_existing_attempt_reference', requested_scene_id=scene['id'],
        original_scene_id=old_scene['id'], policy_id=spec['policy_id'],
        result_path=result_path.relative_to(ROOT).as_posix(), result_sha256=sha(result_path),
        manifest_sha256=sha(directory/'manifest.json'), raw_sha256=result['raw_sha256'],
        raw_opened=False, original_attempt_retained=True, comparison_window_minutes=[0.,1065.])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--check', action='store_true')
    mode.add_argument('--register', action='store_true')
    parser.add_argument('--prefix', default='energy_tradeoff_r1')
    parser.add_argument('--source-binding-digest')
    args = parser.parse_args()
    if not re.fullmatch(r'[a-zA-Z0-9_]+', args.prefix):
        raise ValueError('Prefix must be a plain unique batch name')
    value = read(REGISTRY)
    binding = validate_registration_inputs(value, args.source_binding_digest)
    print(json.dumps(dict(mode='register' if args.register else 'read_only_check',
        source_binding_digest=digest(binding),
        source_changed_since_design=binding != value['design_source_binding'],
        scenarios=len(value['scenes']), comparison_pairs=12, physical_runs_started=0), ensure_ascii=False), flush=True)
    if not args.register:
        return
    if args.source_binding_digest is None:
        raise ValueError('--register requires the explicit current --source-binding-digest from --check')
    receipt_path = RUN/'design'/('ENERGY_TRADEOFF_REGISTRATION_'+args.prefix+'.json')
    if receipt_path.exists():
        raise FileExistsError('Registration receipt is immutable; use a new prefix')
    pending=[]; reused=[]
    for row, scene in zip(value['comparisons'], value['scenes']):
        missing=[]
        for spec in value['policy_specs']:
            found = reusable_result(row,scene,spec,binding,value['model_config'])
            if found is None: missing.append(spec)
            else: reused.append(found)
        if missing:
            directory = RUN/'experiments'/(args.prefix+'_'+row['key'])
            if directory.exists():
                raise FileExistsError('Previous batch exists; no overwrite: '+str(directory))
            pending.append((row,scene,missing,directory))
    registered=[]
    for row, scene, specs, directory in pending:
        proposal = data_design.panel('energy_tradeoff_'+row['key'],[scene],
            bindings=value['source_metadata_bindings'],evaluation_role='development')
        proposal.pop('signature',None)
        proposal.update(energy_tradeoff_preregistration_sha256=sha(REGISTRY),
            energy_tradeoff_comparison_window=copy.deepcopy(value['comparison_window']),
            energy_tradeoff_seen_background=True)
        proposal['signature']=digest(proposal)
        plan = runner.register(proposal,directory,specs,model_id=value['model_id'],
            model_config=value['model_config'],phase='energy_tradeoff_development')
        registered.append(dict(directory=directory.relative_to(ROOT).as_posix(),
            scenario_id=scene['id'], registered_pairs=plan['registered_pairs'],
            plan_sha256=sha(directory/'plan.json'), manifest_sha256=sha(directory/'manifest.json')))
        print(json.dumps(registered[-1],ensure_ascii=False),flush=True)
    receipt=dict(schema='hospital_ies.v19.energy_tradeoff_registration.v1',
        preregistration_sha256=sha(REGISTRY), source_binding_digest=digest(binding),
        source_binding=binding, model_config=value['model_config'],policy_specs=value['policy_specs'],
        registered=registered,reused=reused,physical_runs_started=0,
        comparison_pairs=len(reused)+sum(r['registered_pairs'] for r in registered),
        previous_failures_and_unknowns_retained=True)
    assert receipt['comparison_pairs'] == 12
    with receipt_path.open('x',encoding='utf-8') as stream:
        json.dump(receipt,stream,ensure_ascii=False,indent=2)
        stream.write('\n')


if __name__ == '__main__':
    main()
