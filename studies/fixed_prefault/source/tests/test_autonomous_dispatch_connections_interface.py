import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))


def api():
    return importlib.import_module('tools.autonomous_dispatch.connections')


def test_connection_representation_is_explicit_and_old_validator_is_unchanged():
    old = importlib.import_module('tools.autonomous_dispatch.contracts')
    action = {'joint': {'options': {'variant': 'coupled', 'connection_mode': 'retain_when_no_gt'}}}
    with pytest.raises(ValueError):
        old.validate_action(action)
    assert api().validate_action(action) == action
    with pytest.raises(ValueError):
        api().validate_action({'joint': {'options': {'connection_mode': 'instant_diesel_drop'}}})


def test_retention_requires_actual_no_gt_and_no_pending_startup():
    a = api()
    core = SimpleNamespace(state=SimpleNamespace(gt_prev=np.zeros(3)), gt_start_pending={})
    assert a.retention_eligible(core)
    core.state.gt_prev[0] = 100.
    assert not a.retention_eligible(core)
    core.state.gt_prev[:] = 0.
    core.gt_start_pending = {0: {'ready': 30.}}
    assert not a.retention_eligible(core)


def test_queue_keeps_planned_shortfall_distinct_from_actual_contact_commands():
    a = api()
    core = a.ConnectionTransitionCore.__new__(a.ConnectionTransitionCore)
    core.failed=False;core.time=15.;core.sequence=0;core.pending_commands=[];core.events=[]
    core.env=SimpleNamespace(dt_h=.25)
    core.data_at=lambda t:(1,None,np.ones((4,3)),{})
    core.delayed_at=lambda t:(True,27.5)
    dispatch={'gt_electric_kW':[0.]*3,'grid_import_kW':0.,'diesel_kW':200.,
              'boiler_steam_lbph':300.,'absorption_cooling_kW':0.,'electric_chiller_cooling_kW':[0.]*3}
    lost=np.array([[0.]*3,[0.]*3,[10.]*3,[20.]*3])
    core._solve_joint=lambda request,loads,f:dict(action=dispatch,lost=lost,planned_current_action=dispatch,
        command_disconnected=np.zeros((4,3)),joint_diagnostic={'planned_unserved_kW':lost.tolist(),
        'command_disconnected_kW':np.zeros((4,3)).tolist()},optimizer_warning='')
    command=core.generate_command({'joint':{'options':{'variant':'coupled','connection_mode':'retain_when_no_gt'}}})
    assert command['arrival_minute']==27.5
    assert command['disconnected']==np.zeros((4,3)).tolist()
    assert command['joint_dispatch']['diagnostic']['planned_unserved_kW']==lost.tolist()
    assert command==core.pending_commands[0]==core.events[-1]['command']


def test_second_runner_preserves_first_version_source_binding():
    old=importlib.import_module('tools.autonomous_dispatch.closed_loop')
    before=old._source_binding()
    new=importlib.import_module('tools.autonomous_dispatch.closed_loop_v2')
    assert old._source_binding()==before
    for name in ('planner_connections','connections','closed_loop_v2'):
        assert f'tools/autonomous_dispatch/{name}.py' in new.SOURCE_FILES
    assert new.SCHEMA!=old.SCHEMA
