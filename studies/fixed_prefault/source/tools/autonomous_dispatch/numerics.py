"""Original-unit solver presentation for the frozen joint planner chain.

Only optional positive row scaling is disabled. Canonical arrays, objectives,
lex locks, entailed bounds, backend finite retries and acceptance thresholds are
the original objects/procedures. No frozen module globals are modified.
"""
from __future__ import annotations
from types import FunctionType

from hospital_ies.solver_diagnostics import solve_layer as _original_solve_layer
from . import planner, planner_connections, planner_margin

NUMERICAL_CONTRACT_ID = 'unscaled_original_rows.v1'
_options = planner_margin._options


def _unscaled_layer(*args, **kwargs):
    kwargs['row_scaling'] = False
    return _original_solve_layer(*args, **kwargs)


def _bind(function, **dependencies):
    namespace = dict(function.__globals__, solve_layer=_unscaled_layer, **dependencies)
    result = FunctionType(function.__code__, namespace, function.__name__,
                          function.__defaults__, function.__closure__)
    result.__kwdefaults__ = None if function.__kwdefaults__ is None else dict(function.__kwdefaults__)
    return result


# Every compatibility delegation stays inside this isolated numerical route.
_frozen_solve_joint = _bind(planner.solve_joint)
_connections_solve_joint = _bind(planner_connections.solve_joint,
                               _frozen_solve_joint=_frozen_solve_joint)
_margin_solve_joint = _bind(planner_margin.solve_joint,
                          _connections_solve_joint=_connections_solve_joint)


def solve_joint(row, plant, loads, availability, state, dt_h,
                grid_import_limit_kW, diesel_start_available, gt_ramp_kW_per_h,
                solver_time_limit_sec=6., config=None, tracking_targets=None,
                security_gt_penalty_USD_per_kWh=0., execution_contract=None, *,
                joint_options=None, risk_loads=None, retention_eligible=False, connection_mode=None):
    result = _margin_solve_joint(row, plant, loads, availability, state, dt_h,
        grid_import_limit_kW, diesel_start_available, gt_ramp_kW_per_h,
        solver_time_limit_sec, config, tracking_targets, security_gt_penalty_USD_per_kWh,
        execution_contract, joint_options=joint_options, risk_loads=risk_loads,
        retention_eligible=retention_eligible, connection_mode=connection_mode)
    result['joint_diagnostic'].update(numerical_contract_id=NUMERICAL_CONTRACT_ID,
        solver_row_scaling=False, canonical_problem_changed=False,
        original_backend_validation_thresholds_unchanged=True)
    return result
