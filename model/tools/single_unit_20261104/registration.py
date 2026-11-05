"""Strict derivative registration; the historical canonical validator stays strict."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from tools.annual_readiness.registration import load_registry
from tools.minute1_20261103.registration import validate_case as validate_base_case
from tools.minute1_20261103.runtime import validate_minute_scenario
from tools.model_closure_v22.environment import validate_events
from tools.sci_engineering.design import digest

from . import BASELINE_COMMIT, MODEL_ID, SCHEMA

KINDS = ('tower_unit', 'primary_pump', 'healthy_no_dispatch')
VARIANTS = ('unattended', 'responsive')
BACKGROUNDS = {4137: dict(topic='T05', original_target='TOWER', method='M_ready'),
               5072: dict(topic='T06', original_target='AUX_CHW_PUMP', method='M_available')}


def _binding(case, binding):
    if not isinstance(binding, dict) or set(binding) != {'path', 'sha256'}:
        raise ValueError('Exact base CASE path/sha256 binding required')
    path = Path(binding['path']).resolve()
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != binding['sha256']:
        raise ValueError('Base CASE bytes differ from their authenticated binding')
    value = json.loads(path.read_text(encoding='utf-8-sig'))
    if digest(value.get('case', value)) != digest(case):
        raise ValueError('Base CASE file contains a different canonical case')
    return dict(path=str(path), sha256=binding['sha256'])


def _differences(left, right, path=''):
    if isinstance(left, dict) and isinstance(right, dict):
        result = []
        for key in sorted(set(left) | set(right)):
            child = path + '.' + key if path else key
            if key not in left or key not in right:
                result.append(child)
            else:
                result.extend(_differences(left[key], right[key], child))
        return result
    return [] if left == right else [path]


def make_case(base_case, *, kind, variant, base_binding):
    """Derive only the registered target/calendar and explicit study identity."""
    base = validate_base_case(base_case, load_registry())
    binding = _binding(base, base_binding)
    if kind not in KINDS or variant not in VARIANTS:
        raise ValueError('Unknown single-unit kind or intervention variant')
    if base['minute1']['variant'] != variant:
        raise ValueError('Intervention variant must match the unchanged canonical base CASE')
    scene = base['scenario']; hour = scene['start_hour']
    if hour not in BACKGROUNDS or scene['duration_steps'] != 180 or scene['step_minutes'] != 1.:
        raise ValueError('Only original T05/T06 complete minute backgrounds are registered')
    background = BACKGROUNDS[hour]
    if (base['configuration'] != 'H2' or base['minute1']['method'] != background['method']
            or scene.get('model_closure_events') != [
                dict(time_minute=60., availability={background['original_target']: 0.}),
                dict(time_minute=120., availability={background['original_target']: 1.})]):
        raise ValueError('Original H2 background, method and common-fault base CASE required')
    if kind == 'tower_unit' and hour != 4137 or kind == 'primary_pump' and hour != 5072:
        raise ValueError('Single-unit kind differs from the registered original background')
    if kind == 'healthy_no_dispatch' and variant != 'unattended':
        raise ValueError('Healthy no-dispatch diagnostic requires the unattended base CASE')
    target = {'tower_unit': 'TOWER_1', 'primary_pump': 'EC_1_PRIMARY_PUMP',
              'healthy_no_dispatch': None}[kind]
    native_target = 'EC_1' if kind == 'primary_pump' else target
    identity = dict(model_id=MODEL_ID, kind=kind, variant=variant,
                    base_case_sha256=digest(base), base_binding=binding,
                    source_baseline_commit=BASELINE_COMMIT)
    case_id = 'single_unit_' + digest(identity)[:18]
    case = deepcopy(base)
    case.update(id=case_id, study_id=MODEL_ID, evaluation_role='finite_single_unit_causal_research')
    declared = case['scenario']
    declared.update(id=case_id, normal_verification=target is None,
                    sampling_cohort='single_unit_20261104', purpose='finite_single_unit_causal_research',
                    exogenous_stream_id='single_unit_exogenous:' + digest(dict(
                        original=scene['exogenous_stream_id'], kind=kind)))
    declared['model_closure_events'] = ([] if target is None else [
        dict(time_minute=60., availability={target: 0.}),
        dict(time_minute=120., availability={target: 1.})])
    validate_minute_scenario(declared)
    native = deepcopy(declared)
    if kind == 'primary_pump':
        native['model_closure_events'] = [dict(time_minute=event['time_minute'],
            availability={native_target: event['availability'][target]})
            for event in declared['model_closure_events']]
    validate_minute_scenario(native); validate_events(native['model_closure_events'])
    differences = _differences(base, case)
    case['unit_study'] = dict(schema=SCHEMA, model_id=MODEL_ID, kind=kind, variant=variant,
        base_case=base, base_case_sha256=digest(base), base_binding=binding,
        source_baseline_commit=BASELINE_COMMIT, original_topic=background['topic'],
        declared_target=target, native_functional_target=native_target,
        declared_scene_sha256=digest(declared), native_scene=native, native_scene_sha256=digest(native),
        strict_difference_list=differences,
        legacy_static_metadata_not_used_for_actual_fault={name: deepcopy(declared[name])
            for name in ('fault_target', 'fault_availability', 'fault_duration_minutes') if name in declared},
        authoritative_declared_fault_target=target,
        authoritative_declared_fault_calendar=deepcopy(declared['model_closure_events']),
        actual_fault_uses_model_closure_events_only=True,
        native_calendar_semantics=('functional_branch_interlock_not_compressor_hardware_fault'
            if kind == 'primary_pump' else 'native_single_tower_slot' if target else 'healthy_no_dispatch'),
        one_exogenous_cause=target is not None, exogenous_cause_count=int(target is not None),
        at_most_one_exogenous_cause=True, future_calendar_is_policy_input=False,
        topology_qualification=('DOE dedicated primary branch mapped to research EC1; no OEM hydraulic calibration'
            if kind == 'primary_pump' else 'One of four equal research tower slots; no DOE cell mapping'
            if kind == 'tower_unit' else 'Healthy reference with original optimization suppression after60'),
        new_capacity_flow_power_or_time_constant_inferred=False)
    return case


def validate_case(case):
    """Reconstruct the exact derivative from byte-authenticated canonical inputs."""
    if not isinstance(case, dict) or not isinstance(case.get('unit_study'), dict):
        raise ValueError('Explicit single-unit derivative CASE required')
    meta = case['unit_study']
    try:
        expected = make_case(meta['base_case'], kind=meta['kind'], variant=meta['variant'],
                             base_binding=meta['base_binding'])
    except (KeyError, TypeError) as exc:
        raise ValueError('Incomplete single-unit derivative CASE') from exc
    if digest(expected) != digest(case):
        raise ValueError('Single-unit CASE differs from its exact registered causal declaration/lowering')
    return deepcopy(case)
