"""Prevent misleading legacy metadata and future M21 boundary exports."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]

def test_zonal_demand_is_not_exported_as_a_false_constant_p0_multiplier():
    from tools.sci_engineering.contracts import normalize_study_scenario
    s=dict(id='zone',source_block_id='b',family_id='b',stage=4,start_hour=1,
        event_class='L4b',duration_steps=16,step_minutes=15.,role='confirmation',
        shock_by_zone={'OR1':{'e':.5}},exogenous_stream_id='declared_upstream_id')
    value=normalize_study_scenario(s)
    assert value['exogenous_stream_id']==s['exogenous_stream_id']
    assert value['purpose']=='confirmation'
    assert 'actual_p0_load_multiplier' not in value
    assert value['m21_actual_demand_semantics']=='hour-dependent original requests and zonal increments in segment ledger'

def test_observed_zonal_and_auxiliary_boundaries_stop_at_actual_observation():
    from tools.sci_engineering.contracts import observed_engineering_events
    scene=dict(duration_steps=16,step_minutes=15.,fault_start_minute=60.,fault_duration_minutes=120.,
        shock_start_minute=60.,shock_duration_minutes=120.,auxiliary_faults={'AUX_CHW_PUMP':0.},
        shock_by_zone={'OR1':{'c':.5}})
    prefix=observed_engineering_events(scene,75.,has_segments=True)
    assert len(prefix)==2 and all(e['time_minute']==60. for e in prefix)
    assert all(e['origin']=='exogenous' for e in prefix)
    complete=observed_engineering_events(scene,190.,has_segments=True)
    assert len(complete)==4
    assert observed_engineering_events(scene,0.,has_segments=False)==[]
