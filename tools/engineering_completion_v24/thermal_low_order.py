"""One bounded V24 air/furniture/construction countercheck, isolated from IES.

The air capacity is fixed to original IDF volume. The two positive storage
paths and the heat sink are passive. Their output is a conditional relative
temperature reconstruction, never a clinical or autonomous forecast.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from functools import lru_cache
import json
from pathlib import Path

import numpy as np
from scipy.linalg import expm
from scipy.optimize import least_squares


@lru_cache(maxsize=4096)
def _kernel(parameters, dt_h):
    cair, cfurniture, cconstruction, gtotal, fraction, gout = parameters
    if (not np.isfinite(parameters).all() or min(cair, cfurniture, cconstruction, gtotal, gout) <= 0
            or not 0 < fraction < 1 or not np.isfinite(dt_h) or dt_h <= 0):
        raise ValueError('Positive capacities/conductances and physical split required')
    gf, gs = gtotal*fraction, gtotal*(1-fraction)
    matrix = np.array([[-gtotal/cair, gf/cair, gs/cair],
                       [gf/cfurniture, -gf/cfurniture, 0.],
                       [gs/cconstruction, 0., -(gs+gout)/cconstruction]])
    drive = np.array([1/cair, 0., 0.])
    transition = expm(matrix*dt_h)
    response = np.linalg.solve(matrix, (transition-np.eye(3))@drive)
    return matrix, drive, transition, response


def commit(state, parameters, *, sensible_delta_kW, actual_extra_cooling_kW, dt_h):
    initial = np.asarray(state, dtype=float)
    if (initial.shape != (3,) or not np.isfinite(initial).all() or
            not np.isfinite([sensible_delta_kW, actual_extra_cooling_kW]).all() or actual_extra_cooling_kW < 0):
        raise ValueError('Invalid current state or actual delivered thermal rate')
    matrix, drive, transition, response = _kernel(tuple(parameters), dt_h)
    q = sensible_delta_kW-actual_extra_cooling_kW
    final = transition@initial+response*q
    integral = np.linalg.solve(matrix, (transition-np.eye(3))@initial + (response-dt_h*drive)*q)
    passive = parameters[5]*integral[2]
    change = np.dot(parameters[:3], final-initial)
    return dict(state=final, sensible_input_kWh=sensible_delta_kW*dt_h,
                actual_extra_cooling_kWh=actual_extra_cooling_kW*dt_h,
                passive_heat_rejection_kWh=float(passive), thermal_energy_change_kWh=float(change),
                balance_residual_kWh=float(q*dt_h-passive-change))


def predict(forcing, parameters, *, dt_h=1/6):
    values = np.asarray(forcing, dtype=float)
    if values.ndim != 1 or not np.isfinite(values).all():
        raise ValueError('Complete finite actual sensible rates required')
    _, _, transition, response = _kernel(tuple(parameters), dt_h)
    state, result = np.zeros(3), np.empty((len(values), 3))
    for index, q in enumerate(values):
        state = transition@state+response*q
        result[index] = state
    return result


def bounded_fit(cases, registration):
    selected = [c for c in cases if c['split'] == 'calibration' and c['season'] == 'summer']
    models = {}
    for zone in sorted({c['zone'] for c in selected}):
        group = [c for c in selected if c['zone'] == zone]
        bound = registration['bounds'][zone]
        air = bound['fixed_air_capacity_kWh_K']
        def residual(free):
            parameters = [air, *free]
            return np.concatenate([predict(c['forcing_kW'], parameters, dt_h=c['dt_h'])[:, 0] -
                                   c['actual_delta_C'] for c in group])
        started = datetime.now(timezone.utc).isoformat()
        fit = least_squares(residual, bound['initial'], bounds=(bound['lower'], bound['upper']),
                            max_nfev=200, ftol=1e-10, xtol=1e-10, gtol=1e-10)
        models[zone] = dict(parameters=[air, *fit.x.tolist()],
                            fit_case_ids=[c['case_id'] for c in group], validation_used=False,
                            actual_optimizer_fits=1, started_utc=started,
                            finished_utc=datetime.now(timezone.utc).isoformat(),
                            attempt=dict(initial=bound['initial'], success=bool(fit.success),
                                         message=fit.message, nfev=int(fit.nfev), cost=float(fit.cost),
                                         optimality=float(fit.optimality)))
    return models


def main():
    from tools.engineering_completion_v24.thermal_diagnostics import load_calibration, metric_rows, calibration_gate
    from tools.model_correctness_v23.thermal_data import sha256
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--destination', type=Path, required=True)
    parser.add_argument('--registration', type=Path, required=True)
    args = parser.parse_args()
    registration = json.loads(args.registration.read_text(encoding='utf-8'))
    config = json.loads(Path('configs/model_correctness_v23/temperature_parameters.json').read_text(encoding='utf-8'))
    if sha256(args.source/'PARAMETER_FREEZE.json') != config['parameter_freeze_sha256']:
        raise ValueError('Historical freeze identity changed')
    cases, frames, _ = load_calibration(args.source, config)
    if args.source.resolve() == args.destination.resolve() or (args.destination/'THREE_STATE_FIT.json').exists():
        raise ValueError('Historical source and completed attempt may not be overwritten')
    args.destination.mkdir(parents=True, exist_ok=True)
    models = bounded_fit(cases, registration)
    result = dict(models=models, actual_optimizer_fits=len(models),
                  registration_sha256=sha256(args.registration),
                  model_source_sha256=sha256(__file__), same_coefficients_across_days=True,
                  physical_capacity_statement='Air fixed to IDF air; furniture <= full original furniture; construction <= full original layers; no capacity duplicated between storage states',
                  frozen_utc=datetime.now(timezone.utc).isoformat())
    (args.destination/'THREE_STATE_FIT.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    rows, traces = metric_rows(cases, frames, {z: f['parameters'] for z,f in models.items()},
                               'M24_air_furniture_construction_countercheck', predictor=predict)
    pd = __import__('pandas')
    pd.DataFrame(rows).to_csv(args.destination/'THREE_STATE_CALIBRATION_METRICS.csv', index=False)
    result['calibration_gate'] = calibration_gate(rows)
    result['time_constants_h'] = {z: sorted((-1/np.linalg.eigvals(_kernel(tuple(f['parameters']), 1/6)[0])).tolist())
                                  for z,f in models.items()}
    result['status'] = ('Calibration gate met; preregister independent strong summer dates before running them'
                        if result['calibration_gate']['qualified_for_new_validation'] else
                        'Calibration failed; stop low-order expansion and new-date validation; exploratory reconstruction only')
    (args.destination/'THREE_STATE_RESULT.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    for cid, frame in traces.items():
        frame.to_csv(args.destination/(cid+'_M24_three_state_trace.csv'), index_label='timestamp')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
