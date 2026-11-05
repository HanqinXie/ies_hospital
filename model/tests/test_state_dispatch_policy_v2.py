import importlib
from pathlib import Path
import sys

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from test_state_dispatch_policy import features


def api():
    try:
        return importlib.import_module('tools.state_dispatch.policy_v2')
    except ModuleNotFoundError:
        pytest.fail('Versioned state-dispatch refinement is missing')


def test_island_request_cannot_displace_gt_to_unavailable_grid():
    a = api()
    plan = [250., 250., 0., 300., 0., 100.]
    targets, detail = a.spread_targets(plan, [300.]*3, [1.]*3, [0.]*3,
                                      fraction=.55, minimum_fraction=.4,
                                      grid_limit_kW=0.)
    assert targets[3] == pytest.approx(0.)
    assert targets[:3] == pytest.approx([800./3]*3)
    assert sum(targets[:4]) == pytest.approx(sum(plan[:4]))
    assert detail['grid_limit_kW'] == 0.


def test_high_pressure_request_respects_current_licensed_import_cap():
    a = api()
    targets, _ = a.spread_targets([300., 300., 300., 850., 0., 100.],
                                  [300.]*3, [1.]*3, [0.]*3,
                                  fraction=.55, minimum_fraction=.4,
                                  grid_limit_kW=850.)
    assert targets == pytest.approx([300., 300., 300., 850., 0., 100.])


def test_cooling_dominant_guard_uses_observed_load_not_scenario_identity():
    a = api()
    p = a.StateDispatchPolicy({}, 'guard_probe')
    assert p.select_route(features(cooling=160., electric=100.))['guard_active'] is True
    assert p.select_route(features(cooling=20., electric=100.))['guard_active'] is False
    island = features(cooling=160., electric=100.)
    island['availability_GRID'] = 0.
    assert p.select_route(island)['guard_active'] is False


def test_service_recovery_mode_can_exit_with_persistent_gt_lockout():
    a = api()
    tracker = a.StateModeTracker(a.validate_config({}))
    f = features()
    tracker.update(f)
    f['availability_GT_1'] = 0.
    assert tracker.update(f)['mode'] == 'emergency'
    assert tracker.update(f)['mode'] == 'recovery'
    assert tracker.update(f)['mode'] == 'prepare'
    assert f['availability_GT_1'] == 0.


def test_refinement_does_not_mutate_registered_v1_source():
    import hashlib
    root = Path(__file__).resolve().parents[1]
    names = ('tools/state_dispatch/policy.py', 'tools/state_dispatch/closed_loop.py')
    before = {name: hashlib.sha256((root/name).read_bytes()).hexdigest() for name in names}
    a = api()
    p = a.StateDispatchPolicy({}, 'isolation_probe')
    p.select_route(features())
    for name in names:
        assert hashlib.sha256((root/name).read_bytes()).hexdigest() == before[name]
