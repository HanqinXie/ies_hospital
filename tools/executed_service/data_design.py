"""Finite v13 metadata panels on already exposed v12 backgrounds.

No annual physical rows, saved results, raw trajectories or environments are
read here. Confirmation is a new-coordinate test, never a new-background claim.
"""
from __future__ import annotations

import argparse
from collections import Counter
import copy
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from tools.coordinated_dispatch import data_design as legacy

digest = legacy.digest
validate_scenario = legacy.validate_scenario
validate_observation_support = legacy.validate_observation_support
PHYSICAL_FIELDS = legacy.PHYSICAL_FIELDS
CLASSES = legacy.CLASSES
VERSION = 'executed_service.v13.data_design.v1'
STUDY = 'hospital_ies_executed_service_v13'
V12_DESIGN = Path('outputs/v12_service_switch_20261017_run01/design')
SOURCE_NAMES = ('TARGETED_DEVELOPMENT_PROPOSAL.json',
                'DISTRIBUTED_DEVELOPMENT_PROPOSAL.json',
                'EXPOSED_V11_POOL.json', 'COMBINED_FROZEN_PROPOSAL.json')
DEVELOPMENT_ADDITIONAL_DAYS = (129, 212)
CONFIRMATION_DAYS = (2, 3, 123, 147, 222, 234, 282, 301)
CONFIRMATION_RECIPES = [
    dict(id='v13_phase_plus_3p75', phase_delta_minutes=3.75, shock_scale=1., duration_scale=1.),
    dict(id='v13_phase_plus_13p75_shock_duration_1p025', phase_delta_minutes=13.75,
         shock_scale=1.025, duration_scale=1.025),
]
RESOURCE_LIMITS = dict(max_registered_pairs=480, max_actual_attempts=480,
    max_new_physical_specs=80, max_major_recipes=1, max_feedback_recipes=1,
    max_wall_hours=8, max_neural_training_fits=0, limits_are_ceilings_not_quotas=True)
ROLE_FIELDS = ('experiment_role', 'analysis_cohort', 'sampling_cohort', 'sampling_role',
               'purpose', 'selection_allowed', 'fit_allowed', 'requires_policy_freeze',
               'condition_id')


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _physical(scene):
    return {key: scene.get(key) for key in PHYSICAL_FIELDS}


def _sources(workspace_root):
    root = Path(workspace_root).resolve()
    values, bindings = {}, {}
    for name in SOURCE_NAMES:
        path = root / V12_DESIGN / name
        value = legacy._signed(path)
        if value.get('sealed_final_days') != list(legacy.SEALED_DAYS):
            raise ValueError('Source sealed final12 registry differs')
        values[name] = value
        bindings[(V12_DESIGN / name).as_posix()] = dict(sha256=_sha(path), signature=value['signature'])
    return values, bindings


def _finish(value):
    value = copy.deepcopy(value)
    value.pop('signature', None)
    value.update(study_name=STUDY, resource_limits=copy.deepcopy(RESOURCE_LIMITS),
        strict_final_gate='not_verified', original_final12_evaluated=False,
        original_final_release_authorized=False, sealed_final_days=list(legacy.SEALED_DAYS),
        source_load_values_read=False, raw_or_result_files_read=False,
        historical_annual_qc_already_parsed_physical_values=True,
        new_independent_background_count=0, hospital_count=1, source_year_count=1,
        statistical_iid_claim=False, total_planned_pair_count=312,
        planned_development_pair_count=72, planned_confirmation_pair_count=240)
    value['signature'] = digest(value)
    return value


def _scene(source, *, confirmation=False, recipe=None):
    scene = legacy._perturb(source, recipe) if confirmation else copy.deepcopy(source)
    role = 'confirmation' if confirmation else 'development'
    cohort = 'v13_final_once_new_coordinates' if confirmation else 'v13_executed_service_development'
    normal = source['analysis_class'] == 'N2'
    scene.update(source_v12_scenario_id=source['id'],
        source_role_metadata={**{k: copy.deepcopy(source.get(k)) for k in ROLE_FIELDS},
                              'condition_id_present': 'condition_id' in source},
        sample_schema_version=VERSION, study_name=STUDY,
        experiment_role=role, analysis_cohort=cohort, sampling_cohort=cohort, purpose=cohort,
        selection_allowed=not confirmation, fit_allowed=False, requires_policy_freeze=confirmation,
        previously_exposed_v12=True, new_independent_background=False,
        new_registered_support_window=False, is_final_blind=False,
        demand_background='previously_exposed_v12_same_hospital_year',
        is_new_physical_spec=confirmation and not normal,
        same_background_new_perturbation=confirmation and not normal,
        normal_is_new_perturbation=False,
        physical_duplicate_of=source['id'] if confirmation and normal else None,
        sampling_role=('repeated_normal_cost_regression' if normal else 'new_coordinates_on_seen_v12_root')
                      if confirmation else 'seen_v12_development',
        condition_id=copy.deepcopy(source.get('condition_id')))
    scene['physical_scenario_signature'] = digest(_physical(scene))
    if confirmation and not normal:
        scene['exogenous_stream_id'] = 'v13_exogenous:' + digest([scene['root_family_id'], scene['exogenous_events']])
    scene['id'] = ('v13_confirm_' if confirmation else 'v13_dev_') + digest(
        [source['id'], recipe, scene['physical_scenario_signature']])[:24]
    if confirmation:
        cell = 'v13_recipe_cell:' + digest([scene['root_family_id'], recipe])[:24]
        scene['design_cell_id'] = scene['matched_group_id'] = cell
    legacy._seal_check(scene)
    return scene


def _roots(sources, scenes):
    wanted = {s['root_family_id'] for s in scenes}
    records = {}
    for value in sources.values():
        for source in value.get('roots', []):
            if source['root_family_id'] in wanted and source['root_family_id'] not in records:
                row = copy.deepcopy(source)
                row.setdefault('condition_id', None)
                row.update(previously_exposed_v12=True, new_independent_background=False)
                records[row['root_family_id']] = row
    if set(records) != wanted:
        raise ValueError('A selected scenario has no signed source root')
    return sorted(records.values(), key=lambda r: (r['start_hour'], r['root_family_id']))


def _check_panel(scenes, expected_count, expected_classes):
    if len(scenes) != expected_count or len({s['id'] for s in scenes}) != expected_count:
        raise ValueError('Panel scenario count or identity uniqueness differs')
    if Counter(s['analysis_class'] for s in scenes) != expected_classes:
        raise ValueError('Panel real analysis-class counts differ')
    for scene in scenes:
        legacy._seal_check(scene)


def build_development(workspace_root=ROOT):
    sources, bindings = _sources(workspace_root)
    targeted = sources[SOURCE_NAMES[0]]['scenarios']
    distributed = sources[SOURCE_NAMES[1]]['scenarios']
    if len(targeted) != 12:
        raise ValueError('Expected exactly twelve disclosed v12 targeted cases')
    extra = [s for s in distributed if s['source_day_index'] in DEVELOPMENT_ADDITIONAL_DAYS]
    for day in DEVELOPMENT_ADDITIONAL_DAYS:
        if Counter(s['analysis_class'] for s in extra if s['source_day_index'] == day) != dict.fromkeys(CLASSES, 1):
            raise ValueError('Development supplemental day lacks six original real classes')
    scenes = [_scene(source) for source in [*targeted, *extra]]
    _check_panel(scenes, 24, dict(L1=4, L2=3, L3=3, L4a=2, L4b=9, N2=3))
    for scene in scenes:
        is_targeted = scene['source_v12_scenario_id'] in {s['id'] for s in targeted}
        scene.update(source_outcomes_used_for_selection=is_targeted,
                     development_selection_role='disclosed_targeted_counterexample_or_negative' if is_targeted
                                                else 'metadata_selected_existing_six_class_root')
    return _finish(dict(version=VERSION + '.development', scenarios=scenes,
        roots=_roots(sources, scenes), source_metadata_bindings=bindings,
        analysis_class_counts=dict(Counter(s['analysis_class'] for s in scenes)),
        source_outcomes_used_for_selection=True,
        selection_rule='Original v12 disclosed twelve plus existing day129/day212 six-class rows; no new outcome reads',
        new_physical_spec_count=0, planned_policy_count=3, planned_pair_count=72,
        registered_plan_created=False, physical_episodes_started=0,
        independence='All backgrounds and all development coordinates previously exposed in v12'))


def build_confirmation(workspace_root=ROOT, *, recipes=CONFIRMATION_RECIPES):
    sources, bindings = _sources(workspace_root)
    recipes = [legacy._recipe(r) for r in recipes]
    if len(recipes) != 2 or len({r['id'] for r in recipes}) != 2:
        raise ValueError('Exactly two distinct fixed exogenous recipes are required')
    selected = [s for s in sources[SOURCE_NAMES[1]]['scenarios']
                if s['source_day_index'] in CONFIRMATION_DAYS]
    for day in CONFIRMATION_DAYS:
        if Counter(s['analysis_class'] for s in selected if s['source_day_index'] == day) != dict.fromkeys(CLASSES, 1):
            raise ValueError('Confirmation day lacks six original real classes')
    roots = _roots(sources, selected)
    assignments = {}
    for quarter in range(1, 5):
        quarter_roots = [r for r in roots if r['season_quarter'] == quarter]
        if len(quarter_roots) != 2:
            raise ValueError('Confirmation requires two existing roots per quarter')
        for index, record in enumerate(quarter_roots):
            assignments[record['root_family_id']] = recipes[index]
    scenes = [_scene(s, confirmation=True, recipe=assignments[s['root_family_id']]) for s in selected]
    _check_panel(scenes, 48, dict.fromkeys(CLASSES, 8))
    seen = {digest(_physical(s)) for value in sources.values() for s in value['scenarios']}
    new_keys = [digest(_physical(s)) for s in scenes if s['is_new_physical_spec']]
    if len(set(new_keys)) != 40 or set(new_keys) & seen:
        raise ValueError('A proposed new physical coordinate is duplicate or already seen in v12')
    return _finish(dict(version=VERSION + '.confirmation', scenarios=scenes, roots=roots,
        recipes=recipes, root_recipe_assignments={rid: recipe['id'] for rid, recipe in assignments.items()},
        source_metadata_bindings=bindings,
        analysis_class_counts=dict(Counter(s['analysis_class'] for s in scenes)),
        source_outcomes_used_for_selection=False,
        selection_rule='Fixed days2/3/123/147/222/234/282/301, six existing classes each, one recipe per root by quarter/time order',
        new_physical_spec_count=40, normal_physical_repeat_count=8,
        planned_policy_count=5, planned_pair_count=240,
        registered_plan_created=False, physical_episodes_started=0,
        confirmation_gate='One method/source/parameter/scoring freeze before execution; no frozen-outcome tuning',
        independence='Previously exposed same-hospital/year roots; 40 new event coordinates and 8 repeated normal references, not blind or independent backgrounds'))


def build_manifest(scenarios, roots, **options):
    """Reuse the original metadata/inventory builder and explicit runtime IDs."""
    value = legacy.build_manifest(scenarios, roots, **options)
    value.update(manifest_version=VERSION + '.manifest', study_name=STUDY)
    return _finish(value)


def _write_new(path, value):
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--workspace-root', type=Path, default=ROOT)
    parser.add_argument('--manifest-options', type=Path,
        help='Optional JSON mapping development/confirmation to explicit runtime build_manifest kwargs')
    args = parser.parse_args(argv)
    destination = args.output.resolve()
    if destination.drive.lower() == 'c:':
        raise ValueError('Use the authorized D/E workspace for v13 output')
    development = build_development(args.workspace_root)
    confirmation = build_confirmation(args.workspace_root)
    values = {'DEVELOPMENT_PROPOSAL.json': development, 'CONFIRMATION_PROPOSAL.json': confirmation}
    if args.manifest_options:
        options = json.loads(args.manifest_options.read_text(encoding='utf-8'))
        for name, proposal in (('development', development), ('confirmation', confirmation)):
            values[name.upper() + '_MANIFEST.json'] = build_manifest(
                proposal['scenarios'], proposal['roots'], workspace_root=args.workspace_root, **options[name])
    destination.mkdir(parents=True, exist_ok=True)
    if any((destination / name).exists() for name in (*values, 'DATA_DESIGN_RECEIPT.json')):
        raise FileExistsError('Metadata output already exists; old proposal bytes will not be overwritten')
    for name, value in values.items():
        _write_new(destination / name, value)
    receipt = dict(schema=VERSION + '.receipt', generator_sha256=_sha(__file__),
        source_metadata_bindings=development['source_metadata_bindings'],
        output_sha256={name: _sha(destination / name) for name in values},
        output_directory=str(destination), development_scenarios=24, confirmation_scenarios=48,
        new_physical_event_coordinates=40, repeated_normal_specifications=8,
        planned_development_pairs=72, planned_confirmation_pairs=240, planned_total_pairs=312,
        resource_limits=copy.deepcopy(RESOURCE_LIMITS), annual_physical_rows_parsed=0,
        raw_or_result_files_read=0, physical_episodes_started=0, plans_registered=0,
        strict_final_gate='not_verified', original_final12_evaluated=False,
        manifests_created=bool(args.manifest_options))
    _write_new(destination / 'DATA_DESIGN_RECEIPT.json', receipt)
    print(json.dumps(receipt, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
