"""Causal one/two-state thermal diagnostics with an actual-delivery energy port.

Positive sensible_delta adds heat relative to normal. It already includes the
complete zone delivered sensible HVAC boundary. Additional recovery delivery
must be excluded from sensible_delta and passed exactly once to commit.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from functools import lru_cache
import math

import numpy as np
from scipy.linalg import expm


def _scalar(name, value, positive=False, nonnegative=False):
    if isinstance(value, bool):
        raise ValueError(f'Invalid {name}')
    value = float(value)
    if not math.isfinite(value) or (positive and value <= 0) or (nonnegative and value < 0):
        raise ValueError(f'Invalid {name}')
    return value


@dataclass(frozen=True)
class TwoState:
    air_delta_C: float
    mass_delta_C: float
    air_capacity_kWh_K: float
    mass_capacity_kWh_K: float
    air_mass_conductance_kW_K: float
    mass_outdoor_conductance_kW_K: float

    def __post_init__(self):
        for name in self.__dataclass_fields__:
            object.__setattr__(self, name, _scalar(name, getattr(self, name),
                               positive=name not in ('air_delta_C', 'mass_delta_C')))


@dataclass(frozen=True)
class ThermalCommit:
    state: TwoState
    sensible_input_kWh: float
    actual_extra_cooling_kWh: float
    passive_heat_rejection_kWh: float
    thermal_energy_change_kWh: float
    balance_residual_kWh: float


@lru_cache(maxsize=4096)
def _discretize(cair, cmass, gam, gout, dt):
    matrix = np.array([[-gam / cair, gam / cair], [gam / cmass, -(gam + gout) / cmass]])
    drive = np.array([1. / cair, 0.])
    transition = expm(matrix * dt)
    response = np.linalg.solve(matrix, (transition - np.eye(2)) @ drive)
    return matrix, drive, transition, response


def _kernel(state, dt_h):
    dt = _scalar('dt_h', dt_h, positive=True)
    return _discretize(state.air_capacity_kWh_K, state.mass_capacity_kWh_K,
                       state.air_mass_conductance_kW_K, state.mass_outdoor_conductance_kW_K, dt)


def commit(state, actual_extra_cooling_kW, dt_h, *, sensible_delta_kW=0., ventilation_delta_kW=0.):
    extra = _scalar('actual_extra_cooling_kW', actual_extra_cooling_kW, nonnegative=True)
    sensible = _scalar('sensible_delta_kW', sensible_delta_kW)
    if _scalar('ventilation_delta_kW', ventilation_delta_kW) != 0.:
        raise ValueError('Complete delivered HVAC sensible boundary already includes mechanical ventilation')
    matrix, drive, transition, response = _kernel(state, dt_h)
    initial = np.array([state.air_delta_C, state.mass_delta_C])
    net = sensible - extra
    final = transition @ initial + response * net
    integral = np.linalg.solve(matrix, (transition - np.eye(2)) @ initial + (response - dt_h * drive) * net)
    passive = state.mass_outdoor_conductance_kW_K * integral[1]
    change = (state.air_capacity_kWh_K * (final[0] - initial[0]) +
              state.mass_capacity_kWh_K * (final[1] - initial[1]))
    return ThermalCommit(replace(state, air_delta_C=float(final[0]), mass_delta_C=float(final[1])),
                         sensible * dt_h, extra * dt_h, float(passive), float(change),
                         float(net * dt_h - passive - change))


def recovery_request_kW(state, target_delta_C=0., known_horizon_h=1., max_power_kW=0.):
    """Current-state request only; no future event, weather, wall or repair input."""
    target = _scalar('target_delta_C', target_delta_C)
    maximum = _scalar('max_power_kW', max_power_kW, nonnegative=True)
    _, _, transition, response = _kernel(state, known_horizon_h)
    passive_end = transition @ np.array([state.air_delta_C, state.mass_delta_C])
    return min(maximum, max(0., float((passive_end[0] - target) / response[0])))


def predict(forcing, parameters, *, dt_h=1/12, order=2):
    values = np.asarray(forcing, dtype=float)
    if values.ndim != 1 or not np.isfinite(values).all():
        raise ValueError('Invalid causal thermal forcing')
    result = np.empty((len(values), order))
    if order == 1:
        capacity, conductance = parameters
        decay = math.exp(-conductance * dt_h / capacity)
        response = -math.expm1(-conductance * dt_h / capacity) / conductance
        delta = 0.
        for i, q in enumerate(values):
            delta = decay * delta + response * q
            result[i, 0] = delta
    elif order == 2:
        state = TwoState(0., 0., *parameters)
        _, _, transition, response = _kernel(state, dt_h)
        vector = np.zeros(2)
        for i, q in enumerate(values):
            vector = transition @ vector + response * q
            result[i] = vector
    else:
        raise ValueError('Only the preregistered one/two-state structures are permitted')
    return result


def bounded_fit(cases, *, order, lower, upper, starts):
    """Fit calibration only; retain every limited initial-value/convergence run."""
    from scipy.optimize import least_squares
    selected = [case for case in cases if case['split'] == 'calibration']
    if not selected or not 1 <= len(starts) <= 3:
        raise ValueError('Calibration cases and at most three preregistered starts required')
    arrays = []
    for case in selected:
        q = np.asarray(case['forcing_kW'], dtype=float)
        y = np.asarray(case['actual_delta_C'], dtype=float)
        if q.shape != y.shape or q.ndim != 1 or not np.isfinite(q).all() or not np.isfinite(y).all():
            raise ValueError('Invalid calibration arrays')
        arrays.append((q, y, case['dt_h']))
    def residual(parameters):
        return np.concatenate([predict(q, parameters, dt_h=dt, order=order)[:, 0] - y
                               for q, y, dt in arrays])
    attempts = []
    for initial in starts:
        fit = least_squares(residual, initial, bounds=(lower, upper), max_nfev=200,
                            ftol=1e-10, xtol=1e-10, gtol=1e-10)
        attempts.append(dict(initial=list(initial), parameters=fit.x.tolist(), success=bool(fit.success),
                             message=fit.message, nfev=int(fit.nfev), cost=float(fit.cost),
                             optimality=float(fit.optimality)))
    best = min(attempts, key=lambda item: item['cost'])
    return dict(order=order, parameters=best['parameters'], fit_case_ids=[c['case_id'] for c in selected],
                lower_bounds=list(lower), upper_bounds=list(upper), attempts=attempts,
                selected_cost=best['cost'], validation_used=False)


def paired_metrics(case, frame, predicted_delta):
    """Keep original thresholds and three consecutive 10min recovery points."""
    import pandas as pd
    event = pd.Timestamp(case['event_start'])
    recovery = event + pd.Timedelta(hours=case['outage_hours'])
    modeled = frame.baseline_temperature_C + np.asarray(predicted_delta)
    actual = frame.actual_temperature_C
    error = modeled - actual
    event_mask = (frame.index > event) & (frame.index <= recovery)
    recovery_mask = (frame.index > recovery) & (frame.index <= recovery + pd.Timedelta(hours=6))
    evaluate = frame.index > event
    def rmse(mask):
        return float(np.sqrt(np.mean(np.asarray(error[mask])**2))) if np.any(mask) else None
    def crossing(series):
        crossed = series[(series > frame.setpoint_C + 2. + 1e-7) & evaluate]
        return crossed.index[0] if len(crossed) else None
    observed_cross, modeled_cross = crossing(actual), crossing(modeled)
    def recover(delta):
        # Original physical duration is retained: 3 points on 10min grid.
        native = delta[(delta.index.minute % 10) == 0]
        for i in range(len(native) - 2):
            if native.index[i] > recovery and np.all(np.abs(native.iloc[i:i+3]) <= .2):
                return float((native.index[i] - recovery).total_seconds() / 60.)
        return None
    actual_recovery = recover(actual - frame.baseline_temperature_C)
    modeled_recovery = recover(modeled - frame.baseline_temperature_C)
    matching = (observed_cross is None) == (modeled_cross is None)
    crossing_error = (abs((modeled_cross - observed_cross).total_seconds()) / 60.
                      if observed_cross is not None and modeled_cross is not None else (0. if matching else None))
    return dict(event_rmse_C=rmse(event_mask), recovery_first6h_rmse_C=rmse(recovery_mask),
                maximum_absolute_error_C=float(abs(error[evaluate]).max()),
                first_crossing_error_minutes=crossing_error,
                recovery_time_error_minutes=(abs(actual_recovery-modeled_recovery)
                    if actual_recovery is not None and modeled_recovery is not None else None),
                observed_first_crossing=observed_cross.isoformat() if observed_cross is not None else None,
                modeled_first_crossing=modeled_cross.isoformat() if modeled_cross is not None else None,
                crossing_status=('no_crossing_both' if observed_cross is None and matching else
                                 ('matching_crossing' if matching else 'missed_or_false_crossing')),
                observed_recovery_minutes=actual_recovery, modeled_recovery_minutes=modeled_recovery,
                recovery_status=('observed_both' if actual_recovery is not None and modeled_recovery is not None
                                 else 'one_or_both_right_censored'),
                complete_input_interval_minutes=float((frame.index[1]-frame.index[0]).total_seconds()/60.), inputs_complete=True,
                recovery_phase_start=recovery.isoformat(),
                observation_end=frame.index[-1].isoformat())
