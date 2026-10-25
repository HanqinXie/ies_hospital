"""V24 read-only thermal diagnosis and one bounded spatial-pooling countercheck.

No function in this module changes IES physics or takes true future surfaces as
prediction inputs. Old validation dates are development evidence, never a new
holdout. Native zone heating/cooling rates are per representative room.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from tools.model_correctness_v23.thermal_data import ZONES, sha256
from tools.model_correctness_v23.thermal_model import (
    _discretize, bounded_fit, paired_metrics, predict,
)

LIMITS = dict(event_rmse_C=1., recovery_first6h_rmse_C=.5,
              maximum_absolute_error_C=2., first_crossing_error_minutes=10.,
              recovery_time_error_minutes=20., complete_input_interval_minutes=10.)


def calibration_gate(rows):
    """Strong summer calibration is necessary; weak winter cannot qualify it."""
    summer = [r for r in rows if r.get('season') == 'summer']
    failures = []
    for row in summer:
        values = [row.get(field) for field in LIMITS]
        valid = all(v is not None and math.isfinite(float(v)) and
                    float(v) <= LIMITS[field]
                    for field, v in zip(LIMITS, values))
        if not valid or row.get('maximum_actual_delta_C', 0.) < 2.:
            failures.append(row['case_id'])
    return dict(qualified_for_new_validation=bool(summer) and not failures,
                strong_summer_rows=len(summer), failed_or_unassessed_cases=failures,
                winter_rows_separately_reported=sum(r.get('season') == 'winter' for r in rows),
                limits=LIMITS)


def heat_balance_residual(frame, *, multiplier):
    if not math.isfinite(float(multiplier)) or multiplier <= 0:
        raise ValueError('Positive physical zone multiplier required')
    return (frame.internal_convective_kW + frame.surface_convection_kW +
            frame.interzone_kW + frame.outdoor_transfer_kW +
            (frame.system_air_kW + frame.system_convective_kW) / multiplier -
            frame.air_storage_kW)


def fit_zone_models(cases, bounds):
    """Fixed zone coefficients, no date-dependent parameters or new states."""
    selected = [c for c in cases if c['split'] == 'calibration' and c['season'] == 'summer']
    if not selected:
        raise ValueError('Strong summer calibration is required')
    result = {}
    for zone in sorted({c['zone'] for c in selected}):
        group = [c for c in selected if c['zone'] == zone]
        result[zone] = bounded_fit(group, order=2, **bounds[zone])
    return result


def load_calibration(source, parameters):
    cases, frames, raw = [], {}, {}
    for season, month, day in [('summer', 7, 20), ('winter', 1, 22)]:
        for zone in ZONES:
            base = pd.read_csv(source / f'native10_cal_{season}_0h_{zone}.csv',
                               index_col=0, parse_dates=True)
            for hours in [2, 6]:
                actual = pd.read_csv(source / f'native10_cal_{season}_{hours}h_{zone}.csv',
                                     index_col=0, parse_dates=True)
                if not base.index.equals(actual.index):
                    raise ValueError('Paired native clocks differ')
                if not np.all(np.diff(base.index.asi8) == 600_000_000_000):
                    raise ValueError('Only complete actual native ten-minute inputs permitted')
                event = pd.Timestamp(2006, month, day, 12)
                delta = actual.select_dtypes('number') - base.select_dtypes('number')
                if delta[base.index <= event].abs().to_numpy().max() > 1e-10:
                    raise ValueError('Nonzero pre-event state or input difference')
                scale = parameters['zone_scales'][zone]
                forcing = (delta.heating_kW - delta.cooling_kW) / scale
                case = dict(case_id=f'cal_{season}_{hours}h_{zone}', split='calibration',
                            season=season, zone=zone, event_start=event.isoformat(),
                            outage_hours=hours, dt_h=1/6, scale=scale,
                            forcing_kW=forcing.to_numpy(), actual_delta_C=delta.temperature_C.to_numpy())
                cases.append(case)
                frames[case['case_id']] = pd.DataFrame(dict(
                    baseline_temperature_C=base.temperature_C, actual_temperature_C=actual.temperature_C,
                    setpoint_C=base.setpoint_C, forcing_kW=forcing))
                raw[case['case_id']] = (base, actual, delta)
    return cases, frames, raw


def metric_rows(cases, frames, model_parameters, model, predictor=predict):
    rows, traces = [], {}
    for case in cases:
        parameters = model_parameters[case['zone']] if isinstance(model_parameters, dict) else model_parameters
        prediction = predictor(case['forcing_kW'], parameters, dt_h=case['dt_h'])
        frame = frames[case['case_id']]
        recovery = pd.Timestamp(case['event_start']) + pd.Timedelta(hours=case['outage_hours'])
        after = frame.index > recovery
        tail = frame.index > recovery + pd.Timedelta(hours=6)
        error = prediction[:, 0] - case['actual_delta_C']
        row = dict(case_id=case['case_id'], season=case['season'], zone=case['zone'], model=model,
                   maximum_actual_delta_C=float(np.max(np.abs(case['actual_delta_C']))),
                   recovery_first_hour_bias_C=float(error[after & ~tail][:6].mean()),
                   recovery_after6h_bias_C=float(error[tail].mean()),
                   recovery_after6h_rmse_C=float(np.sqrt(np.mean(error[tail]**2))),
                   **paired_metrics(case, frame, prediction[:, 0]))
        row['passed'] = all(row[k] is not None and np.isfinite(row[k]) and row[k] <= limit
                            for k, limit in LIMITS.items())
        rows.append(row)
        traces[case['case_id']] = frame.assign(actual_delta_C=case['actual_delta_C'],
                                               predicted_delta_C=prediction[:, 0],
                                               latent_mass_delta_C=prediction[:, 1])
        if prediction.shape[1] == 3:
            traces[case['case_id']]['latent_construction_delta_C'] = prediction[:, 2]
    return rows, traces


def thermal_time_constants(parameters):
    matrix, _, _, _ = _discretize(*parameters, 1/6)
    return sorted((-1 / np.linalg.eigvals(matrix)).tolist())


def read_detailed_eso(path):
    """Read archived Each Call rates with ESO 0.01min timestamps, not SQL rounding."""
    header, data = path.read_text(encoding='utf-8').split('End of Data Dictionary', 1)
    identifiers = {}
    for line in header.splitlines():
        fields = line.split(',')
        if (len(fields) >= 4 and fields[2] == 'OR1_FLR_2' and '!Each Call' in fields[3]
                and 'Zone Air System Sensible ' in fields[3]):
            identifiers[fields[0]] = 'heat' if 'Heating' in fields[3] else 'cool'
    if set(identifiers.values()) != {'heat', 'cool'}:
        raise ValueError('Archived detailed heating and cooling channels required')
    samples, pending, clock, dt = [], {}, None, None
    def flush():
        if pending:
            if set(pending) != {'heat', 'cool'} or clock is None or dt <= 0:
                raise ValueError('Incomplete archived fractional HVAC interval')
            samples.append((clock, dt/60, (pending['heat']-pending['cool'])/1000))
            pending.clear()
    for line in data.splitlines():
        fields = [f.strip() for f in line.split(',')]
        if fields[0] == '2':
            flush()
            month, day, dst, hour = map(int, fields[2:6])
            start, end = map(float, fields[6:8])
            clock = pd.Timestamp(2006, month, day) + pd.Timedelta(hours=hour-1+dst, minutes=end)
            dt = end-start
        elif fields[0] in identifiers:
            pending[identifiers[fields[0]]] = float(fields[1])
    flush()
    result = pd.DataFrame(samples, columns=['timestamp', 'dt_h', 'net_sensible_kW']).set_index('timestamp')
    if result.empty or result.index.has_duplicates or not np.isfinite(result.to_numpy()).all():
        raise ValueError('Invalid archived detailed native rate coverage')
    return result


def detailed_prediction(source, season, hours, parameters):
    """Same frozen kernel, archived real HVAC rate shape and fractional ESO clock."""
    frame = read_detailed_eso(source/f'native10_cal_{season}_{hours}h/eplusout.eso')
    state, samples = np.zeros(2), []
    for clock, row in frame.iterrows():
        _, _, transition, response = _discretize(*parameters, row.dt_h)
        state = transition@state + response*row.net_sensible_kW
        samples.append((clock, state[0]))
    result = pd.Series(dict(samples))
    result.attrs['integrated_sensible_kWh'] = float((frame.dt_h*frame.net_sensible_kW).sum())
    return result


def diagnose(source, destination, parameters, frozen, cases, frames, raw):
    rows, traces = metric_rows(cases, frames, frozen, 'M23_frozen')
    pd.DataFrame(rows).to_csv(destination/'M23_CALIBRATION_REPLAY.csv', index=False)
    details = []
    for case in cases:
        base, actual, delta = raw[case['case_id']]
        event = pd.Timestamp(case['event_start'])
        recovery = event + pd.Timedelta(hours=case['outage_hours'])
        outage = (base.index > event) & (base.index <= recovery)
        late = base.index > recovery + pd.Timedelta(hours=6)
        q = delta.heating_kW-delta.cooling_kW
        surface_model = (traces[case['case_id']].latent_mass_delta_C -
                         traces[case['case_id']].predicted_delta_C)*frozen[2]*case['scale']
        empirical_hb = heat_balance_residual(delta, multiplier=parameters['zone_multipliers'][case['zone']])
        details.append(dict(case_id=case['case_id'], season=case['season'],
            pre_event_max_all_numeric_delta=float(delta.loc[:event].abs().to_numpy().max()),
            setpoint_max_abs_delta_C=float(delta.setpoint_C.abs().max()),
            coil_event_max_kW=float(actual.shared_coil_total_kW[outage].max()),
            event_net_sensible_delta_kWh=float(q[outage].sum()/6),
            normal_event_cooling_kWh=float(base.cooling_kW[outage].sum()/6),
            actual_event_cooling_kWh=float(actual.cooling_kW[outage].sum()/6),
            actual_event_heating_kWh=float(actual.heating_kW[outage].sum()/6),
            post_event_extra_zone_cooling_kWh=float(np.maximum(delta.cooling_kW, 0.)[base.index > recovery].sum()/6),
            late_mean_actual_surface_delta_kW=float(delta.surface_convection_kW[late].mean()),
            late_mean_actual_delivered_delta_kW=float(q[late].mean()),
            late_mean_model_surface_error_kW=float((surface_model-delta.surface_convection_kW)[late].mean()),
            late_surface_rmse_kW=float(np.sqrt(np.mean((surface_model-delta.surface_convection_kW)[late]**2))),
            late_mean_outdoor_transfer_delta_kW=float(delta.outdoor_transfer_kW[late].mean()),
            delta_heat_balance_residual_rms_kW=float(np.sqrt(np.mean(empirical_hb**2))),
            delta_heat_balance_residual_max_kW=float(empirical_hb.abs().max())))
        traces[case['case_id']].assign(actual_surface_convection_delta_kW=delta.surface_convection_kW,
                                      model_surface_convection_delta_kW=surface_model,
                                      actual_radiant_delta_C=delta.radiant_C).to_csv(
            destination/(case['case_id']+'_M23_diagnostic_trace.csv'), index_label='timestamp')
    pd.DataFrame(details).to_csv(destination/'CALIBRATION_INPUT_AND_SURFACE_DIAGNOSTICS.csv', index=False)
    detailed = []
    for season in ['summer', 'winter']:
        normal = detailed_prediction(source, season, 0, frozen)
        for hours in [2, 6]:
            cid = f'cal_{season}_{hours}h_OR1_FLR_2'
            trace = traces[cid]
            actual_detailed = detailed_prediction(source, season, hours, frozen)
            value = actual_detailed.reindex(trace.index) - normal.reindex(trace.index)
            if value.isna().any():
                raise ValueError('Archived detailed-rate coverage is incomplete')
            change = value - trace.predicted_delta_C
            recovery = pd.Timestamp(next(c['event_start'] for c in cases if c['case_id']==cid)) + pd.Timedelta(hours=hours)
            tail = trace.index > recovery + pd.Timedelta(hours=6)
            native_energy = float(trace.forcing_kW.sum()/6)
            eso_energy = actual_detailed.attrs['integrated_sensible_kWh']-normal.attrs['integrated_sensible_kWh']
            detailed.append(dict(case_id=cid, clock='Original ESO fractional minutes, 0.01min output precision',
                                 maximum_detailed_minus_native_prediction_C=float(abs(change).max()),
                                 tail_mean_detailed_minus_native_prediction_C=float(change[tail].mean()),
                                 native_paired_energy_kWh=native_energy, eso_paired_energy_kWh=eso_energy,
                                 paired_energy_difference_kWh=eso_energy-native_energy))
    pd.DataFrame(detailed).to_csv(destination/'ARCHIVED_HVAC_SUBSTEP_COUNTERCHECK.csv', index=False)
    result = dict(model='M23_frozen', time_constants_h=thermal_time_constants(frozen),
                  calibration_gate=calibration_gate(rows), source_freeze_sha256=sha256(source/'PARAMETER_FREEZE.json'),
                  intervention='Shared VAV_OR mechanical cooling coil unavailable; air handler, ventilation and reheat remain available',
                  quantitative_claim='Conditional paired-temperature reconstruction from realized net zone sensible input; not autonomous fault-temperature prediction',
                  no_new_EnergyPlus_runs=True)
    (destination/'M23_DIAGNOSTIC_RESULT.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=['diagnose', 'fit-zone'])
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--destination', type=Path, required=True)
    parser.add_argument('--registration', type=Path)
    args = parser.parse_args()
    args.destination.mkdir(parents=True, exist_ok=True)
    if args.source.resolve() == args.destination.resolve():
        raise ValueError('Historical source must remain read-only')
    config = json.loads(Path('configs/model_correctness_v23/temperature_parameters.json').read_text(encoding='utf-8'))
    frozen = json.loads((args.source/'PARAMETER_FREEZE.json').read_text(encoding='utf-8'))
    if sha256(args.source/'PARAMETER_FREEZE.json') != config['parameter_freeze_sha256']:
        raise ValueError('Historical parameter freeze identity changed')
    selected = [*args.source.glob('native10_cal_*_OR*.csv'), args.source/'PARAMETER_FREEZE.json']
    hashes_before = {str(p): sha256(p) for p in selected}
    cases, frames, raw = load_calibration(args.source, config)
    if args.phase == 'diagnose':
        diagnose(args.source, args.destination, config, frozen['models']['two_state']['parameters'], cases, frames, raw)
    else:
        if args.registration is None:
            raise ValueError('Prior fixed-zone countercheck registration is required')
        registration = json.loads(args.registration.read_text(encoding='utf-8'))
        if (args.destination/'FIXED_ZONE_FIT.json').exists():
            raise ValueError('Do not overwrite a fit attempt; preserve failed and completed evidence')
        fits = fit_zone_models(cases, registration['bounds'])
        result = dict(completed_utc=datetime.now(timezone.utc).isoformat(),
                      registration_sha256=sha256(args.registration), models=fits,
                      actual_optimizer_fits=sum(len(v['attempts']) for v in fits.values()),
                      runtime_input_fields=['current relative air and mass state', 'actual realized net zone sensible difference', 'elapsed interval'],
                      forbidden_input_fields=['future true surface or radiant temperature', 'future repair time', 'true future room temperature'])
        (args.destination/'FIXED_ZONE_FIT.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
        rows, traces = metric_rows(cases, frames, {z: f['parameters'] for z,f in fits.items()}, 'M24_fixed_zone_countercheck')
        pd.DataFrame(rows).to_csv(args.destination/'FIXED_ZONE_CALIBRATION_METRICS.csv', index=False)
        result['calibration_gate'] = calibration_gate(rows)
        result['time_constants_h'] = {z: thermal_time_constants(f['parameters']) for z,f in fits.items()}
        result['status'] = ('Calibration gate met; independent preregistered strong summer validation still required'
                            if result['calibration_gate']['qualified_for_new_validation'] else
                            'Calibration gate not met; no new validation dates or quantitative upgrade authorized by this result')
        (args.destination/'FIXED_ZONE_RESULT.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
        for cid, trace in traces.items():
            trace.to_csv(args.destination/(cid+'_M24_fixed_zone_trace.csv'), index_label='timestamp')
        print(json.dumps(result, indent=2))
    hashes_after = {str(p): sha256(p) for p in selected}
    if hashes_after != hashes_before:
        raise RuntimeError('Historical inputs changed during diagnostics')
    (args.destination/(args.phase.upper().replace('-', '_')+'_SOURCE_INTEGRITY.json')).write_text(
        json.dumps(dict(hashes=hashes_after, unchanged=True), indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
