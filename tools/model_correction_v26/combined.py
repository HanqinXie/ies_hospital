"""Register four matched active-GT2 cases; no simulator or optimizer copy.

Canonical physical classes are separate from the historical parser classes.
The parser's two stage branches select equal fault/shock clocks in this study.
"""
from __future__ import annotations
import copy
import hashlib
import json
from . import MODEL_ID, CONTRACT_ID, STUDY_ID

V25_SOURCE = '47345730795cc15eda9e5b15071ddbedb65e916e4341e561c1f51148c75ea278'
V25_HEAD = 'a016e3119c7730e2c513839bbfe3576d6b3530ab'
VARIANTS = ('N', 'F', 'D', 'FD')
ER_ZONES = ('ER_TRAUMA1_FLR_1', 'ER_TRAUMA2_FLR_1', 'ER_EXAM1_MULT4_FLR_1', 'ER_EXAM3_MULT4_FLR_1')
CLASSES = dict(N='NORMAL', F='L2', D='L3', FD='L4')
PARSER = dict(N=(3, 'L3'), F=(3, 'L3'), D=(4, 'L4'), FD=(4, 'L4b'))
EVENT_OR_LABEL_KEYS = frozenset(('id', 'canonical_class', 'analysis_class', 'stage', 'event_class',
    'faults', 'shock_by_zone', 'demand_amplitude', 'normal_verification',
    'normal_exogenous_fault_present', 'normal_clinical_shock_present', 'exogenous_stream_id'))


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def common_spec(case):
    """All registered physical/operational fields except intended event factors."""
    scene = {k: copy.deepcopy(v) for k, v in case['scenario'].items() if k not in EVENT_OR_LABEL_KEYS}
    return dict(scenario=scene, configuration=case['configuration'], policy=copy.deepcopy(case['policy']),
        operating_rule_id=case['operating_rule_id'])


def check_selection(evidence):
    if evidence.get('source_sha256') != V25_SOURCE:
        raise ValueError('Selection must bind the existing V25 Source03')
    candidates = evidence.get('candidates', [])
    if {c['case_id'] for c in candidates} != {'four_normal_summer', 'four_normal_winter_optional'}:
        raise ValueError('Both supported V25 normal backgrounds must be screened')
    if any(float(c['boiler_max_lbph']) > 2000. for c in candidates):
        raise ValueError('A supported natural boiler-group background takes precedence')
    summer = next(c for c in candidates if c['case_id'] == 'four_normal_summer')
    if summer['start_hour'] != 4001 or abs(float(summer['pre30_minutes'])-30.) > 1e-7:
        raise ValueError('Selected normal must cover the complete pre-event period')
    if float(summer['pre30_gt2_min_kW']) <= 1e-7 or len(summer.get('native_sha256', '')) != 64:
        raise ValueError('Actual individually active GT2 and original native identity are required')


def build_registry(original, selection):
    """Pure derivation from the frozen normal background and supported L4 shock."""
    check_selection(selection)
    source = next(c for c in original['cases'] if c['id'] == 'four_normal_summer')
    old_l4 = next(c for c in original['cases'] if c['id'] == 'four_L4_boiler_ER_autumn')
    supported_shock = {z: {c: .25 for c in 'ehc'} for z in ER_ZONES}
    if old_l4['scenario']['shock_by_zone'] != supported_shock:
        raise ValueError('The supported original ER disturbance has changed')
    scene = copy.deepcopy(source['scenario'])
    scene.update(experiment_role='seen_engineering_model_regression',
        fault_target='GT_2', fault_availability=0., fault_start_minute=30., fault_duration_minutes=60.,
        shock_start_minute=30., shock_duration_minutes=60., first_event_minute=30., recovery_start_minute=90.,
        history_interval_minutes=[0., 30.], tail_interval_minutes=[90., 180.],
        response_delay_minutes=30., normal_eval_interval_minutes=[0., 180.],
        model_closure_events=[], sampling_cohort='v26_preregistered_active_GT2_factorial',
        sampling_method='fixed_seen_background_selected_by_pre_event_actual_path',
        historical_exposure_status='seen_V25_normal_h4001_not_independent_confirmation',
        native_event_class_is_parser_encoding_only=True, reference_phase_clocks_are_analysis_only=True)
    scene.pop('base_state_id', None)
    initial_spec = dict(scenario={k: v for k, v in scene.items() if k not in EVENT_OR_LABEL_KEYS},
        hardware=original['hardware_profiles']['H0'], policy=source['policy'],
        operating_rule=original['operating_rules']['current_service_bridge'])
    scene['base_state_id'] = 'v26_initialization:'+digest(initial_spec)
    cases = []
    for variant in VARIANTS:
        s = copy.deepcopy(scene); fault = 'F' in variant; demand = 'D' in variant
        s.update(id='v26_combined_'+variant, canonical_class=CLASSES[variant], analysis_class=CLASSES[variant],
            stage=PARSER[variant][0], event_class=PARSER[variant][1], faults={'GT_2': 0.} if fault else {},
            shock_by_zone=copy.deepcopy(supported_shock) if demand else {}, demand_amplitude=.25 if demand else 0.,
            normal_verification=variant == 'N', normal_exogenous_fault_present=fault,
            normal_clinical_shock_present=demand)
        s['exogenous_stream_id'] = 'v26_exogenous:'+digest({k: s[k] for k in (
            'faults', 'fault_start_minute', 'fault_duration_minutes',
            'shock_by_zone', 'shock_start_minute', 'shock_duration_minutes')})
        cases.append(dict(id='combined_'+variant, purpose='Matched active GT2 fault x supported ER request: '+variant,
            factorial_variant=variant, scenario=s, configuration='H0', policy=copy.deepcopy(source['policy']),
            operating_rule_id='current_service_bridge', study_id=STUDY_ID,
            evaluation_role='seen_V26_mechanism_regression_not_independent_statistics',
            provenance=dict(normal_parent_case_id=source['id'], demand_parent_case_id=old_l4['id'],
                V25_source_sha256=V25_SOURCE, original_L4_active_boiler_excited=False)))
    smoke = copy.deepcopy(cases[0]); smoke.pop('factorial_variant')
    smoke.update(id='smoke_normal', purpose='15min public entry using the same M25 physical implementation')
    smoke['scenario'].update(id='v26_smoke_normal', stage=3, event_class='L3', duration_steps=1,
        fault_start_minute=0., fault_duration_minutes=0., shock_start_minute=0., shock_duration_minutes=0.,
        first_event_minute=0., recovery_start_minute=15., history_interval_minutes=[0., 15.],
        tail_interval_minutes=[15., 15.], normal_eval_interval_minutes=[0., 15.])
    smoke['scenario']['exogenous_stream_id'] = 'v26_exogenous:'+digest(dict(healthy=True, duration_minutes=15.))
    cases.append(smoke)
    registry = copy.deepcopy(original)
    registry.update(schema='hospital_ies.v26.matched_cases.v1', study_id=STUDY_ID,
        physical_model_id=MODEL_ID, physical_contract_id=CONTRACT_ID, baseline_commit=V25_HEAD,
        scope='Four matched active-path seen-background mechanisms and one normal public smoke; unchanged M25 physics',
        cases=cases, hardware_profiles={'H0': copy.deepcopy(original['hardware_profiles']['H0'])},
        default_operating_rule='current_service_bridge',
        operating_rules={'current_service_bridge': copy.deepcopy(original['operating_rules']['current_service_bridge'])},
        combined_selection=copy.deepcopy(selection), no_runs_started_by_registry_creation=True)
    registry['schemas']['model_correction_v26_study'] = STUDY_ID
    validate_registration(registry)
    return registry


def validate_registration(registry):
    if registry.get('physical_model_id') != MODEL_ID or registry.get('physical_contract_id') != CONTRACT_ID:
        raise ValueError('V26 registration must preserve the M25 physical identity')
    cases = registry['cases']; matrix = {c['factorial_variant']: c for c in cases if 'factorial_variant' in c}
    if len(cases) != 5 or set(matrix) != set(VARIANTS) or len(matrix) != 4:
        raise ValueError('Exactly four factorial cases plus one smoke are registered')
    views = [common_spec(matrix[v]) for v in VARIANTS]
    if any(v != views[0] for v in views[1:]):
        raise ValueError('Factorial common physical/initial/operational settings differ')
    for variant, case in matrix.items():
        s = case['scenario']
        if (s['stage'], s['event_class']) != PARSER[variant] or s['canonical_class'] != CLASSES[variant]:
            raise ValueError('Canonical physical class or native parser encoding differs')
        if (s['fault_start_minute'], s['shock_start_minute'], s['response_delay_minutes']) != (30., 30., 30.):
            raise ValueError('Stage-selected response/protection clocks must share the common boundary')
        if s['faults'] != ({'GT_2': 0.} if 'F' in variant else {}):
            raise ValueError('Unexpected root hardware event')
        expected = {z: {c: .25 for c in 'ehc'} for z in ER_ZONES} if 'D' in variant else {}
        if s['shock_by_zone'] != expected:
            raise ValueError('Unexpected or unsupported demand event')
    return True
