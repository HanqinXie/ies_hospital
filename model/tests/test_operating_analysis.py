"""In-memory analysis contracts, including real long tail and domain unknowns."""
import copy
import importlib
import json
from pathlib import Path

import pytest

from test_engineering_analysis import payload,segment


def api():
    try:
        return importlib.import_module('tools.operating_response.analysis')
    except ModuleNotFoundError:
        pytest.fail('v15 analysis not implemented')


def test_true_48_hour_registered_full_does_not_stop_at_1440():
    a=api(); p=payload([segment(0,1440),segment(1440,2880,loss=2,disconnected=1)],requested=2880)
    before=copy.deepcopy(p);r=a.analyze_payload(p,None)
    assert r['windows']['registered_full']['window_minutes']==[0.,2880.]
    assert r['windows']['registered_full']['observed_prefix']['total_ENS_kWh'][2][0]==48
    assert r['windows']['full']['window_minutes']==[0.,600.]
    assert r['windows']['full']['original_19']['targets'][8]==0
    assert p==before


def test_event_windows_include_prefix_cost_and_do_not_relabel_original_full():
    a=api(); p=payload([segment(0,360),segment(360,960,loss=3,disconnected=1)],requested=960)
    p['scenario'].update(first_event_minute=360.,normal_history_minutes=360.,
        fault_start_minute=360.,fault_duration_minutes=180.,faults={'GRID':0.},
        shock_start_minute=375.,shock_duration_minutes=90.,shock={'e':.25})
    r=a.analyze_payload(p,None)
    assert r['windows']['normal_prefix']['window_minutes']==[0.,360.]
    assert r['windows']['event_first_600']['window_minutes']==[360.,960.]
    assert r['windows']['event_first_600']['observed_prefix']['total_ENS_kWh'][2][0]==30.
    assert r['windows']['full']['window_minutes']==[0.,600.]
    assert r['windows']['fault_active']['window_minutes']==[360.,540.]
    assert r['windows']['registered_full']['original_19']['accounting_cost_USD']==4.
    # No plant/tariff env was supplied; the original actual-fee contract is null.
    assert r['windows']['registered_full']['original_19']['actual_energy_fee_USD'] is None
    assert r['matching_semantics']['policy_dependent_prefix_states_possible']


def test_unobserved_model_domain_tail_stays_null_and_p3_false_is_not_coverage():
    a=api(); p=payload([segment(0,30),segment(30,45,loss=2,disconnected=1)],requested=2880)
    p['summary']['status']='unsupported_model_regime'
    p['error']={'type':'DieselDomainError','message':'speed crossed declared range'}
    r=a.analyze_payload(p,None)
    assert r['windows']['registered_full']['all_registered'] is None
    assert r['model_domain']['unknown_tail_minutes']==[45.,2880.]
    assert r['model_domain']['termination']['type']=='DieselDomainError'
    assert r['thermal_coverage']['P3']['disturbed_temperature_C'] is None
    assert r['thermal_coverage']['P3']['temperature_violation'] is None
    assert r['thermal_coverage']['P3']['status']=='not_modeled'
    json.dumps(r,allow_nan=False)


def test_diesel_segment_energies_and_domain_metadata_are_retained_without_fake_tail():
    a=api(); s=segment(0,60)
    s['diesel_local_response']=dict(electrical_work_kWh=10.,mechanical_work_kWh=10.2,
        loss_work_kWh=.3,rotor_delta_kWh=-.1,speed_start_pu=1.,speed_end_pu=.99,
        min_speed_pu=.98,max_speed_pu=1.01,validity_domain=[.9,1.1],energy_residual_kWh=0.)
    r=a.analyze_payload(payload([s]),None)
    assert r['diesel_local_response']['observed_prefix']['electrical_work_kWh']==10.
    assert r['diesel_local_response']['observed_prefix']['mechanical_work_kWh']==10.2
    assert r['diesel_local_response']['observed_prefix']['rotor_delta_kWh']==-.1
    assert r['diesel_local_response']['registered_full'] is None
    assert r['timelines']['diesel_local_response'][0]['min_speed_pu']==.98


def test_export_unknown_registered_tail_even_when_old_full600_complete(tmp_path):
    a=api(); r=a.analyze_payload(payload([segment(0,600)],requested=2880),None)
    receipt=a.export_rows([dict(scenario_id='s',policy_id='p',analysis=r)],tmp_path)
    assert receipt['unknown_registered_full_trajectory_count']==1
    assert receipt['unknown_full_trajectory_count']==0
    assert (tmp_path/'DIESEL_LOCAL_RESPONSE.csv').exists()
    assert (tmp_path/'THERMAL_COVERAGE.csv').exists()
    assert receipt['physical_episodes_started']==0 and receipt['additional_raw_opens']==0
    with pytest.raises(FileExistsError):a.export_rows([r],tmp_path)


def test_real_rotor_receipt_retains_shaft_budget_and_speed_extrema_separately_from_pe_fee():
    a=api(); s=segment(0,600)
    s['diesel_local_response']=dict(electrical_work_kWh=10.,mechanical_work_kWh=10.2,
        loss_work_kWh=.3,rotor_delta_kWh=-.1,speed_min_pu=.98,speed_max_pu=1.01,
        fuel_budget_debit_kWh_equivalent=10.2,original_electric_fee_USD=3.,shaft_equivalent_fee_USD=3.06,
        state_after={'mechanical_work_kWh':99.})
    r=a.analyze_payload(payload([s]),None)['diesel_local_response']['observed_prefix']
    assert r['fuel_budget_debit_kWh_equivalent']==10.2
    assert r['original_electric_fee_USD']==3. and r['shaft_equivalent_fee_USD']==3.06
    assert r['speed_min_pu']==.98 and r['speed_max_pu']==1.01
    assert r['mechanical_work_kWh']==10.2  # Never sum the nested cumulative99 ledger.


def actual_command_prefix():
    path=Path(__file__).resolve().parents[1]/'outputs/v15_engineering_operation_20261017_run01/review/command_identity_evidence/ACTUAL_PREFIX_FIXTURE.json'
    value=json.loads(path.read_text(encoding='utf8'))
    p=value['payload']
    # The saved fixture is a strict observed prefix, never an invented tail.
    p['summary']['observed_until_minute']=p['segments'][-1]['end_minute']
    p['events']=[e for e in p['events'] if e['time_minute']<=p['summary']['observed_until_minute']]
    return p


def test_actual_saved_core_string_sort_is_migrated_without_reassigning_active_command():
    a=api();p=actual_command_prefix();before=copy.deepcopy(p)
    from tools.executed_service.timeline import extract_command_timeline
    from tools.control_improvement.branches import window_targets
    # Native exporter lexical core:13/core:9 order is the observed old defect.
    with pytest.raises(ValueError,match='Segment command_id disagrees'):
        extract_command_timeline(p)
    r=a.analyze_payload(p,None)
    intervals=r['timelines']['execution_intervals']
    assert next(x for x in intervals if x['command_id']==1)['observed_execution_minutes']==0.
    assert next(x for x in intervals if x['command_id']==2)['observed_execution_minutes']==15.
    assert r['event_order_migration']['reordered_same_time_core_events']>0
    assert r['windows']['full']['original_19']==window_targets(p,0.,600.,None)
    assert p==before


def test_true_segment_command_disagreement_still_fails_after_order_migration():
    a=api();p=actual_command_prefix();p['segments'][1]['command_id']=1
    with pytest.raises(ValueError,match='Segment command_id disagrees'):
        a.analyze_payload(p,None)


def test_duplicate_native_core_sequence_is_not_silently_sorted_or_dropped():
    a=api();p=actual_command_prefix()
    core=[e for e in p['events'] if str(e.get('event_id','')).startswith('core:')]
    core[-1]['event_id']=core[0]['event_id']
    with pytest.raises(ValueError,match='[Dd]uplicate.*core|core.*[Dd]uplicate'):
        a.analyze_payload(p,None)
