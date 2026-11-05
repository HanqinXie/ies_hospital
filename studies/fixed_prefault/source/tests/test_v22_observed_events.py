"""Versioned event metadata exports observed boundaries without future scripts."""
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src'),str(ROOT/'tests')]
from test_sci_environment import scenario

def test_new_and_inherited_external_nodes_are_limited_to_actual_observed_prefix():
    from tools.model_closure_v22.environment import observed_model_events
    s=scenario(aux=True);s.update(fault_start_minute=5.,fault_duration_minutes=5.,shock_by_zone={'OR1_FLR_2':{'c':.5}},shock_start_minute=6.,shock_duration_minutes=6.,
        model_closure_events=[dict(time_minute=5.,availability={'STATION_AC':0.}),dict(time_minute=9.,availability={'STATION_AC':1.})])
    events=observed_model_events(s,8.,has_segments=True,events=[],research_stress_proxy=False)
    assert len(events)==3
    assert all(x['time_minute']<=8. and x['parent_event_id'] is None for x in events)
    assert any(x['kind']=='m21_exogenous_auxiliary_availability_start' for x in events)
    assert any(x['kind']=='m21_exogenous_zonal_clinical_demand_start' for x in events)
    assert any(x['kind']=='m22_exogenous_availability_changed' and x['details']['availability']=={'STATION_AC':0.} for x in events)
    assert observed_model_events(s,8.,has_segments=False,events=[],research_stress_proxy=False)==[]

def test_explicit_research_trip_keeps_legacy_class_and_never_relabels_interlocks():
    from tools.model_closure_v22.environment import observed_model_events
    raw=[dict(event_id='core:1',time_minute=5.,kind='protection_trip',cause_class='residual_stress_protection'),
         dict(event_id='core:2',time_minute=5.,kind='no_export_interlock',cause_class='no_export_isolation')]
    exported=observed_model_events({},6.,has_segments=True,events=raw,research_stress_proxy=True)
    assert exported[0]['cause_class']=='research_stress_proxy'
    assert exported[0]['legacy_cause_class']=='residual_stress_protection'
    assert exported[0]['mechanism_class']=='research_proxy' and exported[0]['actual_not_nameplate_overload']
    assert exported[1]==raw[1]
    assert raw[0]['cause_class']=='residual_stress_protection'
    ordinary=observed_model_events({},6.,has_segments=True,events=raw,research_stress_proxy=False)
    assert 'mechanism_class' not in ordinary[0]
