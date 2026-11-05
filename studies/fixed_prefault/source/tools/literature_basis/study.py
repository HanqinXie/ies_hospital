"""Fixed conditional checks using the unchanged registered physical model.

These locally prepared windows are not annual inventory replays. The source
calendar belongs to the exogenous environment and is never a policy input.
"""
from __future__ import annotations

from tools.annual_readiness.registration import make_case
from tools.operating_rules_study.registration import load_registry

STUDY_ID = 'literature_basis_20261030'


def build_study():
    registry = load_registry()
    winter = 'b07_L4_GT_ER_disturbed_M'
    summer = 'b03_L2_tower_disturbed_M'
    specifications = [
        ('boiler_24h', winter, 33, 'H0', 'BOILER_1', 1620., 1500., .0039/100),
        ('abs_48h', summer, 4137, 'H2', 'ABS', 3060., 2940., .0220/100),
        ('shared_tower', summer, 4137, 'H2', 'TOWER', 180., 120., None),
        ('shared_chw', summer, 4137, 'H0', 'AUX_CHW_PUMP', 180., 120., None),
    ]
    cases, pairs = [], []
    for name, source, hour, configuration, port, duration, repair, probability in specifications:
        common = dict(mode='N', start_hour=hour, configuration=configuration,
            duration_minutes=duration, response_wait_minutes=15., demand_amplitude=0.,
            boiler_restart_minutes=1., requalification_minutes=None)
        healthy = make_case(registry, source, 'M_ready', calendar=[], **common)
        events = [dict(time_minute=60., availability={port: 0.}),
                  dict(time_minute=repair, availability={port: 1.})]
        fault = make_case(registry, source, 'M_ready', calendar=events, **common)
        cases.extend((healthy, fault))
        pairs.append(dict(name=name, healthy=healthy['id'], fault=fault['id'],
            source_id=source, source_start_hour=hour, port=port,
            probability_per_hour=probability,
            duration_origin='ECM_2026_Table2_fixed_duration' if probability is not None
                else 'registered_conditional_shared_support_check',
            conditional_fault_not_random_year_sample=True,
            annual_inventory_initialization=False,
            hospital_failure_probability_claim=False,
            recovery_tail_is_observation_not_service_guarantee=True))
    return dict(study_id=STUDY_ID, cases=cases, pairs=pairs,
        full_8760_physical_run=False, original_final12_evaluated=False,
        physical_model_modified=False, future_calendar_policy_input=False)
