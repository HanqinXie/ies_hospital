import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src'),str(ROOT/'tests')]
from test_v22_core_environment import make
from test_sci_environment import scenario

def test_release_requires_actual_current_feasibility_witness():
    from tools.model_closure_v22.environment import decision_snapshot
    from tools.model_closure_v22.policy import SciPolicy
    e=make();e.reset(scenario=scenario(),seed=5)
    current=decision_snapshot(e);current['model_closure_v22']['current_non_diesel_feasibility']={'ready':False,'status':'no_station_path'}
    current['features']['grid_outage_elapsed_minutes']=200.
    action,diagnostic=SciPolicy('A',plant=e.plant).decide([current])
    assert action['state_coordinated_joint']['coordination']['critical_non_diesel_ready'] is False
    assert diagnostic['release_witness']['status']=='no_station_path'

def test_future_witness_clock_is_rejected():
    from tools.model_closure_v22.environment import decision_snapshot
    from tools.model_closure_v22.policy import SciPolicy
    import pytest
    e=make();e.reset(scenario=scenario(),seed=5);current=decision_snapshot(e)
    current['model_closure_v22']['available_time_minute']=current['decision_time_min']+1.
    with pytest.raises(ValueError):SciPolicy('A',plant=e.plant).decide([current])
