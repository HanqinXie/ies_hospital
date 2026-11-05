"""Canonical minute cases with an unchanged authenticated historical parent.

The parent remains historical 15-minute metadata. Only the explicit new case
selects minute source, clock and upper-command intervention; it grants no new
hardware capability, future observation, initialization or execution path.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

from tools.annual_readiness import registration as original
from tools.sci_engineering.design import digest, source_support
from tools.single_fault_model_20261102.runtime import validate_single_fault_scenario
from .source import validate_source_spec, sha256

MODEL_ID = 'hospital_ies.minute1_20261103.v1'
SCHEMA = 'hospital_ies.minute1.case.v1'
VARIANTS = ('normal', 'unattended', 'responsive')
ALLOWED_START_HOURS = (1, 33, 1977, 4137, 5072)
BACKGROUND_TARGETS = {1:('BOILER_1',),33:('GT_1',),1977:('GRID',),
    4137:('ABS','EC_1','TOWER'),5072:('AUX_CHW_PUMP',)}
BACKGROUND_METHODS = {1:'M_available',33:'M_ready',1977:'M_available',
    4137:'M_ready',5072:'M_available'}


def _parent_binding(parent, binding):
    if binding is None:
        return None
    if not isinstance(binding, dict) or set(binding) != {'path','sha256'}:
        raise ValueError('Explicit original parent file/hash binding required')
    path = Path(binding['path']).resolve()
    if not path.is_file() or sha256(path) != binding['sha256']:
        raise ValueError('Original parent file/hash mismatch')
    value = json.loads(path.read_text(encoding='utf-8-sig'))
    recorded = value.get('case', value)
    if digest(recorded) != digest(parent):
        raise ValueError('Original parent binding contains a different canonical case')
    return dict(path=str(path),sha256=binding['sha256'])


def make_case(parent, registry, *, minute_source, variant='responsive', parent_binding=None):
    parent = original.validate_case(parent, registry)
    validate_single_fault_scenario(parent['scenario'])
    if variant not in VARIANTS:
        raise ValueError('Unknown minute intervention variant')
    source = validate_source_spec(minute_source)
    scene = parent['scenario']
    end = float(scene['duration_steps'])*float(scene['step_minutes'])
    if (scene['start_hour'] not in ALLOWED_START_HOURS or parent['configuration'] != 'H2'
            or end != (240. if scene['start_hour']==1977 else 180.)):
        raise ValueError('Minute cases must retain the registered five H2 local backgrounds')
    if parent['annual_readiness']['method'] != BACKGROUND_METHODS[scene['start_hour']]:
        raise ValueError('Registered background method differs from the original topic mapping')
    events = scene.get('model_closure_events', [])
    if variant == 'normal':
        if events or scene.get('faults') or scene.get('auxiliary_faults') or not scene.get('normal_verification'):
            raise ValueError('A clean canonical normal parent is required')
    else:
        if (len(events) != 2 or events[0]['time_minute'] != 60.
                or events[1]['time_minute'] != 120.
                or set(events[0]['availability']) != set(events[1]['availability'])
                or len(events[0]['availability']) != 1
                or next(iter(events[0]['availability'].values())) >= 1.
                or next(iter(events[1]['availability'].values())) != 1.):
            raise ValueError('One canonical fault at60 and same-target repair at120 required')
        if next(iter(events[0]['availability'])) not in BACKGROUND_TARGETS[scene['start_hour']]:
            raise ValueError('Registered background fault target differs from the original topic mapping')
    binding = _parent_binding(parent,parent_binding)
    case = deepcopy(parent)
    case.pop('annual_readiness')
    case.pop('v28_thermal_profile',None)
    case['v28_demand_version'] = 'Minute1_adopted'
    case['study_id'] = MODEL_ID
    case['evaluation_role'] = 'finite_minute_single_fault_research'
    case['minute1'] = dict(schema=SCHEMA,model_id=MODEL_ID,variant=variant,
        parent=deepcopy(parent),parent_case_id=parent['id'],parent_case_sha256=digest(parent),
        parent_binding=binding,minute_source=source,
        method=parent['annual_readiness']['method'],
        parameters=deepcopy(parent['annual_readiness']['parameters']),
        control_interval_minutes=1.,source_interval_minutes=1.,
        upper_commands_disabled_at_minute=60. if variant=='unattended' else None,
        command_queue_and_actual_delays_unchanged=True,calendar_is_online_policy_input=False,
        initialization='original canonical static reset and seed; minute requests start at actualt0',
        old_results_reused_as_new_outcomes=False)
    new_scene = case['scenario']
    new_scene['step_minutes'] = 1.
    new_scene['duration_steps'] = int(end)
    new_scene['sampling_cohort'] = 'minute1_20261103'
    new_scene['purpose'] = 'finite_minute_single_fault_research'
    source_identity = digest(source)
    family = 'minute1_background_'+digest(dict(start_hour=scene['start_hour'],
        duration_minutes=end,configuration=parent['configuration'],minute_source=source_identity))[:18]
    new_scene.update(source_block_id=family,family_id=family,root_family_id=family,
        design_family=family,parent_template_id=parent['id'],
        exogenous_stream_id='minute1_exogenous:'+digest(dict(
            parent_exogenous=scene['exogenous_stream_id'],minute_source=source_identity)))
    new_scene['full_source_support'] = source_support(new_scene)
    identity = deepcopy(case);identity.pop('id',None);identity['scenario'].pop('id',None)
    case['id'] = new_scene['id'] = 'minute1_'+digest(identity)[:18]
    return case


def validate_case(case, registry):
    if not isinstance(case,dict) or not isinstance(case.get('minute1'),dict):
        raise ValueError('Explicit canonical minute case required')
    meta = case['minute1']
    try:
        expected = make_case(meta['parent'],registry,minute_source=meta['minute_source'],
            variant=meta['variant'],parent_binding=meta['parent_binding'])
    except (KeyError,TypeError) as exc:
        raise ValueError('Incomplete canonical minute registration') from exc
    if digest(case) != digest(expected):
        raise ValueError('Minute case differs from canonical source, clock, intervention or physics')
    return deepcopy(case)


def parent_case(case, registry):
    return validate_case(case,registry)['minute1']['parent']


def worker_registry(case, registry):
    return original.worker_registry(parent_case(case,registry),registry)
