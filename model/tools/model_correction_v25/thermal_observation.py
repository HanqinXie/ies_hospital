"""V25 thermal observation contract; legacy endpoint models are unchanged.

Historical EnergyPlus substeps are offline reference only.  API durations are
authoritative; ESO fractional clocks are preserved as printed, never expanded
from the rounded SQL time table.
"""
from __future__ import annotations

from pathlib import Path
import numpy as np
import pandas as pd

from tools.model_correctness_v23.thermal_data import ZONES

DETAILED = {'temperature_C': 'Zone Air Temperature',
            'heating_kW': 'Zone Air System Sensible Heating Rate',
            'cooling_kW': 'Zone Air System Sensible Cooling Rate'}


def observation_idf(original):
    """Append reporting requests; preserve every original character/object."""
    extra = []
    for zone in ZONES:
        for variable in DETAILED.values():
            extra.append(f'Output:Variable,{zone},{variable},Detailed;')
        extra.append(f'Output:Variable,{zone},Zone Mean Air Temperature,Timestep;')
    return original + '\n' + '\n'.join(extra) + '\n'


def aggregate_completed(frame, *, interval_minutes=10.):
    """Duration weighted actual system endpoints for complete zone intervals.

    A row belongs to its explicit completed bucket, not a rounded inferred
    internal timestamp.  No interpolation or continuous mean is substituted.
    """
    required = ['bucket_end', 'dt_h', 'temperature_C']
    if frame.empty or any(field not in frame for field in required):
        raise ValueError('Complete endpoint records and bucket assignment required')
    data = frame.copy()
    data['bucket_end'] = pd.to_datetime(data.bucket_end)
    if data.bucket_end.isna().any() or not np.isfinite(data[['dt_h','temperature_C']]).all().all() or (data.dt_h <= 0).any():
        raise ValueError('Finite endpoints, timestamps and positive actual durations required')
    rows = []
    for bucket, group in data.groupby('bucket_end', sort=True):
        duration = float(group.dt_h.sum())
        if abs(duration-interval_minutes/60) > 1e-10:
            raise ValueError(f'Incomplete or overfull actual interval: {bucket}/{duration}')
        row = dict(bucket_end=bucket, temperature_C=float(np.dot(group.temperature_C,group.dt_h)/duration),
                   endpoint_C=float(group.temperature_C.iloc[-1]),dt_h=duration,substeps=len(group))
        if 'net_sensible_kW' in group:
            if not np.isfinite(group.net_sensible_kW).all():
                raise ValueError('Nonfinite actual sensible input')
            row['net_sensible_kW'] = float(np.dot(group.net_sensible_kW,group.dt_h)/duration)
        rows.append(row)
    return pd.DataFrame(rows).set_index('bucket_end')


def paired_report(normal, actual, *, interval_minutes=10.):
    """Aggregate each real trajectory on its own substeps, then subtract."""
    base = aggregate_completed(normal,interval_minutes=interval_minutes)
    fault = aggregate_completed(actual,interval_minutes=interval_minutes)
    if not base.index.equals(fault.index):
        raise ValueError('Paired completed zone intervals differ')
    return fault[['temperature_C','endpoint_C']]-base[['temperature_C','endpoint_C']]


def align_completed_api(frame, completed_zone_clock, *, interval_minutes=10.):
    """Attach captured system durations to actual *complete* zone records.

    API current_time at the end-zone callback may retain the last shortened
    HVAC-step offset.  Ordinal complete-zone reports provide the authoritative
    ten-minute boundary. They never determine or round a system duration.
    """
    clock = pd.DatetimeIndex(completed_zone_clock)
    groups = list(frame.groupby('bucket_end',sort=False))
    if len(groups) != len(clock) or clock.has_duplicates or not clock.is_monotonic_increasing:
        raise ValueError('API/zone complete-interval counts or clocks differ')
    rows = []
    for (old_bucket,group),bucket in zip(groups,clock):
        if 'substep_index' in group and group.substep_index.tolist() != list(range(len(group))):
            raise ValueError('API substep order is incomplete')
        if abs(group.dt_h.sum()-interval_minutes/60) > 1e-10 or (group.dt_h <= 0).any():
            raise ValueError('Incomplete actual API zone interval')
        result = group.copy()
        result['source_api_bucket_end'] = pd.Timestamp(old_bucket)
        result['bucket_end'] = bucket
        elapsed = np.cumsum(result.dt_h.to_numpy())
        result['timestamp'] = [bucket-pd.Timedelta(int(round((interval_minutes/60-t)*3_600_000_000_000)),unit='ns') for t in elapsed]
        rows.append(result)
    return pd.concat(rows,ignore_index=True)


def read_detailed_eso(path):
    """Read the original ESO Each Call T/H/C and verbatim fractional clocks.

    `eso_dt_h` is labelled as rounded; exact API dt_h must be joined by ordinal
    within an environment/zone for authoritative reconstruction and fitting.
    """
    identifiers, records, pending = {}, [], {}
    dictionary, environment, clock = True, None, None
    def flush():
        if not pending:
            return
        for zone, values in pending.items():
            if set(values) != set(DETAILED) or clock is None:
                raise ValueError('Incomplete original detailed temperature/H/C record')
            records.append(dict(environment=environment,zone=zone,**clock,**values,
                                net_sensible_kW=values['heating_kW']-values['cooling_kW']))
        pending.clear()
    with Path(path).open(encoding='utf-8',errors='strict') as stream:
        for raw in stream:
            line = raw.strip()
            if dictionary:
                if line == 'End of Data Dictionary':
                    dictionary = False
                    continue
                fields = line.split(',')
                if len(fields) >= 4 and fields[2] in ZONES and '!Each Call' in fields[3]:
                    for label, variable in DETAILED.items():
                        if fields[3].startswith(variable+' ['):
                            identifiers[fields[0]] = (fields[2],label)
                continue
            fields = [field.strip() for field in line.split(',')]
            if fields[0] == '1':
                flush()
                environment = fields[1]
            elif fields[0] == '2':
                flush()
                month,day,dst,hour = map(int,fields[2:6])
                start,end = fields[6:8]
                duration = (float(end)-float(start))/60
                if duration <= 0:
                    raise ValueError('Nonpositive original ESO interval')
                stamp = pd.Timestamp(2006,month,day)+pd.Timedelta(hours=hour-1+dst,minutes=float(end))
                clock = dict(eso_timestamp=stamp,month=month,day=day,dst=dst,hour=hour,
                             eso_start_minute=start,eso_end_minute=end,eso_dt_h=duration)
            elif fields[0] in identifiers:
                zone,label = identifiers[fields[0]]
                value = float(fields[1])/(1000 if label.endswith('_kW') else 1)
                if label in pending.setdefault(zone,{}):
                    raise ValueError('Duplicate detailed sample without original timestamp')
                pending[zone][label] = value
        flush()
    if not records:
        raise ValueError('Original ESO lacks requested Detailed temperature/H/C')
    return pd.DataFrame(records)
