"""Finite v14 metadata on disclosed roots, with sealed-support exclusion.

Proposal construction reads signed definitions only, never physical annual
rows, trajectory raws or outcomes. Manifest construction reuses byte inventory
from the legacy builder and binds the explicit v14 model separately.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from collections import Counter
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from tools.coordinated_dispatch import data_design as legacy

digest = legacy.digest
validate_observation_support = legacy.validate_observation_support
PHYSICAL_FIELDS = legacy.PHYSICAL_FIELDS
SEALED_DAYS = legacy.SEALED_DAYS
SCHEMA = 'hospital_ies.engineering_metadata.v14.v1'
ENGINEERING_MODEL_CONTRACT_ID = 'hospital_ies.engineering_model.v14.v1'
ACTION_SCHEMA = 'engineering_model.engineering_joint.v14'
PLANNING_INTERFACE = 'engineering_model.ordinary_joint_command.v14'
RESOURCE_LIMITS = dict(max_actual_attempts=336, max_experiment_wall_hours=8,
                       max_main_dispatch_configurations=3, ceilings_not_quotas=True)
V13_DESIGN = Path('outputs/v13_executed_service_20261017_run01/design')
SOURCE_PATHS = (V13_DESIGN / 'DEVELOPMENT_PROPOSAL.json',
                V13_DESIGN / 'CONFIRMATION_PROPOSAL.json')
CORE_CLASSES = ('L1', 'L2', 'L3', 'L4b')
FINAL_RECIPE = dict(id='v14_phase_minus_6p25', phase_delta_minutes=-6.25,
                    shock_scale=1., duration_scale=1.)
NORMAL_SOURCE_IDS = ('v13_confirm_fd05755beda001695e3284f5',
                     'v13_confirm_23c13f8480c004a5c3e96adc',
                     'v13_dev_855a255879c25d138e593434')
COUNTEREXAMPLE_IDS = ('v13_dev_bf706b06d55bd34cf598f317',
                      'v13_dev_c8533f9d38c2794e63855d5b',
                      'v13_dev_beb7909e5e1b15dc70df2378')


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def validate_sealed_support(scene):
    """Check all real source/endpoint hours and conservative 60 minute embargo."""
    legacy._seal_check(scene)
    support = scene['full_source_support']
    start, end = support['raw_start_minute'] - 60., support['raw_end_minute'] + 60.
    if any(start < (day + 1) * 1440. and day * 1440. < end for day in SEALED_DAYS):
        raise ValueError('Sealed original final12 overlaps complete support/embargo')
    return copy.deepcopy(support)


def _finish(value):
    value = copy.deepcopy(value)
    value.pop('signature', None)
    value.update(schema=SCHEMA, study_name='hospital_ies_engineering_model_v14',
        strict_final_gate='not_verified', original_final12_evaluated=False,
        original_final_release_authorized=False, sealed_final_days=list(SEALED_DAYS),
        new_independent_background_count=0, hospital_count=1, source_year_count=1,
        statistical_iid_claim=False, source_load_values_read=False,
        raw_or_result_files_read=False, physical_episodes_started=0,
        resource_limits=copy.deepcopy(RESOURCE_LIMITS))
    value['signature'] = digest(value)
    return value


def _physical(scene):
    return {key: scene.get(key) for key in PHYSICAL_FIELDS}


def _scene(source, cohort, *, phase=False, long_run=False):
    scene = legacy._perturb(source, FINAL_RECIPE) if phase else copy.deepcopy(source)
    scene.update(source_v13_scenario_id=source['id'],
        source_v13_role_metadata={k: copy.deepcopy(source.get(k)) for k in
            ('experiment_role', 'analysis_cohort', 'sampling_role', 'selection_allowed', 'requires_policy_freeze')},
        sample_schema_version=SCHEMA, experiment_role='confirmation' if phase else 'development',
        analysis_cohort='v14_' + cohort, sampling_cohort='v14_' + cohort, purpose='v14_' + cohort,
        selection_allowed=not phase, requires_policy_freeze=phase, fit_allowed=False,
        is_final_blind=False, previously_exposed_v13=True, new_independent_background=False,
        demand_background='previously_exposed_same_hospital_year',
        source_outcomes_used_for_selection=not phase, is_new_physical_spec=phase,
        same_background_new_perturbation=phase, new_registered_support_window=long_run,
        sampling_role='frozen_new_coordinates_on_seen_root' if phase else 'disclosed_engineering_development',
        old_results_read_only=True, condition_id=copy.deepcopy(source.get('condition_id')))
    if long_run:
        scene.update(duration_steps=96, step_minutes=15., score_interval_minutes=[60., 1320.],
            tail_interval_minutes=[1320., 1440.],
            long_run_window_semantics='Original primary/full600 unchanged; long1440/extension600_1440 reported separately',
            normal_is_new_perturbation=False, is_new_physical_spec=True,
            physical_duplicate_of=None,
            initialization_semantics='Original repeated first-hour algebraic initialization; not chronological prehistory')
        scene['full_source_support'] = validate_observation_support(scene)
        scene['sampling_role'] = 'long_normal_seen_root_new_support'
        scene['time_block_id'] = 'v14_long_support:' + digest(scene['full_source_support'])[:24]
    scene['physical_scenario_signature'] = digest(_physical(scene))
    scene['id'] = 'v14_' + cohort + '_' + digest([source['id'], scene['physical_scenario_signature']])[:24]
    if phase:
        scene['exogenous_stream_id'] = 'v14_exogenous:' + digest([scene['root_family_id'], scene['exogenous_events']])
    if phase or long_run:
        scene['matched_group_id'] = scene['design_cell_id'] = 'v14_cell:' + digest(
            [scene['root_family_id'], FINAL_RECIPE if phase else 'long1440'])[:24]
    validate_sealed_support(scene)
    return scene


def _panel(name, scenes, roots, bindings, *, new_specs=0):
    selected_roots = []
    for rid in sorted({s['root_family_id'] for s in scenes}):
        record = copy.deepcopy(roots[rid])
        support = next(s['full_source_support'] for s in scenes if s['root_family_id'] == rid)
        record.update(full_source_support=copy.deepcopy(support),
                      raw_start_minute=support['raw_start_minute'], raw_end_minute=support['raw_end_minute'],
                      embargo_start_minute=support['raw_start_minute']-60,
                      embargo_end_minute=support['raw_end_minute']+60,
                      new_independent_background=False, previously_exposed_v13=True)
        selected_roots.append(record)
    if len({s['id'] for s in scenes}) != len(scenes):
        raise ValueError('Duplicate v14 scene identity')
    return _finish(dict(panel=name, scenarios=scenes, roots=selected_roots,
        analysis_class_counts=dict(Counter(s['analysis_class'] for s in scenes)),
        source_metadata_bindings=bindings, new_physical_spec_count=new_specs,
        source_outcomes_used_for_selection=name != 'confirmation',
        incident_denominator_classes=list(CORE_CLASSES), separately_reported_classes=['L4a', 'N2'],
        scoring_windows={'primary': [60., 420.], 'full': [0., 600.]},
        additional_scoring_windows={'long1440': [0., 1440.], 'extension_600_1440': [600., 1440.]}
            if name == 'long_normal' else {},
        independence='Previously disclosed same-hospital/year roots; new coordinates/support are not unseen backgrounds'))


def build_proposals(root=ROOT):
    root = Path(root).resolve(); definitions, roots, bindings = {}, {}, {}
    for relative in SOURCE_PATHS:
        path = root / relative
        value = legacy._signed(path)
        if value.get('sealed_final_days') != list(SEALED_DAYS):
            raise ValueError('Original sealed final12 identity registry differs')
        bindings[relative.as_posix()] = dict(sha256=_sha(path), signature=value['signature'])
        for scene in value['scenarios']:
            definitions[scene['id']] = scene
        for record in value['roots']:
            roots[record['root_family_id']] = record
    core = [s for s in definitions.values() if s['id'].startswith('v13_dev_') and
            s['source_day_index'] in (47, 129, 212) and s['analysis_class'] in CORE_CLASSES]
    extension = [s for s in definitions.values() if s['id'].startswith('v13_dev_') and
                 s['source_day_index'] in (129, 212) and s['analysis_class'] == 'L4a']
    chosen = [*core, *(definitions[sid] for sid in COUNTEREXAMPLE_IDS),
              *(definitions[sid] for sid in NORMAL_SOURCE_IDS), *extension]
    if len(chosen) != 20:
        raise ValueError('Expected exactly twenty registered engineering specifications')
    development = [_scene(s, 'development') for s in chosen]
    long_normal = [_scene(definitions[sid], 'long_normal', long_run=True) for sid in NORMAL_SOURCE_IDS]
    confirmation = [_scene(s, 'confirmation', phase=True) for s in core if s['source_day_index'] in (129, 212)]
    known = {digest(_physical(s)) for s in definitions.values()}
    # Include prior coordinate registries without ever inspecting trajectory outcomes.
    for name in ('EXPOSED_V11_POOL.json', 'COMBINED_FROZEN_PROPOSAL.json'):
        path = root / 'outputs/v12_service_switch_20261017_run01/design' / name
        value = legacy._signed(path)
        bindings[path.relative_to(root).as_posix()] = dict(sha256=_sha(path), signature=value['signature'])
        known.update(digest(_physical(s)) for s in value['scenarios'])
    if len(confirmation) != 8 or any(digest(_physical(s)) in known for s in confirmation):
        raise ValueError('Final engineering coordinates are not eight new specifications')
    return dict(development=_panel('development', development, roots, bindings),
        long_normal=_panel('long_normal', long_normal, roots, bindings, new_specs=3),
        confirmation=_panel('confirmation', confirmation, roots, bindings, new_specs=8))


def build_manifest(proposal, source_sha256, model_id, model_config, root=ROOT, policies=None):
    if model_id not in ('M0', 'M14_engineering'):
        raise ValueError('Unsupported v14 execution model')
    if not isinstance(model_config, dict):
        raise ValueError('Explicit model configuration mapping required')
    json.dumps(model_config, allow_nan=False)
    for scene in proposal['scenarios']:
        validate_sealed_support(scene)
    # Legacy metadata construction/inventory is model-neutral; its old M0 label
    # is replaced explicitly here before a fresh signature is created.
    options = {} if policies is None else dict(policies=tuple(policies))
    manifest = legacy.build_manifest(proposal['scenarios'], proposal['roots'],
        source_sha256=source_sha256, planning_interface_id=PLANNING_INTERFACE, action_schema=ACTION_SCHEMA,
        workspace_root=root, execution_model_id='M0', **options)
    manifest.pop('signature', None)
    manifest.update(manifest_version=SCHEMA + '.manifest', execution_model_id=model_id,
        engineering_model_contract_id=ENGINEERING_MODEL_CONTRACT_ID,
        planning_interface_id=PLANNING_INTERFACE, schemas=dict(manifest['schemas'], action=ACTION_SCHEMA),
        model_config=copy.deepcopy(model_config), model_config_signature=digest(model_config),
        source_proposal_signature=proposal['signature'], panel=proposal['panel'],
        source_sha256=copy.deepcopy(source_sha256))
    manifest.update(legacy_execution_contract_id=manifest['execution_contract_id'],
        legacy_assumption_set_id=manifest['assumption_set_id'], legacy_simulator_hash=manifest['simulator_hash'])
    manifest['simulator_hash'] = digest(source_sha256)
    if model_id != 'M0':
        suffix = '__v14_' + manifest['model_config_signature'][:16]
        manifest['execution_contract_id'] += suffix
        manifest['assumption_set_id'] += suffix
    # Native exporter IDs now distinguish the actual M14 assumption/actuator
    # contract; legacy identities remain evidence fields, never M14 aliases.
    return _finish(manifest)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--root', type=Path, default=ROOT)
    args = parser.parse_args(argv)
    destination = args.output.resolve()
    if destination.drive.lower() == 'c:':
        raise ValueError('Large project outputs must use the D/E workspace')
    panels = build_proposals(args.root)
    names = {key: key.upper() + '_PROPOSAL.json' for key in panels}
    expected = [*names.values(), 'DATA_DESIGN_RECEIPT.json']
    if any((destination / name).exists() for name in expected):
        raise FileExistsError('Existing proposal bytes are read-only; use a fresh directory')
    destination.mkdir(parents=True, exist_ok=True)
    for key, name in names.items():
        with (destination / name).open('x', encoding='utf8') as stream:
            json.dump(panels[key], stream, ensure_ascii=False, indent=2, allow_nan=False)
    receipt = dict(schema=SCHEMA + '.receipt', generator_sha256=_sha(__file__),
        output_sha256={name: _sha(destination / name) for name in names.values()},
        source_metadata_bindings=panels['development']['source_metadata_bindings'],
        panel_spec_counts={key: len(value['scenarios']) for key, value in panels.items()},
        annual_physical_rows_parsed=0, raw_or_result_files_read=0, physical_episodes_started=0,
        strict_final_gate='not_verified', original_final12_evaluated=False)
    with (destination / 'DATA_DESIGN_RECEIPT.json').open('x', encoding='utf8') as stream:
        json.dump(receipt, stream, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps(receipt, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
