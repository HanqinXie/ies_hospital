"""A numerical retry remains visible in a successful physical call."""
from types import SimpleNamespace
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]


def test_successful_call_keeps_all_backend_attempts(monkeypatch):
    from hospital_ies.state_transition import TransitionCore
    import hospital_ies.state_transition as module
    layers = [{'rank': 0, 'attempts': [{'status': 2}, {'status': 0}]}]
    result = {'solver_backend_layers': layers}
    monkeypatch.setattr(module, '_solve_milp', lambda *args, **kwargs: result)
    core = object.__new__(TransitionCore)
    core.env = SimpleNamespace(plant=None, scenario={'grid_limit_kW': 250},
                               execution_contract={}, config={'gt_ramp_kW_per_hour': 900,
                                                             'grid_import_limit_kW': 250,
                                                             'solver_time_limit_sec': 6})
    core.env.state = None
    core.time = 15.
    core.data_at = lambda t: (None, None, None, None)
    core.diesel_available = lambda t, f: False
    core.solver_calls = []
    assert core._solve(None, {}, .25, 'test', {}) is result
    assert core.solver_calls[0]['solver_backend_layers'] == layers
