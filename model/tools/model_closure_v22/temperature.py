"""Bounded net-sensible 1R1C research model and read-only paired-data replay.

The module has no device, command, scenario-event, or clinical-service access.
Recovery requests are pure current-state calculations; only ``commit`` with
actually delivered cooling changes the thermal state. Positive sensible input
adds heat. Whole-air-system net sensible input already includes ventilation.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import math


def _finite(name, value, *, positive=False, nonnegative=False):
    if isinstance(value, bool):
        raise ValueError(f'{name} must be a finite scalar')
    try:
        value = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f'{name} must be a finite scalar') from exc
    if not math.isfinite(value) or (positive and value <= 0.) or (nonnegative and value < 0.):
        raise ValueError(f'Invalid {name}')
    return value


@dataclass(frozen=True)
class ThermalState:
    delta_C: float
    capacity_kWh_K: float
    conductance_kW_K: float

    def __post_init__(self):
        for name, positive, nonnegative in (
                ('delta_C', False, False), ('capacity_kWh_K', True, False),
                ('conductance_kW_K', False, True)):
            object.__setattr__(self, name, _finite(name, getattr(self, name),
                                                 positive=positive, nonnegative=nonnegative))


@dataclass(frozen=True)
class ThermalCommit:
    state: ThermalState
    sensible_input_kWh: float
    actual_extra_cooling_kWh: float
    passive_heat_rejection_kWh: float
    thermal_energy_change_kWh: float
    balance_residual_kWh: float
    input_boundary: str


def _response(state, dt_h):
    dt = _finite('dt_h', dt_h, positive=True)
    if state.conductance_kW_K == 0.:
        return 1., dt / state.capacity_kWh_K
    exponent = -state.conductance_kW_K * dt / state.capacity_kWh_K
    return math.exp(exponent), -math.expm1(exponent) / state.conductance_kW_K


def recovery_request_kW(state, target_delta_C=0., known_horizon_h=1., max_power_kW=0.):
    """Extra current-state cooling request, bounded by declared available power.

    ``known_horizon_h`` is a configured recovery interval, never the unknown
    remaining outage duration. No future load/repair/event data are accepted.
    The request accounts for passive relaxation with zero *additional* forcing;
    it provides no claim about future service sufficiency or actual delivery.
    """
    target = _finite('target_delta_C', target_delta_C)
    maximum = _finite('max_power_kW', max_power_kW, nonnegative=True)
    decay, response = _response(state, known_horizon_h)
    return min(maximum, max(0., (decay * state.delta_C - target) / response))


def commit(state, actual_extra_cooling_kW, dt_h, *, sensible_delta_kW=0.,
           ventilation_delta_kW=0., input_boundary='net_air_system_sensible'):
    """Advance one real interval, retaining a signed, closed thermal ledger.

    ``sensible_delta_kW`` is actual minus baseline net heat, positive into the
    zone, and MUST exclude the separately declared extra recovery cooling.
    For paired EnergyPlus data it is ``(H_actual-H_base)-(C_actual-C_base)``;
    recovery already contained in that observed difference must not be passed
    again as ``actual_extra_cooling_kW``. The alternative deficit boundary may
    add independently known signed ventilation; a complete net-air-system
    boundary rejects that second term to prevent double accounting.

    Actual overdelivery can yield a negative delta. The state is never clipped
    or reset on repair, since either would silently remove thermal energy.
    """
    extra = _finite('actual_extra_cooling_kW', actual_extra_cooling_kW, nonnegative=True)
    sensible = _finite('sensible_delta_kW', sensible_delta_kW)
    ventilation = _finite('ventilation_delta_kW', ventilation_delta_kW)
    if input_boundary == 'net_air_system_sensible':
        if ventilation != 0.:
            raise ValueError('Net air-system sensible input already includes ventilation')
    elif input_boundary != 'cooling_deficit_plus_explicit_ventilation':
        raise ValueError('Unknown sensible input boundary')
    dt_h = _finite('dt_h', dt_h, positive=True)
    decay, response = _response(state, dt_h)
    net_power = sensible + ventilation - extra
    next_delta = decay * state.delta_C + response * net_power
    next_state = replace(state, delta_C=next_delta)
    input_energy = (sensible + ventilation) * dt_h
    extra_energy = extra * dt_h
    change = state.capacity_kWh_K * (next_delta - state.delta_C)
    # Analytic integral of U*delta(t), independently evaluated from the stored
    # energy change. It is signed: below-baseline zones receive passive heat.
    passive = (state.capacity_kWh_K * state.delta_C * (1.-decay) +
               net_power * (dt_h-state.capacity_kWh_K*response))
    residual = input_energy - extra_energy - passive - change
    return ThermalCommit(next_state, input_energy, extra_energy, passive, change,
                         residual, input_boundary)


def evaluate_acceptance(metrics, acceptance):
    """Missing/censored precision metrics remain unassessed, never a pass."""
    limits = acceptance['limits']
    failed = []
    names = ('event_rmse_C', 'recovery_first6h_rmse_C', 'maximum_absolute_error_C',
             'first_crossing_error_minutes', 'recovery_time_error_minutes')
    for name in names:
        value = metrics.get(name)
        if (value is None or not math.isfinite(float(value)) or float(value) < 0. or
                float(value) > float(limits[name])):
            failed.append(name)
    interval = metrics.get('complete_input_interval_minutes')
    if (interval is None or not math.isfinite(float(interval)) or interval <= 0. or
            interval > limits['maximum_complete_input_interval_minutes']):
        failed.append('complete_input_interval_minutes')
    if not metrics.get('crossing_status_matches', False):
        failed.append('crossing_status_matches')
    if not metrics.get('inputs_complete', False):
        failed.append('inputs_complete')
    return dict(passed=not failed, failed_or_unassessed=failed)


def validate_measurement_contract(acceptance):
    """Reject config drift from the deliberately fixed native research metrics."""
    supported = dict(research_threshold_above_setpoint_C=2., recovery_band_C=.2,
                     recovery_consecutive_native_samples=3, recovery_native_sample_minutes=10.)
    if any(acceptance.get(key) != value for key, value in supported.items()):
        raise ValueError('Unsupported research measurement contract; change code and regression together')


def _predict(forcing, dt_h, capacity, conductance):
    state = ThermalState(0., capacity, conductance)
    result = []
    for power in forcing:
        state = commit(state, 0., dt_h, sensible_delta_kW=power).state
        result.append(state.delta_C)
    return result


def fit_one_state(cases, *, capacity_bounds, conductance_bounds, initial):
    """One two-parameter bounded least-squares fit, calibration rows only.

    Bounds come from the recorded air/furniture capacity and the pre-registered
    limited effective-U range. There are no restarts or model-order searches.
    A fit is a diagnostic result, not a deployment or precision acceptance.
    """
    import numpy as np
    from scipy.optimize import least_squares

    selected = [case for case in cases if case['split'] == 'calibration']
    if not selected:
        raise ValueError('Calibration cases are required')

    def residual(parameters):
        values = []
        for case in selected:
            forcing = np.asarray(case['forcing_kW'], dtype=float)
            actual = np.asarray(case['actual_delta_C'], dtype=float)
            if forcing.shape != actual.shape or forcing.ndim != 1 or not len(actual):
                raise ValueError('Invalid paired thermal arrays')
            weights = np.asarray(case.get('fit_weights', np.ones(len(actual))), dtype=float)
            if weights.shape != actual.shape or not np.all(np.isfinite(actual)) or not np.all(np.isfinite(weights)):
                raise ValueError('Invalid paired thermal fit values')
            predicted = np.asarray(_predict(forcing, case['dt_h'], *parameters))
            values.extend((predicted - actual) * weights)
        return np.asarray(values)

    result = least_squares(residual, initial,
                           bounds=([capacity_bounds[0], conductance_bounds[0]],
                                   [capacity_bounds[1], conductance_bounds[1]]),
                           max_nfev=80)
    return dict(capacity_kWh_K=float(result.x[0]), conductance_kW_K=float(result.x[1]),
                fit_case_ids=[case['case_id'] for case in selected], fit_attempts=1,
                optimizer_success=bool(result.success), optimizer_message=str(result.message),
                function_evaluations=int(result.nfev), cost=float(result.cost),
                capacity_bounds=list(capacity_bounds), conductance_bounds=list(conductance_bounds),
                no_independent_validation=True, additional_thermal_states=0)


def aggregate_net_sensible_hourly(raw, baseline_heating_hourly, actual_heating_hourly):
    """Retain native hourly heating; integrate 10min cooling to that boundary.

    Temperatures use the hour-end state, never the average of six states. No
    hourly heat value is expanded into a fabricated 10-minute input series.
    """
    import numpy as np

    if raw.index.has_duplicates or not np.all(np.diff(raw.index.asi8) == 600_000_000_000):
        raise ValueError('Native paired clock must be continuous at ten minutes')
    bins = raw.resample('1h', closed='right', label='right')
    counts = bins.size()
    if not (counts == 6).all():
        raise ValueError('Incomplete native hour in paired thermal data')
    hourly = bins.last()
    for field in ('baseline_sensible_cooling_kW', 'independent_sensible_cooling_kW'):
        hourly[field] = bins[field].mean()
    for values in (baseline_heating_hourly, actual_heating_hourly):
        if not hourly.index.equals(values.index):
            raise ValueError('Hourly heating clock does not match paired cooling')
    hourly['baseline_sensible_heating_kW'] = baseline_heating_hourly
    hourly['independent_sensible_heating_kW'] = actual_heating_hourly
    hourly['forcing_kW'] = (actual_heating_hourly - baseline_heating_hourly +
                            hourly.baseline_sensible_cooling_kW - hourly.independent_sensible_cooling_kW)
    return hourly


def _first_crossing(temperature, limit):
    crossed = temperature[temperature > limit + 1e-7]
    return crossed.index[0] if len(crossed) else None


def _native_recovery(delta, recovery):
    """Require three real 10min states; a coarse state cannot prove this."""
    import numpy as np

    if len(delta) < 3 or not np.all(np.diff(delta.index.asi8) == 600_000_000_000):
        return None
    for i in range(len(delta) - 2):
        if delta.index[i] > recovery and np.all(np.abs(delta.iloc[i:i+3]) <= .2):
            return float((delta.index[i] - recovery).total_seconds() / 60.)
    return None


def paired_metrics(case, actual, predicted_delta, *, complete_input_interval_minutes, inputs_complete):
    """Evaluate at real model points and disclose coarser input resolution."""
    from datetime import datetime, timedelta
    import numpy as np

    event = datetime(2006, case['month'], case['day'], 12)
    recovery = event + timedelta(hours=case['outage_hours'])
    if not predicted_delta.index.isin(actual.index).all():
        raise ValueError('Prediction timestamps must be real paired source points')
    comparison = actual.loc[predicted_delta.index]
    modeled = comparison.baseline_temperature_C + predicted_delta
    error = modeled - comparison.independent_temperature_C
    event_mask = (error.index > event) & (error.index <= recovery)
    recovery_mask = (error.index > recovery) & (error.index <= recovery + timedelta(hours=6))
    evaluate = error.index > event
    def rmse(mask):
        return float(np.sqrt(np.mean(np.asarray(error[mask])**2))) if np.any(mask) else None
    observed_delta = actual.independent_temperature_C - actual.baseline_temperature_C
    actual_after = actual.index > event
    predicted_after = modeled.index > event
    observed_cross = _first_crossing(actual.independent_temperature_C[actual_after],
                                     actual.cooling_setpoint_C[actual_after] + 2.)
    modeled_cross = _first_crossing(modeled[predicted_after],
                                    comparison.cooling_setpoint_C[predicted_after] + 2.)
    same = (observed_cross is None) == (modeled_cross is None)
    crossing_error = (abs((modeled_cross-observed_cross).total_seconds())/60.
                      if same and observed_cross is not None else (0. if same else None))
    actual_recovery = _native_recovery(observed_delta, recovery)
    modeled_recovery = _native_recovery(predicted_delta, recovery)
    return dict(event_rmse_C=rmse(event_mask), recovery_first6h_rmse_C=rmse(recovery_mask),
                full_tail_rmse_C=rmse(evaluate),
                maximum_absolute_error_C=float(np.max(np.abs(error[evaluate]))) if np.any(evaluate) else None,
                first_crossing_error_minutes=crossing_error,
                recovery_time_error_minutes=(abs(modeled_recovery-actual_recovery)
                    if modeled_recovery is not None and actual_recovery is not None else None),
                independent_first_crossing=observed_cross.isoformat() if observed_cross is not None else None,
                modeled_first_crossing=modeled_cross.isoformat() if modeled_cross is not None else None,
                independent_recovery_minutes=actual_recovery, modeled_recovery_minutes=modeled_recovery,
                crossing_status_matches=same, complete_input_interval_minutes=complete_input_interval_minutes,
                metric_interval_minutes=float((predicted_delta.index[1]-predicted_delta.index[0]).total_seconds()/60.),
                inputs_complete=inputs_complete)


def _sql_series(database, variable, key, frequency):
    from datetime import datetime, timedelta
    from pathlib import Path
    import sqlite3
    import pandas as pd

    with sqlite3.connect(Path(database).resolve().as_uri() + '?mode=ro', uri=True) as connection:
        rows = connection.execute(
            'SELECT t.Month,t.Day,t.Hour,t.Minute,t.Dst,x.VariableValue,d.VariableUnits '
            'FROM ReportVariableData x JOIN ReportVariableDataDictionary d USING(ReportVariableDataDictionaryIndex) '
            'JOIN Time t USING(TimeIndex) JOIN EnvironmentPeriods e USING(EnvironmentPeriodIndex) '
            'WHERE e.EnvironmentType=3 AND d.VariableName=? AND d.KeyValue=? '
            'AND d.ReportingFrequency=? ORDER BY t.TimeIndex', (variable, key, frequency)).fetchall()
    if not rows:
        raise ValueError(f'Missing native {frequency} output: {variable}/{key}')
    stamps = [datetime(2006, m, d) + timedelta(hours=h+dst, minutes=minute)
              for m, d, h, minute, dst, _, _ in rows]
    values = []
    for *_, value, unit in rows:
        if unit not in ('W', 'C'):
            raise ValueError(f'Unexpected SQL thermal unit: {unit}')
        values.append(float(value) / (1000. if unit == 'W' else 1.))
    result = pd.Series(values, index=pd.DatetimeIndex(stamps))
    if result.index.has_duplicates:
        raise ValueError('Duplicate native SQL clock')
    return result


def read_existing_pair(pair_root, case):
    """Read the eight archived SQLs in read-only mode; never launch EnergyPlus."""
    from datetime import datetime, timedelta
    from pathlib import Path
    import pandas as pd

    pair_root = Path(pair_root)
    outputs = {}
    heating = {}
    for mode in ('baseline', 'interruption'):
        database = pair_root / (case['case_id'] + '_' + mode) / 'eplusout.sql'
        outputs[mode] = pd.DataFrame({
            'temperature_C': _sql_series(database, 'Zone Mean Air Temperature', 'OR1_FLR_2', 'Zone Timestep'),
            'cooling_kW': _sql_series(database, 'Zone Air System Sensible Cooling Rate', 'OR1_FLR_2', 'Zone Timestep'),
            'setpoint_C': _sql_series(database, 'Zone Thermostat Cooling Setpoint Temperature', 'OR1_FLR_2', 'Zone Timestep'),
            'coil_kW': _sql_series(database, 'Cooling Coil Total Cooling Rate', 'VAV_OR COOLING COIL', 'Zone Timestep')})
        heating[mode] = _sql_series(database, 'Zone Air System Sensible Heating Rate', 'OR1_FLR_2', 'Hourly')
    base, interrupted = outputs['baseline'], outputs['interruption']
    if not base.index.equals(interrupted.index) or base.isna().any().any() or interrupted.isna().any().any():
        raise ValueError('Paired native clocks/variables do not align')
    raw = pd.DataFrame(dict(baseline_temperature_C=base.temperature_C,
                            independent_temperature_C=interrupted.temperature_C,
                            baseline_sensible_cooling_kW=base.cooling_kW,
                            independent_sensible_cooling_kW=interrupted.cooling_kW,
                            cooling_setpoint_C=base.setpoint_C,
                            baseline_airloop_coil_cooling_kW=base.coil_kW,
                            independent_airloop_coil_cooling_kW=interrupted.coil_kW))
    event = datetime(2006, case['month'], case['day'], 12)
    recovery = event + timedelta(hours=case['outage_hours'])
    off = (raw.index > event) & (raw.index <= recovery)
    if raw.independent_airloop_coil_cooling_kW[off].max() > 1e-6:
        raise ValueError('Archived target coil was not off in the registered event')
    if ((raw.independent_temperature_C - raw.baseline_temperature_C)[raw.index <= event].abs().max() > 1e-7):
        raise ValueError('Archived pair does not share its pre-event temperature state')
    return raw, aggregate_net_sensible_hourly(raw, heating['baseline'], heating['interruption'])


def _sha256(path):
    import hashlib
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            digest.update(block)
    return digest.hexdigest()


def run_replay(pair_root, destination, acceptance_path, idf_path):
    """One bounded fit and compact numeric replay; all four dates are seen."""
    from datetime import datetime, timedelta
    from pathlib import Path
    import json
    import numpy as np
    import pandas as pd
    from hospital_ies.idf_parameters import zone_parameter_table

    pair_root, destination, acceptance_path, idf_path = map(Path,
        (pair_root, destination, acceptance_path, idf_path))
    destination.mkdir(parents=True, exist_ok=False)
    acceptance = json.loads(acceptance_path.read_text(encoding='utf-8-sig'))
    validate_measurement_contract(acceptance)
    cases = [dict(case_id='cal_summer_2h', split='calibration', month=6, day=15, outage_hours=2.),
             dict(case_id='cal_transition_4h', split='calibration', month=9, day=15, outage_hours=4.),
             dict(case_id='val_winter_2h', split='seen_regression', month=1, day=15, outage_hours=2.),
             dict(case_id='val_summer_4h', split='seen_regression', month=8, day=15, outage_hours=4.)]
    parameters = next(row for row in zone_parameter_table(idf_path, .5, 1.5) if row['zone_name'] == 'OR1_FLR_2')
    source_paths = [idf_path, acceptance_path, Path(__file__)]
    source_paths.extend(pair_root / (case['case_id'] + '_' + mode) / 'eplusout.sql'
                        for case in cases for mode in ('baseline', 'interruption'))
    sources = {str(path.resolve()): _sha256(path) for path in source_paths}
    plan = dict(status='started', sources_sha256=sources, cases=cases,
                new_energyplus_runs=0, new_ies_trajectories=0, fit_attempts_planned=1,
                additional_thermal_states=0, all_dates_seen=True,
                input_resolution='Native hourly heating; ten-minute cooling integrated to real hour ends',
                no_hourly_heating_upsampling=True, no_production_parameters_changed=True)
    (destination/'RUN_RECEIPT.json').write_text(json.dumps(plan, indent=2), encoding='utf-8')
    pairs = []
    fit_cases = []
    for case in cases:
        raw, hourly = read_existing_pair(pair_root, case)
        pairs.append((case, raw, hourly))
        event = datetime(2006, case['month'], case['day'], 12)
        recovery = event + timedelta(hours=case['outage_hours'])
        outage = (hourly.index > event) & (hourly.index <= recovery)
        tail = (hourly.index > recovery) & (hourly.index <= recovery+timedelta(hours=6))
        weights = np.zeros(len(hourly))
        weights[outage] = 1/math.sqrt(np.count_nonzero(outage))
        weights[tail] = 1/math.sqrt(np.count_nonzero(tail))
        fit_cases.append(dict(case_id=case['case_id'], split=case['split'], dt_h=1.,
                              forcing_kW=hourly.forcing_kW.to_numpy(),
                              actual_delta_C=(hourly.independent_temperature_C-hourly.baseline_temperature_C).to_numpy(),
                              fit_weights=weights))
    floor = parameters['floor_area_from_volume_m2']
    u_bounds = acceptance['physical_fit_bounds']['effective_U_W_m2K']
    fitted = fit_one_state(fit_cases,
        capacity_bounds=(parameters['air_capacity_kWh_K'],
                         parameters['air_capacity_kWh_K']+parameters['IDF_furniture_capacity_kWh_K']),
        conductance_bounds=(floor*u_bounds[0]/1000., floor*u_bounds[1]/1000.),
        initial=(parameters['effective_C_kWh_K'], parameters['assumed_UA_kW_K']))
    (destination/'FIT.json').write_text(json.dumps(fitted, indent=2), encoding='utf-8')
    rows, curves, energies = [], [], []
    for case, raw, hourly in pairs:
        event = datetime(2006, case['month'], case['day'], 12)
        recovery = event+timedelta(hours=case['outage_hours'])
        off = (raw.index > event) & (raw.index <= recovery)
        original = pd.Series(_predict(raw.baseline_sensible_cooling_kW.to_numpy()*off,
            1/6., parameters['effective_C_kWh_K'], parameters['assumed_UA_kW_K']), index=raw.index)
        signed_cooling = pd.Series(_predict(
            (raw.baseline_sensible_cooling_kW-raw.independent_sensible_cooling_kW).to_numpy(),
            1/6., parameters['effective_C_kWh_K'], parameters['assumed_UA_kW_K']), index=raw.index)
        net_original = pd.Series(_predict(hourly.forcing_kW.to_numpy(), 1.,
            parameters['effective_C_kWh_K'], parameters['assumed_UA_kW_K']), index=hourly.index)
        net_fitted = pd.Series(_predict(hourly.forcing_kW.to_numpy(), 1.,
            fitted['capacity_kWh_K'], fitted['conductance_kW_K']), index=hourly.index)
        for label, predicted, interval, complete in (
                ('original_proxy_10min', original, 10., False),
                ('signed_cooling_only_10min', signed_cooling, 10., False),
                ('actual_net_sensible_hourly_original_C_U', net_original, 60., True),
                ('actual_net_sensible_hourly_bounded_fit', net_fitted, 60., True)):
            metrics = paired_metrics(case, raw, predicted,
                complete_input_interval_minutes=interval, inputs_complete=complete)
            verdict = evaluate_acceptance(metrics, acceptance)
            rows.append(dict(case_id=case['case_id'], split=case['split'], method=label,
                             **metrics, accepted=verdict['passed'],
                             failed_or_unassessed=';'.join(verdict['failed_or_unassessed'])))
            observed = raw.loc[predicted.index]
            curves.extend(dict(case_id=case['case_id'], method=label, timestamp=stamp.isoformat(),
                model_interval_minutes=interval, baseline_temperature_C=float(observed.loc[stamp].baseline_temperature_C),
                independent_temperature_C=float(observed.loc[stamp].independent_temperature_C),
                modeled_delta_C=float(value)) for stamp, value in predicted.items())
        event_h = (hourly.index > event) & (hourly.index <= recovery)
        after_h = hourly.index > recovery
        energies.append(dict(case_id=case['case_id'],
            event_baseline_sensible_cooling_kWh=float(raw.baseline_sensible_cooling_kW[off].sum()/6.),
            event_actual_sensible_cooling_kWh=float(raw.independent_sensible_cooling_kW[off].sum()/6.),
            event_actual_sensible_heating_kWh=float(hourly.independent_sensible_heating_kW[event_h].sum()),
            event_net_sensible_deficit_kWh=float(hourly.forcing_kW[event_h].sum()),
            recovery_additional_sensible_cooling_kWh=float(np.maximum(0.,
                raw.independent_sensible_cooling_kW[raw.index>recovery]-raw.baseline_sensible_cooling_kW[raw.index>recovery]).sum()/6.),
            recovery_net_sensible_input_kWh=float(hourly.forcing_kW[after_h].sum()),
            source='paired EnergyPlus regional output; not automatically an IES plant/electricity ledger'))
    pd.DataFrame(rows).to_csv(destination/'TEMPERATURE_BEFORE_AFTER.csv', index=False)
    pd.DataFrame(curves).to_csv(destination/'TEMPERATURE_SAMPLES.csv', index=False)
    pd.DataFrame(energies).to_csv(destination/'SENSIBLE_ENERGY.csv', index=False)
    plan.update(status='completed_exploratory_precision_not_accepted', fit_attempts_actual=1,
                result_rows=len(rows), accepted_rows=sum(row['accepted'] for row in rows),
                sources_unchanged=all(_sha256(Path(path))==sha for path,sha in sources.items()),
                key_limit='Hourly net sensible input cannot assess ten-minute crossing/recovery precision; no new independent validation',
                fitted_parameters=fitted)
    (destination/'RUN_RECEIPT.json').write_text(json.dumps(plan, indent=2), encoding='utf-8')
    return plan


def main():
    import argparse
    import json
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pair-root', type=Path, required=True)
    parser.add_argument('--destination', type=Path, required=True)
    parser.add_argument('--acceptance', type=Path, default=root/'configs/model_closure_v22/temperature_acceptance.json')
    parser.add_argument('--idf', type=Path, default=root/'inputs/energyplus/Hospital_SanDiego.idf')
    args = parser.parse_args()
    print(json.dumps(run_replay(args.pair_root, args.destination, args.acceptance, args.idf), indent=2))


if __name__ == '__main__':
    main()
