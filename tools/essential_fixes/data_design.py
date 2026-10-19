"""v16 metadata only: preserve real prefixes and original sealed support."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path

from tools.operating_response import data_design as old

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = 'hospital_ies.essential_metadata.v16.v1'
ENGINEERING_MODEL_CONTRACT_ID = 'hospital_ies.essential_engineering.v16.v1'
STUDY_CONTRACT_ID = ENGINEERING_MODEL_CONTRACT_ID
ACTION_SCHEMA = old.ACTION_SCHEMA
PLANNING_INTERFACE = old.PLANNING_INTERFACE
SEALED_DAYS = old.SEALED_DAYS
CORE_CLASSES = old.CORE_CLASSES
RESOURCE_LIMITS = dict(max_actual_attempts=600, max_experiment_wall_hours=12,
                       ceilings_not_quotas=True, internal_provisional_limits=True)
digest = old.digest
validate_observation_support = old.validate_observation_support
validate_sealed_support = old.validate_sealed_support
allowed_hours = old.allowed_hours
V15 = Path('outputs/v15_engineering_operation_20261017_run01')


def _finish(value):
    result = old._finish(value)
    result.pop('signature', None)
    result.update(schema=SCHEMA, study_name='hospital_ies_essential_engineering_v16',
                  resource_limits=copy.deepcopy(RESOURCE_LIMITS))
    result['signature'] = digest(result)
    return result


def seal_scene(scene):
    """Reseal coordinates after an explicit physical design edit; never simulate."""
    value = copy.deepcopy(scene)
    previous = value.get('id')
    if previous and str(previous).startswith('v15_'):
        value.setdefault('source_v15_scenario_id', previous)
    value.setdefault('experiment_role', 'development')
    confirmation = value['experiment_role'] == 'confirmation'
    value.update(is_final_blind=False, original_final12_evaluated=False,
                 sample_schema_version=SCHEMA, old_results_read_only=True,
                 new_independent_background=False, fit_allowed=False,
                 selection_allowed=not confirmation, requires_policy_freeze=confirmation)
    value.setdefault('analysis_cohort', 'v16_development')
    value.setdefault('sampling_cohort', 'v16_development')
    value.setdefault('purpose', 'v16_essential_engineering')
    value.setdefault('sampling_role', 'registered_engineering_development')
    value.setdefault('prehistory_mode', 'same_continuous_episode_no_reset')
    value.setdefault('state_inheritance', 'Uninterrupted equipment, fuel, thermal state and ordinary command queues')
    value['full_source_support'] = validate_observation_support(value)
    value['time_block_id'] = 'v16_support:' + digest(value['full_source_support'])[:24]
    value['exogenous_stream_id'] = 'v16_exogenous:' + digest(
        [value['root_family_id'], value.get('exogenous_events', [])])[:24]
    physical = {key: value.get(key) for key in old.old.PHYSICAL_FIELDS}
    value['physical_scenario_signature'] = digest(physical)
    identity = [value['physical_scenario_signature'], value['experiment_role'], value.get('design_variant')]
    value['id'] = 'v16_' + value['analysis_class'] + '_' + digest(identity)[:24]
    value['matched_group_id'] = value['design_cell_id'] = 'v16_cell:' + digest(
        [value['root_family_id'], value['analysis_class'], value.get('design_variant')])[:24]
    validate_sealed_support(value)
    return value


def panel(name, scenes, *, bindings=None, freeze_signature=None):
    scenes = [seal_scene(scene) for scene in scenes]
    result = old._panel(name, scenes, bindings=bindings, freeze_signature=freeze_signature)
    result.update(source_outcomes_used_for_selection=any(s.get('source_outcomes_used_for_selection', False)
                                                        for s in scenes),
                  physical_model_selection_precedes_confirmation=True)
    return _finish(result)


def execution_model_contract_id(model_id):
    """The physical model identity is separate from this v16 study wrapper."""
    if model_id == 'M16_essential':
        from .execution import DYNAMICS_CONTRACT_ID
    elif model_id == 'M15_local_response':
        from tools.operating_response.execution import DYNAMICS_CONTRACT_ID
    elif model_id == 'M14_engineering':
        from tools.engineering_model.execution import DYNAMICS_CONTRACT_ID
    elif model_id == 'M0':
        from tools.engineering_model.environment import model_contract
        return model_contract('M0', {})[0]['name']
    else:
        raise ValueError('Unknown explicitly versioned execution model')
    return DYNAMICS_CONTRACT_ID


def build_manifest(proposal, source_sha256, model_id, model_config, root=ROOT, policies=None):
    if model_id not in ('M0', 'M14_engineering', 'M15_local_response', 'M16_essential'):
        raise ValueError('Unsupported explicitly versioned v16 comparison model')
    # The v15 builder only constructs the inherited byte inventory; no physical
    # resolver is called. Replace both public model and contract suffix below.
    manifest = old.build_manifest(proposal, source_sha256, 'M15_local_response',
                                  model_config, root=root, policies=policies)
    manifest.pop('signature', None)
    actual_contract = execution_model_contract_id(model_id)
    manifest.update(manifest_version=SCHEMA + '.manifest', execution_model_id=model_id,
                    engineering_model_contract_id=actual_contract, study_contract_id=STUDY_CONTRACT_ID,
                    planning_interface_id=PLANNING_INTERFACE,
                    source_proposal_signature=proposal['signature'])
    suffix = '__v16_' + digest([model_id, model_config, actual_contract, STUDY_CONTRACT_ID])[:16]
    manifest['execution_contract_id'] = manifest['legacy_execution_contract_id'] + suffix
    manifest['assumption_set_id'] = manifest['legacy_assumption_set_id'] + suffix
    return _finish(manifest)


def local_a_proposal(root=ROOT):
    """315/280: original and +/-2.5min shock, one no-shock control, good normal."""
    root = Path(root)
    scenes, bindings = [], {}
    for day, filename in ((315, 'FROZEN_INCIDENTS_PROPOSAL.json'), (280, 'DEV_V2_PROPOSAL.json')):
        relative = V15 / 'design' / filename
        path = root / relative
        proposal = old.old.legacy._signed(path)
        bindings[relative.as_posix()] = dict(sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                                            signature=proposal['signature'])
        source = next(s for s in proposal['scenarios']
                      if s['source_day_index'] == day and s['analysis_class'] == 'L2')
        for delta in (-2.5, 0., 2.5):
            s = copy.deepcopy(source)
            s.update(design_variant='local_A_shock_phase_' + str(delta),
                     local_engineering_case='minimum_output_boundary', phase_delta_minutes=delta,
                     source_outcomes_used_for_selection=True, previously_exposed_v15=True,
                     experiment_role='development', analysis_cohort='v16_local_A', sampling_cohort='v16_local_A',
                     first_event_minute=source['first_event_minute'] + delta,
                     shock_start_minute=source['shock_start_minute'] + delta,
                     normal_history_minutes=source['first_event_minute'] + delta,
                     diagnostic_reference_event_minute=source['first_event_minute'])
            for event in s['exogenous_events']:
                event['start_minute'] += delta
                event['end_minute'] += delta
            scenes.append(s)
        s = copy.deepcopy(source)
        s.update(design_variant='local_A_no_shock', local_engineering_case='minimum_output_boundary',
                 phase_delta_minutes=0., source_outcomes_used_for_selection=True, previously_exposed_v15=True,
                 experiment_role='development', analysis_cohort='v16_local_A', sampling_cohort='v16_local_A',
                 analysis_class='N2', event_class='L1', stage=1, faults={}, shock={}, shock_by_priority={},
                 exogenous_events=[], enable_propagation=False, first_event_minute=None,
                 normal_history_minutes=s['duration_steps'] * s['step_minutes'],
                 diagnostic_reference_event_minute=source['first_event_minute'],
                 actual_p0_load_multiplier={'e': 1., 'h': 1., 'c': 1.})
        scenes.append(s)
    good = old._base(10, 24)
    good.update(design_variant='local_A_good_normal', local_engineering_case='normal_reference',
                source_outcomes_used_for_selection=True, previously_exposed_v15=True,
                analysis_cohort='v16_local_A', sampling_cohort='v16_local_A')
    scenes.append(good)
    result = panel('local_A_same_prefix_boundary_diagnosis', scenes, bindings=bindings)
    result.update(design_note='Only registered event clock/removal varies within each seen root; original demand and reset fields retained',
                  identical_no_shock_controls_not_triplicated=True)
    return _finish(result)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('local-a',))
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.resolve().drive.lower() == 'c:':
        raise ValueError('Research data must use D/E, not C')
    value = local_a_proposal()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps(dict(scenarios=len(value['scenarios']), output=str(args.output), signature=value['signature'])))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
