"""Production saved telemetry must preserve hardware/topology and causal clocks."""
import copy
import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))

def api():
    return importlib.import_module('tools.execution_recovery.analysis')._m18_source_telemetry

def segment(start=0., bus='ISLAND', grid=0., diesel=20.):
    return dict(start_minute=start,end_minute=start+1.,
        hardware_availability={'GRID':1.},availability={'GRID':0.},
        arrived_source_permissions={'grid_import_kW':850.,'diesel_kW':100.},
        effective_source_setpoints={'grid_import_kW':0.,'diesel_kW':100.},
        execution_limits={'grid_bus_state':bus,'grid_transfer_remaining_minutes':None},
        action={'grid_import_kW':grid,'diesel_kW':diesel})

def test_hardware_recovery_does_not_become_grid_supply_or_permission():
    s=segment(); before=copy.deepcopy(s)
    row=api()({'segments':[s],'events':[]})[0]
    assert row['raw_hardware_grid_availability']==1.
    assert row['topology_effective_grid_availability']==0.
    assert row['arrived_grid_import_permission_kW']==850.
    assert row['effective_grid_import_limit_kW']==row['actual_grid_import_kW']==0.
    assert row['actual_diesel_kW']==20.
    assert row['grid_diesel_parallel_observed'] is False
    assert row['source_telemetry_extraction_status']=='saved_v18_fields_complete'
    assert s==before

def test_future_proof_cannot_backfill_earlier_interval():
    rows=api()({'segments':[segment(0),segment(10)],'events':[
        {'kind':'grid_return_readiness_proved','time_minute':10.,'reason':'actual_saved_proof'}]})
    assert rows[0]['last_return_proof_status'] is None
    assert rows[1]['last_return_proof_status']=='passed'
    assert rows[1]['last_return_proof_age_minutes']==0.

def test_missing_raw_hardware_stays_unknown():
    s=segment();del s['hardware_availability']
    row=api()({'segments':[s],'events':[]})[0]
    assert row['raw_hardware_grid_availability'] is None
    assert row['source_telemetry_extraction_status']=='missing_saved_v18_fields'

def test_actual_parallel_or_open_bus_output_is_explicit():
    rows=api()({'segments':[segment(bus='TRANSFER_OPEN',grid=10.,diesel=5.)],'events':[]})
    assert rows[0]['grid_diesel_parallel_observed'] is True
    assert rows[0]['topology_port_violation_observed'] is True


def test_seen_propagation_off_pair_retains_original_metadata_without_inventing_trip():
    module=importlib.import_module('tools.execution_recovery.analysis')
    scenario=dict(enable_propagation=False,v17_matched_propagation_ablation=True,
                  propagation_pair_id='registered_pair',faults={'GRID':0.})
    events=[dict(cause_class='functional_dependency',device='HRSG_1')]
    result=module._propagation_summary(scenario,events)
    assert result['matched_OFF'] is True
    assert result['allowed'] is False
    assert result['propagation_pair_id']=='registered_pair'
    assert result['residual_stress_protection_events']==[]


def test_explicit_v18_pair_metadata_precedes_legacy_and_actual_trip_remains_recorded():
    module=importlib.import_module('tools.execution_recovery.analysis')
    scenario=dict(enable_propagation=True,v17_matched_propagation_ablation=True,
                  v18_matched_propagation_ablation=False,faults={'GRID':0.})
    event=dict(cause_class='residual_stress_protection',device='GT_1')
    result=module._propagation_summary(scenario,[event])
    assert result['matched_OFF'] is False
    assert result['allowed'] is True
    assert result['residual_stress_protection_events']==[event]
