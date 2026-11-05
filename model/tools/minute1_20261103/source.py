"""Hash-bound native minute inputs; no interpolation, filling or simulation.

The loader checks a declared finite union of source intervals. A syntactically
valid manifest alone is not an EnergyPlus sizing or source-quality acceptance.
Those independent receipts remain required before production admission.
"""
from __future__ import annotations

import copy
import csv
import hashlib
import json
import math
from numbers import Integral, Real
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd

from hospital_ies.resilience import ZONE_PRIORITY, build_zone_groups
from tools.model_review_v28.demand import DEMAND_CONTRACT_ID, source_reference, _ratio
from tools.sci_engineering.auxiliary import apply_auxiliary_allocation

SCHEMA = 'hospital_ies.ep_minute_source_manifest.v1'
METADATA_SCHEMA = 'hospital_ies.minute1.metadata.v1'
THERMAL_TRACE_CONTRACT = 'hospital_ies.minute1.ordinary_wateruse_trace.v1'
CLOCK_COLUMNS = ('minute_of_year', 'timestamp_start_standard',
    'timestamp_end_standard', 'hour_of_year', 'source_dst')
LOAD_COLUMNS = ('timestamp', 'P_e_facility_kW', 'P_e_nonplant_kW', 'P_e_plant_kW',
    'P_e_unresolved_kW', 'balance_residual_kW', 'Q_h_demand_kW', 'Q_c_demand_kW',
    'Q_c_terminal_coils_kW', 'Q_c_chiller_evaporator_kW', 'Q_h_heating_coils_kW',
    'Q_h_DHW_water_use_kW', 'outdoor_temperature_C',
    'v28_ordinary_DHW_source_delivered_kW', 'sci_demand_boundary_contract')
FAN_COLUMNS = tuple('fan_component_'+name+'_kW' for name in (
    'VAV_1', 'VAV_2', 'VAV_ER', 'VAV_OR', 'VAV_ICU', 'VAV_PATRMS', 'VAV_LABS',
    'CAV_KITCHEN', 'CORRIDOR_FLR_5_EXHAUST_FAN', 'DINING_FLR_5_EXHAUST_FAN',
    'KITCHEN_FLR_5_EXHAUST_FAN', 'NURSESTN_LOBBY_FLR_5_EXHAUST_FAN',
    'OFFICE1_FLR_5_EXHAUST_FAN', 'OFFICE2_MULT5_FLR_5_EXHAUST_FAN',
    'OFFICE3_FLR_5_EXHAUST_FAN', 'OFFICE4_MULT6_FLR_5_EXHAUST_FAN'))
AUX_COLUMNS = tuple(f'P{g}_{kind}_kW' for g in range(4) for kind in
    ('fan', 'chw_pump', 'heat_pump')) + tuple(f'P{g}_base_{k}_kW' for g in range(4)
    for k in 'ehc') + ('source_fans_total_kW', 'source_chw_pump_kW',
    'source_heat_pump_kW', 'auxiliary_added_e_kW') + FAN_COLUMNS
GROUP_COLUMNS = tuple(f'P{g}_{k}_kW' for g in range(4) for k in 'ehc')
REFERENCE_COLUMNS = tuple(f'P{g}_reference_{k}_kW' for g in range(4) for k in 'ehc')
ZONE_COLUMNS = ('timestamp', 'zone_name', 'zone_multiplier', 'interior_equipment_kW',
    'interior_lighting_kW', 'zone_mean_air_temperature_C',
    'thermostat_heating_setpoint_C', 'thermostat_cooling_setpoint_C',
    'zone_sensible_heating_kW', 'zone_sensible_cooling_kW',
    'associated_reheat_coil_name', 'associated_reheat_coil_heating_kW')
THERMAL_COLUMNS = ('ordinary_DHW_target_kW', 'ordinary_DHW_original_delivered_kW',
    'space_heating_original_kW', 'central_heat_request_kW',
    'local_laundry_source_reference_NaturalGas_input_kW',
    'local_booster_source_reference_electric_input_already_in_Dref_kW',
    'booster_unmodelled_upstream_preheat_useful_kW')
_TEXT = {'timestamp', 'timestamp_start_standard', 'timestamp_end_standard',
    'zone_name', 'associated_reheat_coil_name', 'sci_demand_boundary_contract'}


def sha256(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            value.update(block)
    return value.hexdigest()


def validate_source_spec(spec):
    if not isinstance(spec, dict) or spec.get('schema') != SCHEMA:
        raise ValueError('Explicit native minute source manifest required')
    files = spec.get('files')
    if not isinstance(files, dict) or set(files) != {'loads', 'critical_zones', 'thermal_components', 'metadata'}:
        raise ValueError('Complete minute loads/zones/thermal/metadata bindings required')
    for name, item in files.items():
        if not isinstance(item, dict) or not isinstance(item.get('path'), str) or not item['path']:
            raise ValueError('Explicit minute source path required: '+name)
        expected = item.get('sha256')
        if (not isinstance(expected, str) or len(expected) != 64
                or any(c not in '0123456789abcdef' for c in expected)):
            raise ValueError('Explicit minute source hash required: '+name)
        if 'rows' in item and (isinstance(item['rows'], bool) or not isinstance(item['rows'], Integral) or item['rows'] <= 0):
            raise ValueError('Invalid minute source row count: '+name)
    return copy.deepcopy(spec)


def _integer(value, name, *, low=1, high=525601):
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value) or value != int(value) or not low <= value <= high:
        raise ValueError('Invalid minute source integer: '+name)
    return int(value)


def _clock(frame, metadata, *, zones=False):
    required = set(CLOCK_COLUMNS)
    if not required <= set(frame):
        raise ValueError('Missing source minute clock columns')
    raw = frame.minute_of_year.to_numpy(float)
    if not np.isfinite(raw).all() or np.any(raw != raw.astype(int)) or np.any(raw < 1) or np.any(raw > 525600):
        raise ValueError('Invalid source minute interval numbers')
    minutes = raw.astype(int)
    if not np.array_equal(frame.hour_of_year.to_numpy(float), (minutes-1)//60+1):
        raise ValueError('Source minute/hour clock mismatch')
    if not frame.source_dst.isin([0, 1]).all():
        raise ValueError('Invalid separate source DST values')
    base = pd.Timestamp(year=metadata['calendar_year'], month=1, day=1)
    start = pd.to_datetime(frame.timestamp_start_standard, errors='raise')
    end = pd.to_datetime(frame.timestamp_end_standard, errors='raise')
    expected = base + pd.to_timedelta(minutes-1, unit='min')
    if start.dt.tz is not None or end.dt.tz is not None or not np.array_equal(start.to_numpy(), expected.to_numpy()) or not np.array_equal(end.to_numpy(), (expected+pd.Timedelta(minutes=1)).to_numpy()):
        raise ValueError('Source minute standard interval clocks differ')
    if 'timestamp' in frame and not np.array_equal(pd.to_datetime(frame.timestamp).to_numpy(), end.to_numpy()):
        raise ValueError('Legacy timestamp must equal minute standard interval end')
    intervals = metadata['support_intervals']
    expected_minutes = np.concatenate([np.arange(s['start_minute_of_year'], s['end_minute_of_year_exclusive']) for s in intervals])
    observed = np.unique(minutes) if zones else minutes
    if not np.array_equal(observed, expected_minutes):
        raise ValueError('Source minute support has gaps, duplicates, ordering errors or undeclared values')
    return minutes


def _frame(path, required):
    with path.open('r', encoding='utf-8-sig', newline='') as stream:
        header = next(csv.reader(stream), [])
    if len(header) != len(set(header)):
        raise ValueError('Duplicate minute CSV column')
    data = pd.read_csv(path, float_precision='round_trip')
    if not set(required) <= set(data):
        raise ValueError('Missing minute source columns: '+','.join(sorted(set(required)-set(data))))
    if data.empty or data.isna().any().any():
        raise ValueError('Empty or missing minute source values')
    for column in set(data)-_TEXT:
        values = pd.to_numeric(data[column], errors='raise').to_numpy(float)
        if not np.isfinite(values).all():
            raise ValueError('Nonfinite minute source values: '+column)
        if (column.endswith(('_kW', '_kWh')) and 'signed' not in column
                and column != 'balance_residual_kW' and np.any(values < 0.)):
            raise ValueError('Negative source power/energy: '+column)
    return data


def _close(a, b, name, *, atol=1e-8):
    if not np.allclose(np.asarray(a, float), np.asarray(b, float), atol=atol, rtol=0.):
        raise ValueError('Minute source boundary does not close: '+name)


class MinuteSource:
    def __init__(self, root, spec):
        self.root = Path(root).resolve()
        self.spec = validate_source_spec(spec)
        paths = {}
        for name, item in self.spec['files'].items():
            path = Path(item['path']); path = path if path.is_absolute() else self.root/path
            path = path.resolve()
            if not path.is_file() or sha256(path) != item['sha256']:
                raise ValueError('Minute source file/hash mismatch: '+name)
            paths[name] = path
        self.paths = paths
        metadata = json.loads(paths['metadata'].read_text(encoding='utf-8-sig'))
        if (metadata.get('schema') != METADATA_SCHEMA or metadata.get('interval_minutes') != 1.
                or metadata.get('native_timestep_minutes') != 1.
                or isinstance(metadata.get('calendar_year'), bool)
                or metadata.get('calendar_year') != 2017):
            raise ValueError('Native one-minute source and original standard calendar required')
        if metadata.get('warmup_excluded') is not True:
            raise ValueError('Source warmup exclusion must be explicit')
        if not isinstance(metadata.get('source_provenance'), dict) or not metadata['source_provenance']:
            raise ValueError('Minute source provenance is missing')
        intervals = metadata.get('support_intervals')
        if not isinstance(intervals, list) or not intervals:
            raise ValueError('Explicit finite minute support required')
        previous = 0
        for item in intervals:
            if not isinstance(item, dict) or set(item) != {'start_minute_of_year', 'end_minute_of_year_exclusive'}:
                raise ValueError('Invalid minute support interval')
            a = _integer(item['start_minute_of_year'], 'support start', high=525600)
            b = _integer(item['end_minute_of_year_exclusive'], 'support end')
            if a < previous or b <= a:
                raise ValueError('Overlapping, unordered or empty source support')
            previous = b
        self.metadata = copy.deepcopy(metadata)
        frames = dict(loads=_frame(paths['loads'], (*CLOCK_COLUMNS, *LOAD_COLUMNS, *AUX_COLUMNS, *GROUP_COLUMNS, *REFERENCE_COLUMNS)),
            critical_zones=_frame(paths['critical_zones'], (*CLOCK_COLUMNS, *ZONE_COLUMNS)),
            thermal_components=_frame(paths['thermal_components'], (*CLOCK_COLUMNS, *THERMAL_COLUMNS)))
        for name, frame in frames.items():
            if 'rows' in self.spec['files'][name] and len(frame) != self.spec['files'][name]['rows']:
                raise ValueError('Minute source bound row count differs: '+name)
            _clock(frame, metadata, zones=name=='critical_zones')
        loads, zones, thermal = (frames[k] for k in ('loads','critical_zones','thermal_components'))
        if zones.duplicated(['minute_of_year','zone_name']).any() or set(zones.zone_name) != set(ZONE_PRIORITY):
            raise ValueError('Critical minute zone identities are missing, duplicated or changed')
        counts = zones.groupby('minute_of_year').zone_name.nunique()
        if not (counts == len(ZONE_PRIORITY)).all() or np.any(zones.zone_multiplier.to_numpy(float) <= 0.):
            raise ValueError('Complete positive-multiplier critical minute zones required')
        if not loads.sci_demand_boundary_contract.eq(DEMAND_CONTRACT_ID).all():
            raise ValueError('Explicit unchanged direct/auxiliary demand boundary required')
        for column in CLOCK_COLUMNS:
            _close(loads[column], thermal[column], 'thermal clock') if column in ('minute_of_year','hour_of_year','source_dst') else None
        _close(thermal.central_heat_request_kW, thermal.space_heating_original_kW+thermal.ordinary_DHW_target_kW, 'central heat components')
        _close(loads.Q_h_demand_kW, thermal.central_heat_request_kW, 'current heat target')
        _close(loads.Q_h_heating_coils_kW, thermal.space_heating_original_kW, 'space heat')
        _close(loads.Q_h_DHW_water_use_kW, thermal.ordinary_DHW_target_kW, 'ordinary DHW target')
        _close(loads.v28_ordinary_DHW_source_delivered_kW, thermal.ordinary_DHW_original_delivered_kW, 'ordinary DHW source delivery')
        _close(loads.P_e_facility_kW, loads.P_e_nonplant_kW+loads.P_e_plant_kW+loads.P_e_unresolved_kW, 'source electricity')
        for k, total in (('e', loads.P_e_nonplant_kW+loads.P_e_unresolved_kW+loads.auxiliary_added_e_kW),
                         ('h', loads.Q_h_demand_kW), ('c', loads.Q_c_demand_kW)):
            _close(loads[[f'P{g}_{k}_kW' for g in range(4)]].sum(axis=1), total, 'priority '+k)
        _close(loads[[f'P{g}_heat_pump_kW' for g in range(4)]].sum(axis=1), loads.auxiliary_added_e_kW, 'added heat circulation')
        _close(loads[list(FAN_COLUMNS)].sum(axis=1), loads.source_fans_total_kW, 'source fan components')
        # The historical helpers group by a column named hour_of_year. Only
        # these detached computational copies use minute IDs as unique keys;
        # no source clock or exported identity is relabelled.
        grouping_loads = loads.copy(deep=True)
        grouping_zones = zones.copy(deep=True)
        grouping_aux = loads.copy(deep=True)
        for frame in (grouping_loads,grouping_zones,grouping_aux):
            frame['hour_of_year'] = frame.minute_of_year
        grouping_loads['Q_h_DHW_water_use_kW'] = thermal.ordinary_DHW_original_delivered_kW.to_numpy()
        grouping_loads['Q_h_demand_kW'] = (thermal.space_heating_original_kW+thermal.ordinary_DHW_original_delivered_kW).to_numpy()
        base_groups = build_zone_groups(grouping_zones,grouping_loads)
        reference_groups = apply_auxiliary_allocation(base_groups,grouping_aux)
        for g in range(4):
            for carrier in 'ehc':
                column = f'P{g}_{carrier}_kW'
                _close(loads[f'P{g}_base_{carrier}_kW'],base_groups[column], 'source priority base '+column)
                _close(loads[f'P{g}_reference_{carrier}_kW'],reference_groups[column], 'source priority reference '+column)
        expected_groups = reference_groups.copy(deep=True)
        expected_groups['P3_h_kW'] += (thermal.ordinary_DHW_target_kW-thermal.ordinary_DHW_original_delivered_kW).to_numpy()
        for column in GROUP_COLUMNS:
            _close(loads[column],expected_groups[column], 'source priority current '+column)
        self._loads, self._groups, self._thermal = {}, {}, {}
        for record in loads.to_dict('records'):
            row = SimpleNamespace(**record)
            reference = source_reference(row)
            current = np.array([[record[f'P{g}_{k}_kW'] for k in 'ehc'] for g in range(4)], float)
            # Use the original zero-reference behavior; no epsilon or surrogate fallback.
            _ratio(current[:,1:], reference['source_load_reference_kW'][:,1:])
            _ratio(current[:,0], reference['source_load_reference_kW'][:,0])
            m = int(record['minute_of_year'])
            self._loads[m] = record
            self._groups[m] = {column:float(record[column]) for column in GROUP_COLUMNS}
        self._components = {int(r['minute_of_year']):r for r in thermal.to_dict('records')}
        for minute, frame in zones.groupby('minute_of_year', sort=False):
            self._thermal[int(minute)] = {r['zone_name']:r for r in frame.to_dict('records')}

    def _minute(self, elapsed, start_hour):
        hour = _integer(start_hour, 'start hour', high=8760)
        if isinstance(elapsed, bool) or not isinstance(elapsed, Real) or not math.isfinite(elapsed) or elapsed < 0.:
            raise ValueError('Finite nonnegative elapsed source time required')
        minute = (hour-1)*60+math.floor(float(elapsed))+1
        if minute not in self._loads:
            raise ValueError('Current minute is outside the explicitly bound source support')
        return minute

    def at_elapsed(self, elapsed, start_hour):
        minute = self._minute(elapsed, start_hour)
        value = copy.deepcopy(self._loads[minute]); components = self._components[minute]
        value['timestamp'] = pd.Timestamp(value['timestamp'])
        value['minute1_thermal_service_trace'] = dict(contract_id=THERMAL_TRACE_CONTRACT,
            path=str(self.paths['thermal_components']), sha256=self.spec['files']['thermal_components']['sha256'],
            minute_of_year=minute, hour_of_year=int(value['hour_of_year']),
            boundary='ordinary_wateruse_central_target_minute1', request_column='ordinary_DHW_target_kW',
            legacy_delivery_column='ordinary_DHW_original_delivered_kW')
        value['v28_local_thermal_source_reference'] = dict(minute_of_year=minute,
            laundry_naturalgas_kW=components['local_laundry_source_reference_NaturalGas_input_kW'],
            booster_electric_kW_already_in_Dref=components['local_booster_source_reference_electric_input_already_in_Dref_kW'],
            booster_unmodelled_upstream_preheat_useful_kW=components['booster_unmodelled_upstream_preheat_useful_kW'],
            assurance='Bound minute source references; no dynamic local heater assurance inferred')
        return int(value['hour_of_year']), SimpleNamespace(**value), copy.deepcopy(self._groups[minute])

    def thermal_at_elapsed(self, elapsed, start_hour):
        minute = self._minute(elapsed, start_hour)
        return {name:SimpleNamespace(**copy.deepcopy(row)) for name,row in self._thermal[minute].items()}
