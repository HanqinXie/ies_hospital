"""Pure calendar migration checks; these are not physical evidence."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import calendar_plan as m


def test_latest_individual_tower_and_primary_pump_are_declared_causes():
    plan=m.candidate_plan()
    rows={r['event_id']:r for r in plan['fault_intervals']}
    assert rows['E5']['device']=='TOWER_1'
    assert rows['E8']==dict(event_id='E8',device='EC_1_PRIMARY_PUMP',
        start_minute=m._minute(9,15,14),end_minute=m._minute(9,15,15),availability=0.)
    assert plan['anchor_rule']['events']==['E1','E2','E3','E8']
    assert m.actual_production({'electric_chiller_cooling_kW':[23.,0.,0.]},'EC_1_PRIMARY_PUMP')==23.


def test_recovery_extension_is_preserved_and_new_exposure_is_exactly_one_hour():
    plan=m.candidate_plan()
    rows={r['event_id']:r for r in plan['fault_intervals']}
    assert rows['E6a']['device']==rows['E6b']['device']=='TOWER'
    assert rows['E6a']['availability']==rows['E6b']['availability']==.01
    assert m.exposure_hours(plan)==dict(N=8012.,D=669.75,F=76.,FD=2.25)
