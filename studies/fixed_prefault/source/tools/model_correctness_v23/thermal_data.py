"""V23 native EnergyPlus data, explicit clocks and nonduplicated zone boundary.

Only derived IDFs and a caller-selected new destination are written. Existing
inputs and V21/V22 evidence are never changed. SQL is always opened read-only.
"""
from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import time

import numpy as np
import pandas as pd

ZONES = ('OR1_FLR_2', 'OR2_MULT5_FLR_2', 'OR3_FLR_2', 'OR4_FLR_2')
COIL = 'VAV_OR Cooling Coil'
VARIABLES = {
    'temperature_C': 'Zone Mean Air Temperature',
    'radiant_C': 'Zone Mean Radiant Temperature',
    'setpoint_C': 'Zone Thermostat Cooling Setpoint Temperature',
    'heating_kW': 'Zone Air System Sensible Heating Rate',
    'cooling_kW': 'Zone Air System Sensible Cooling Rate',
    'internal_convective_kW': 'Zone Air Heat Balance Internal Convective Heat Gain Rate',
    'surface_convection_kW': 'Zone Air Heat Balance Surface Convection Rate',
    'interzone_kW': 'Zone Air Heat Balance Interzone Air Transfer Rate',
    'outdoor_transfer_kW': 'Zone Air Heat Balance Outdoor Air Transfer Rate',
    'system_air_kW': 'Zone Air Heat Balance System Air Transfer Rate',
    'system_convective_kW': 'Zone Air Heat Balance System Convective Heat Gain Rate',
    'air_storage_kW': 'Zone Air Heat Balance Air Energy Storage Rate',
    'internal_radiant_kW': 'Zone Total Internal Radiant Heating Rate',
    'internal_visible_kW': 'Zone Total Internal Visible Radiation Heating Rate',
    'infiltration_gain_kW': 'Zone Infiltration Sensible Heat Gain Energy',
    'infiltration_loss_kW': 'Zone Infiltration Sensible Heat Loss Energy',
}


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _replace_object(text, kind, name, transform):
    count = 0
    def replace(match):
        nonlocal count
        clean = '\n'.join(line.split('!', 1)[0] for line in match.group().splitlines())
        fields = [part.strip() for part in clean.rstrip().rstrip(';').split(',')]
        if fields[0].upper() != kind.upper() or (name is not None and fields[1].upper() != name.upper()):
            return match.group()
        count += 1
        return '\n' + ',\n  '.join(transform(fields)) + ';'
    result = re.sub(r'[^;]+;', replace, text)
    if count != 1:
        raise ValueError(f'Expected exactly one {kind}/{name}; found {count}')
    return result


def derive_idf(source_text, *, event, outage_hours, pre_days=2, post_days=2, interval_minutes=5):
    """Keep physical objects intact; change reporting, dates and one shared coil."""
    if not 0 <= float(outage_hours) < 12 or event.hour != 12:
        raise ValueError('Registered outage must start at noon and end on the same day')
    first = event.replace(hour=0) - timedelta(days=pre_days)
    last = event.replace(hour=0) + timedelta(days=post_days)
    def period(fields):
        fields[1:9] = ['V23_PAIRED_THERMAL', str(first.month), str(first.day), '',
                       str(last.month), str(last.day), '', first.strftime('%A')]
        return fields
    if interval_minutes not in (5, 10):
        raise ValueError('Only registered native five/ten-minute intervals are supported')
    timesteps = 60 // interval_minutes
    result = _replace_object(source_text, 'RunPeriod', None, period)
    result = _replace_object(result, 'Timestep', None, lambda _: ['Timestep', str(timesteps)])
    # Preserve the source 60-minute design-load averaging duration when the
    # numerical zone interval changes from 10 to 5 minutes. Sizing factors stay
    # intact. Without this, merely requesting higher-resolution data changes
    # the averaging duration from one hour to half an hour.
    if re.search(r'\bSizing:Parameters\s*,', source_text, re.I):
        def sizing(fields):
            fields[3] = str(int(fields[3]) * timesteps // 6)
            return fields
        result = _replace_object(result, 'Sizing:Parameters', None, sizing)
    if outage_hours:
        def coil(fields):
            fields[2] = 'V23_OR_COOLING_AVAILABILITY'
            return fields
        result = _replace_object(result, 'Coil:Cooling:Water', COIL, coil)
        end = 12 * 60 + int(round(float(outage_hours) * 60))
        previous = event - timedelta(days=1)
        result += ('\nSchedule:Compact,V23_OR_COOLING_AVAILABILITY,Fraction,\n'
                   f'Through: {previous.month}/{previous.day},For: AllDays,Until: 24:00,1,\n'
                   f'Through: {event.month}/{event.day},For: AllDays,Until: 12:00,1,\n'
                   f'Until: {end//60:02d}:{end%60:02d},0,Until: 24:00,1,\n'
                   'Through: 12/31,For: AllDays,Until: 24:00,1;\n')
    for zone in ZONES:
        for variable in VARIABLES.values():
            result += f'\nOutput:Variable,{zone},{variable},Timestep;'
    result += f'\nOutput:Variable,{COIL},Cooling Coil Total Cooling Rate,Timestep;'
    # Native HVAC intervals allow independent integration checks; no resampling
    # of an hourly heat rate is used to construct the five-minute table.
    for variable in ('Zone Air System Sensible Heating Rate',
                     'Zone Air System Sensible Cooling Rate',
                     'Zone Air Heat Balance System Air Transfer Rate'):
        result += f'\nOutput:Variable,{ZONES[0]},{variable},Detailed;'
    result += '\nOutput:VariableDictionary,IDF;\n'
    return result


def sql_clocks(month, day, hour, minute, dst):
    standard = datetime(2006, int(month), int(day)) + timedelta(hours=int(hour), minutes=int(minute))
    return standard, standard + timedelta(hours=int(dst))


def validate_native_frame(frame, interval_minutes=5):
    if len(frame) < 2 or frame.index.has_duplicates or not np.all(np.diff(frame.index.asi8) == interval_minutes * 60_000_000_000):
        raise ValueError(f'Expected complete continuous native {"five-minute" if interval_minutes == 5 else "ten-minute"} data')
    if not np.isfinite(frame.select_dtypes(include=[np.number]).to_numpy()).all():
        raise ValueError('Native thermal data contains nonfinite values')


def _read_series(connection, variable, key, interval_minutes=5):
    rows = connection.execute(
        'SELECT t.Month,t.Day,t.Hour,t.Minute,t.Dst,x.VariableValue,d.VariableUnits,t.Interval '
        'FROM ReportVariableData x JOIN ReportVariableDataDictionary d USING(ReportVariableDataDictionaryIndex) '
        'JOIN Time t USING(TimeIndex) JOIN EnvironmentPeriods e USING(EnvironmentPeriodIndex) '
        "WHERE e.EnvironmentType=3 AND d.VariableName=? AND upper(d.KeyValue)=upper(?) "
        "AND d.ReportingFrequency IN ('Zone Timestep','Timestep') ORDER BY t.TimeIndex", (variable, key)).fetchall()
    if not rows:
        raise ValueError(f'Missing real native output: {variable}/{key}')
    clocks = [sql_clocks(*row[:5]) for row in rows]
    values = []
    for *_, value, unit, interval in rows:
        if interval != interval_minutes:
            raise ValueError(f'Unexpected native input interval: {variable}/{interval}')
        if unit == 'W':
            value /= 1000.
        elif unit == 'J':
            value /= interval_minutes * 60_000.  # real native integrated energy / seconds / 1000
        elif unit != 'C':
            raise ValueError(f'Unexpected unit {unit} for {variable}')
        values.append(float(value))
    return pd.Series(values, index=pd.DatetimeIndex([pair[1] for pair in clocks])), clocks


def read_native(database, interval_minutes=5):
    result = {}
    with sqlite3.connect(Path(database).resolve().as_uri() + '?mode=ro', uri=True) as connection:
        for zone in ZONES:
            columns = {}
            reference = None
            for label, variable in VARIABLES.items():
                series, clocks = _read_series(connection, variable, zone, interval_minutes)
                if reference is None:
                    reference = clocks
                if clocks != reference:
                    raise ValueError('Thermal channels do not share the same native clock')
                columns[label] = series
            coil, clocks = _read_series(connection, 'Cooling Coil Total Cooling Rate', COIL, interval_minutes)
            if clocks != reference:
                raise ValueError('Coil and zone clocks differ')
            columns['shared_coil_total_kW'] = coil
            frame = pd.DataFrame(columns)
            frame['standard_timestamp'] = [pair[0] for pair in clocks]
            frame['schedule_timestamp'] = [pair[1] for pair in clocks]
            validate_native_frame(frame, interval_minutes)
            result[zone] = frame
    return result


def allocate_priority_delivery(baseline_zone_multiplied_kW, multipliers, actual_priority_kW):
    """Proportional current-baseline allocation; return single-zone delivered kW.

    The dictionary MUST contain the complete priority group, including rooms
    outside the OR airloop. The multiplier appears exactly once on the return
    boundary. This explicit research allocation is not room-level metering.
    """
    if set(baseline_zone_multiplied_kW) != set(multipliers):
        raise ValueError('Incomplete zone multiplier mapping')
    values = {z: float(q) for z, q in baseline_zone_multiplied_kW.items()}
    if any(q < 0 or not np.isfinite(q) for q in values.values()):
        raise ValueError('Invalid baseline zone cooling')
    supplied = float(actual_priority_kW)
    if not np.isfinite(supplied) or supplied < 0 or any(float(m) <= 0 for m in multipliers.values()):
        raise ValueError('Invalid priority delivery or multiplier')
    total = sum(values.values())
    if supplied > total + 1e-7:
        raise ValueError('Recovery overdelivery requires a separately declared zone allocation')
    return {z: (supplied * q / total / float(multipliers[z]) if total else 0.) for z, q in values.items()}


def validate_protocol(protocol):
    support = []
    for date in protocol['dates']:
        event = datetime(2006, date['month'], date['day'])
        block = set(event + timedelta(days=offset)
                    for offset in range(-protocol['pre_days'], protocol['post_days'] + 1))
        for old_split, old_block in support:
            if old_split != date['split'] and block & old_block:
                raise ValueError('Calibration/validation full source support overlap')
        support.append((date['split'], block))
    return True


def run_case(executable, weather, idf, destination, temporary):
    """One synchronous external process; record failed and successful attempts."""
    destination, temporary = Path(destination), Path(temporary)
    destination.mkdir(parents=True, exist_ok=False)
    temporary.mkdir(parents=True, exist_ok=True)
    try:
        import psutil
        running = [p.pid for p in psutil.process_iter(['name'])
                   if (p.info['name'] or '').lower() == 'energyplus.exe']
        if running:
            raise RuntimeError(f'Another EnergyPlus process is already running: {running}')
    except ImportError:
        pass
    command = [str(executable), '-w', str(weather), '-d', str(destination), str(idf)]
    env = dict(os.environ, TEMP=str(temporary), TMP=str(temporary))
    start = time.perf_counter()
    with (destination / 'process.log').open('wb') as stream:
        result = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT, env=env,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    errors = (destination / 'eplusout.err').read_text(encoding='utf-8', errors='replace')
    receipt = dict(command=command, exit_code=result.returncode,
                   elapsed_seconds=time.perf_counter() - start, idf_sha256=sha256(idf),
                   temp=str(temporary), severe=errors.count('** Severe **'),
                   fatal=errors.count('**  Fatal  **'), sql_path=str(destination / 'eplusout.sql'))
    (destination / 'RUN_RECEIPT.json').write_text(json.dumps(receipt, indent=2), encoding='utf-8')
    if result.returncode:
        raise RuntimeError(f'EnergyPlus failed; preserved receipt: {destination}')
    return receipt
