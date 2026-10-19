"""A command at the same timestamp reuses the original anchored ramp window.

The old elapsed-only readiness number became zero after a real arrival and
contradicted the next hourly minimum-stable floor. These are offline current
states only; they never reset/step an environment or replay a physical episode.
"""
import copy
import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
from hospital_ies.model import Plant, gt_level_properties
from hospital_ies.resilience import RuntimeState
from test_engineering_planner import problem
from test_operating_readiness import current

NEW_SCHEMA = 'operating_response.readiness.v15.ramp_window_v2'


def _core(*, executed=(200., 0., 0.), actual=None, now=60., previous=60.,
          anchor=(150., 0., 0.), budget=75.):
    module = importlib.import_module('tools.operating_response.environment')
    core = object.__new__(module.OperatingCommandCore)
    core.time = now
    core.env = SimpleNamespace(dt_minutes=15., config={'gt_ramp_kW_per_hour': 900.})
    core.executed = dict(gt_electric_kW=list(executed), grid_import_kW=850., diesel_kW=0.,
                         boiler_steam_lbph=0., absorption_cooling_kW=0.,
                         electric_chiller_cooling_kW=[0., 0., 0.])
    core.env.state = SimpleNamespace(gt_prev=np.array(executed if actual is None else actual),
        disconnected_loads=np.zeros((4, 3)), diesel_status='OFF', last_diesel_kW=0.)
    core.gt_clocks = SimpleNamespace(earliest_start=lambda i, t: t, can_stop=lambda i, t: i != 1)
    core.gt_start_pending = {}; core.interlock_latched = {}; core.pending_commands = []; core.segments = []
    core.ramp_time = previous
    core.ramp_anchor = None if anchor is None else np.array(anchor)
    core.ramp_budget = budget
    core.data_at = lambda minute: (None, None, None, {f'GT_{i+1}': 1. for i in range(3)})
    return core


@pytest.mark.parametrize('policy', ['K1', 'K2'])
def test_recorded_same_time_arrival_and_hourly_floor_has_a_legal_current_command(policy):
    relative = Path('outputs/v15_engineering_operation_20261017_run01/experiments/dev_v2_incidents_M14_engineering_day130_L4b')
    source = None
    for batch in (ROOT / relative, ROOT / 'evidence' / relative):
        files = list(batch.glob('pairs/*' + policy + '*/RESULT.json'))
        assert len(files) <= 1, f'Ambiguous saved {policy} failure in {batch}'
        if files:
            source = files[0]
            break
    assert source is not None, f'Required saved {policy} failure absent in canonical and evidence/{relative}'
    record = json.loads(source.read_text(encoding='utf-8'))
    assert record['status'] == 'model_constraint_infeasible'
    d = record['error']['diagnostic']
    raw_state = copy.deepcopy(d['state'])
    for key in ('gt_prev', 'overload_minutes', 'disconnected_loads'):
        raw_state[key] = np.asarray(raw_state[key], float)
    raw_state['extra_faulted'] = set(raw_state['extra_faulted'])
    state = RuntimeState(**raw_state)
    local = d['local_execution_state']
    core = _core(executed=d['executed_action']['gt_electric_kW'], actual=state.gt_prev,
        now=d['time_minute'], previous=local['ramp_time'],
        anchor=local['ramp_anchor'], budget=local['ramp_budget'])
    core.env.state = state
    core.executed = copy.deepcopy(d['executed_action'])
    core.env.config = d['config']
    core.data_at = lambda minute: (None, None, None, d['availability'])
    core.segments = d['completed_segments']
    readiness = core.readiness_context()
    joint = d['command']['operating_joint']
    planner = importlib.import_module('tools.operating_response.planner')
    result = planner.solve_joint(
        row=SimpleNamespace(timestamp=pd.Timestamp(d['row']['timestamp']),
                            outdoor_temperature_C=d['row']['outdoor_temperature_C']),
        plant=Plant(**d['plant']), loads=np.asarray(d['loads_kW']), availability=d['availability'],
        state=state, dt_h=d['dt_h'], grid_import_limit_kW=850., diesel_start_available=False,
        gt_ramp_kW_per_h=d['config']['gt_ramp_kW_per_hour'], solver_time_limit_sec=6.,
        config=d['config'], execution_contract=d['execution_contract'],
        joint_options=joint['options'], operating_options=joint['operating_options'],
        risk_loads=joint['risk_loads_kW'], readiness_context=readiness)
    floor = .4 * gt_level_properties(Plant(**d['plant']), d['row']['outdoor_temperature_C'])[0][-1]
    assert floor > state.gt_prev[0] + .38
    assert min(result['action']['gt_electric_kW'][:2]) >= floor - 1e-7
    assert max(map(abs, result['energy_balance_residuals'])) <= 1e-4
    assert result['joint_diagnostic']['current_priority_locks_unchanged'] is True


def test_same_time_context_keeps_original_anchor_instead_of_actual_recentering():
    # Original legal interval is [75,225], while actual+-75 would add an
    # unauthorized [225,275] range. Actual supply remains an independent field.
    context = _core(executed=(200., 0., 0.), actual=(199., 0., 0.)).readiness_context()
    assert context['schema'] == NEW_SCHEMA
    assert context['gt_ramp_anchor_kW'] == [150., 0., 0.]
    assert context['gt_ramp_budget_kW'] == 75.
    assert context['actual_gt_kW'] == [199., 0., 0.]
    assert 'gt_ramp_available_kW' not in context


@pytest.mark.parametrize('now,previous,old_budget,expected_budget', [
    (60., 60., 75., 75.), (60.5, 60., 225., 7.5),
    (75., 60., 1000., 225.), (60., None, 0., 225.)])
def test_clock_window_reuses_only_same_time_budget_and_new_time_uses_original_elapsed(
        now, previous, old_budget, expected_budget):
    readiness = importlib.import_module('tools.operating_response.readiness')
    helper = getattr(readiness, 'command_ramp_window', None)
    assert callable(helper), 'Missing current-clock shared ramp-window helper'
    value = helper(executed_gt_kW=[200., 0., 0.], observation_time_minute=now,
        previous_ramp_time_minute=previous,
        previous_ramp_anchor_kW=None if previous is None else [150., 0., 0.],
        previous_ramp_budget_kW=old_budget, dt_minutes=15., gt_ramp_kW_per_h=900.)
    expected_anchor = [150., 0., 0.] if previous == now else [200., 0., 0.]
    assert value == dict(gt_ramp_anchor_kW=expected_anchor, gt_ramp_budget_kW=expected_budget)


def test_planner_uses_anchored_bounds_without_giving_actual_a_new_symmetric_budget(problem, monkeypatch):
    planner = importlib.import_module('tools.operating_response.planner')
    context = current((200., 0., 0.))
    context.pop('gt_ramp_available_kW', None)
    context.update(schema=NEW_SCHEMA, gt_ramp_anchor_kW=[150., 0., 0.], gt_ramp_budget_kW=75.)
    problem['state'].gt_prev[:] = [200., 0., 0.]
    observed = {}

    class CapturedDomain(Exception):
        pass

    def capture(*args, **kwargs):
        matrix, lower, upper = planner.flatten_constraints(kwargs['constraints'])
        for index, row in enumerate(kwargs['rows']):
            if row['name'] == 'GT_1.ordinary_ramp':
                observed.update(lower=float(lower[index]), upper=float(upper[index]),
                                coefficients=matrix[index].toarray().reshape(-1).tolist())
        raise CapturedDomain

    monkeypatch.setattr(planner, 'solve_layer', capture)
    with pytest.raises(CapturedDomain):
        planner.solve_joint(**problem, readiness_context=context)
    assert observed['lower'] == 75.
    assert observed['upper'] == 225.
    assert observed['coefficients'][0] == 1.
    assert sum(abs(x) for x in observed['coefficients'][1:]) == 0.


def test_new_strict_context_explicitly_rejects_legacy_elapsed_only_ramp():
    readiness = importlib.import_module('tools.operating_response.readiness')
    legacy = current()
    legacy['schema'] = 'operating_response.readiness.v15'
    legacy.pop('gt_ramp_anchor_kW', None)
    legacy.pop('gt_ramp_budget_kW', None)
    legacy['gt_ramp_available_kW'] = [225.] * 3
    with pytest.raises(ValueError):
        readiness.validate_context(legacy)
