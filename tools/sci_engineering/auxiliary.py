"""Auditable ownership of existing fans/CHW pumping and added heat circulation.

This module extracts source data; it does not implement a hydraulic network or
grant execution permissions. Shared fan allocation by design flow and shared
pump allocation by current carrier request are explicit research assumptions.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3

import numpy as np
import pandas as pd

from hospital_ies.idf_parameters import parse_idf_objects
from hospital_ies.resilience import GROUPS, ZONE_PRIORITY, build_zone_groups

CONTRACT_ID = 'hospital_ies.auxiliary_source_accounting.v21.v1'
ALLOCATION_FILE = 'auxiliary_allocation_8760.csv'
METADATA_FILE = 'auxiliary_metadata.json'
KINDS = ('fan', 'chw_pump', 'heat_pump')


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _validate(frame, *, annual=False):
    required = {'hour_of_year', 'auxiliary_added_e_kW'} | {
        f'{g}_{kind}_kW' for g in GROUPS for kind in KINDS}
    if not required <= set(frame):
        raise ValueError('Auxiliary columns missing: ' + ','.join(sorted(required - set(frame))))
    if frame.hour_of_year.duplicated().any() or frame.hour_of_year.isna().any():
        raise ValueError('Auxiliary hour clock must be unique and complete')
    values = frame[list(required - {'hour_of_year'})].to_numpy(dtype=float)
    if not np.isfinite(values).all() or (values < 0).any():
        raise ValueError('Auxiliary power must be finite and nonnegative')
    if annual and frame.hour_of_year.tolist() != list(range(1, 8761)):
        raise ValueError('Auxiliary annual hours must be exactly 1..8760')
    if not np.allclose(frame[[f'{g}_heat_pump_kW' for g in GROUPS]].sum(axis=1),
                       frame.auxiliary_added_e_kW, rtol=0, atol=1e-10):
        raise ValueError('Added heat pump electricity does not match component allocation')


def allocate_fan_power(power_by_loop, design_flow_by_loop, *, critical_share_scale=1.):
    """Distribute each measured fan once; component design flow already has m.

    Sensitivity modifies critical ownership only, retaining the source fan total
    through its P3 remainder. A fan exclusively serving P0 has no P3 remainder
    available to support an upward ownership change: reject, never invent load.
    """
    scale = float(critical_share_scale)
    if not math.isfinite(scale) or scale < 0:
        raise ValueError('Invalid critical fan share scale')
    if set(power_by_loop) != set(design_flow_by_loop) or not power_by_loop:
        raise ValueError('Fan power and design-flow loop identities differ')
    result = None
    for loop, power in power_by_loop.items():
        power = np.asarray(power, dtype=float)
        weights = np.array([design_flow_by_loop[loop].get(g, 0.) for g in GROUPS], dtype=float)
        if (not np.isfinite(weights).all() or (weights < 0).any() or weights.sum() <= 0
                or not np.isfinite(power).all() or (power < 0).any()):
            raise ValueError('Fan power/design share must be finite and nonnegative')
        shares = weights / weights.sum()
        shares[:3] *= scale
        if shares[:3].sum() > 1. + 1e-12:
            raise ValueError('Critical fan share exceeds physical fan total')
        shares[3] = 1. - shares[:3].sum()
        part = power[:, None] * shares[None, :]
        part[:, 3] = power - part[:, :3].sum(axis=1)
        if result is None:
            result = part
        elif result.shape != part.shape:
            raise ValueError('Fan hourly series lengths differ')
        else:
            result += part
    return result


def apply_auxiliary_allocation(group_loads, allocation):
    """Move fan/CHW ownership out of P3; add only separate HeatSys electricity."""
    _validate(allocation)
    if group_loads.hour_of_year.duplicated().any():
        raise ValueError('Priority load hour clock is duplicated')
    indexed = allocation.set_index('hour_of_year')
    if not set(group_loads.hour_of_year) <= set(indexed.index):
        raise ValueError('Auxiliary hour support is missing')
    selected = indexed.loc[group_loads.hour_of_year]
    out = group_loads.copy(deep=True)
    transfer = np.zeros(len(out))
    for g in GROUPS[:3]:
        existing = (selected[f'{g}_fan_kW'] + selected[f'{g}_chw_pump_kW']).to_numpy()
        transfer += existing
        out[f'{g}_e_kW'] += existing + selected[f'{g}_heat_pump_kW'].to_numpy()
    if np.any(group_loads.P3_e_kW.to_numpy() - transfer < -1e-10):
        raise ValueError('Auxiliary reassignment exceeds existing P3 electricity')
    out['P3_e_kW'] = group_loads.P3_e_kW.to_numpy() - transfer + selected.P3_heat_pump_kW.to_numpy()
    return out


def _fan_topology(objects, connection):
    """Join actual IDF airloop/splitter/terminal/ADU/equipment connections."""
    def by_name(kind):
        return {f[0].upper(): f for f in objects.get(kind, [])}
    adus = by_name('ZONEHVAC:AIRDISTRIBUTIONUNIT')
    lists = by_name('ZONEHVAC:EQUIPMENTLIST')
    terminal_zones = {}
    for f in objects['ZONEHVAC:EQUIPMENTCONNECTIONS']:
        equipment = lists[f[1].upper()]
        for i, field in enumerate(equipment[:-1]):
            if field.upper() == 'ZONEHVAC:AIRDISTRIBUTIONUNIT':
                terminal_zones[adus[equipment[i+1].upper()][3].upper()] = f[0].upper()
    terminals = by_name('AIRTERMINAL:SINGLEDUCT:VAV:REHEAT')
    inlets = {f[3].upper(): f for f in terminals.values()}
    flows = dict(connection.execute("SELECT CompName,Value FROM ComponentSizes "
        "WHERE CompType='AirTerminal:SingleDuct:VAV:Reheat' "
        "AND Description='Design Size Maximum Air Flow Rate'"))
    flows = {k.upper(): float(v) for k, v in flows.items()}
    splitters = by_name('AIRLOOPHVAC:ZONESPLITTER')
    paths = {f[1].upper(): f for f in objects['AIRLOOPHVAC:SUPPLYPATH']}
    branches = by_name('BRANCH')
    branchlists = by_name('BRANCHLIST')
    fan_names = {f[0].upper() for kind, rows in objects.items()
                 if kind.startswith('FAN:') for f in rows}
    mapping = []
    for loop in objects['AIRLOOPHVAC']:
        fans = []
        for branch_name in branchlists[loop[4].upper()][1:]:
            branch = branches[branch_name.upper()]
            for i, field in enumerate(branch[:-1]):
                if field.upper().startswith('FAN:'):
                    fans.append(branch[i+1].upper())
        if len(fans) != 1:
            raise ValueError('Airloop must have one auditable supply fan: ' + loop[0])
        splitter = splitters[paths[loop[8].upper()][3].upper()]
        zone_rows = []
        weights = dict.fromkeys(GROUPS, 0.)
        for node in splitter[2:]:
            terminal = inlets.get(node.upper())
            if terminal is None:
                # CAV kitchen uses an uncontrolled terminal and serves only P3.
                if loop[0].upper() != 'CAV_KITCHEN':
                    raise ValueError('Unresolved air terminal inlet: ' + node)
                continue
            name = terminal[0].upper()
            if name not in terminal_zones or name not in flows:
                raise ValueError('Terminal zone/design flow evidence missing: ' + name)
            zone = terminal_zones[name]
            priority = GROUPS[ZONE_PRIORITY.get(zone, 3)]
            weights[priority] += flows[name]
            zone_rows.append(dict(zone=zone, priority=priority, terminal=name,
                design_max_airflow_m3_s=flows[name]))
        if not zone_rows:
            weights['P3'] = 1.
        total = sum(weights.values())
        mapping.append(dict(airloop=loop[0].upper(), fan=fans[0],
            priority_design_flows_m3_s=weights,
            priority_shares={g: weights[g]/total for g in GROUPS}, zones=zone_rows))
    mapped = {r['fan'] for r in mapping}
    for fan in sorted(fan_names - mapped):
        mapping.append(dict(airloop=fan, fan=fan,
            priority_design_flows_m3_s=dict(P0=0., P1=0., P2=0., P3=1.),
            priority_shares=dict(P0=0., P1=0., P2=0., P3=1.), zones=[],
            scope='remaining IDF exhaust fan, outside the registered critical zones'))
    return mapping


def _read_component(connection, clock, variable, key):
    dictionary = connection.execute('SELECT ReportVariableDataDictionaryIndex,VariableUnits '
        'FROM ReportVariableDataDictionary WHERE ReportingFrequency=? AND VariableName=? AND KeyValue=?',
        ('Hourly', variable, key)).fetchall()
    if len(dictionary) != 1 or dictionary[0][1] != 'W':
        raise ValueError('Unique hourly W component evidence missing: ' + variable + '/' + key)
    values = dict(connection.execute('SELECT TimeIndex,VariableValue FROM ReportVariableData '
        'WHERE ReportVariableDataDictionaryIndex=?', (dictionary[0][0],)))
    if not set(clock) <= set(values):
        raise ValueError('Missing component hour: ' + key)
    array = np.array([values[t] / 1000. for t in clock])
    if not np.isfinite(array).all() or (array < 0.).any():
        raise ValueError('Invalid component power: ' + key)
    return array


def compute_auxiliary_allocation(root, source_path=None, *, critical_share_scale=1.):
    """Return annual auxiliary fields; metadata is in ``frame.attrs['metadata']``.

    Explicit source_path is a Starter directory or its original annual SQL.
    With no source_path use the hash-verified compact published CSV instead.
    This allows independent replay without the original SQL or local paths.
    """
    root = Path(root).resolve()
    if source_path is None:
        destination = root / 'inputs/sci_engineering'
        metadata_path = destination / METADATA_FILE
        if not metadata_path.is_file():
            raise ValueError('Auxiliary metadata missing; run scripts/sci_sources.py with explicit source')
        metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
        allocation_path = destination / ALLOCATION_FILE
        if not allocation_path.is_file() or sha256(allocation_path) != metadata.get('allocation_sha256'):
            raise ValueError('Auxiliary allocation hash mismatch')
        if metadata.get('contract_id') != CONTRACT_ID:
            raise ValueError('Auxiliary metadata contract mismatch')
        if float(critical_share_scale) != float(metadata['critical_share_scale']):
            raise ValueError('Ownership sensitivity requires an explicitly re-extracted source allocation')
        bindings = metadata.get('root_input_sha256')
        if not isinstance(bindings, dict) or not bindings:
            raise ValueError('Auxiliary source input hash bindings missing')
        for relative, expected in bindings.items():
            candidate = (root / relative).resolve()
            if (not candidate.is_relative_to(root) or not candidate.is_file()
                    or sha256(candidate) != expected):
                raise ValueError('Auxiliary source input hash mismatch: ' + relative)
        frame = pd.read_csv(allocation_path)
        _validate(frame, annual=True)
        frame.attrs['metadata'] = metadata
        return frame
    source = Path(source_path).resolve()
    database = source / 'simulation/engineering_audit_20261010/eplusout.sql' if source.is_dir() else source
    starter = source if source.is_dir() else database.parents[2]
    idf = starter / 'Hospital_SanDiego.idf'
    if not database.is_file() or not idf.is_file():
        raise ValueError('Explicit source needs original SQL and Starter IDF')
    hourly_path = root / 'inputs/loads/hospital_ies_demand_8760.csv'
    zone_path = root / 'inputs/loads/critical_zones_8760.csv'
    boundary_path = root / 'inputs/loads/electricity_boundary_8760.csv'
    hourly = pd.read_csv(hourly_path)
    zones = pd.read_csv(zone_path)
    boundary = pd.read_csv(boundary_path)
    for frame in (hourly, boundary):
        if frame.hour_of_year.tolist() != list(range(1, 8761)):
            raise ValueError('Original source hour support must be 1..8760')
    with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as connection:
        clock = [r[0] for r in connection.execute('SELECT TimeIndex FROM Time WHERE '
            'EnvironmentPeriodIndex IN (SELECT EnvironmentPeriodIndex FROM EnvironmentPeriods '
            'WHERE EnvironmentType=3) ORDER BY TimeIndex')]
        if len(clock) != 8760 or len(set(clock)) != 8760:
            raise ValueError('Annual source SQL must contain exactly 8760 unique hours')
        mapping = _fan_topology(parse_idf_objects(idf), connection)
        fan_power = {item['airloop']: _read_component(connection, clock, 'Fan Electricity Rate', item['fan'])
                     for item in mapping}
        measured_fan = sum(fan_power.values())
        residual = float(np.max(np.abs(measured_fan - boundary.fans_kW.to_numpy())))
        if residual > 1e-8:
            raise ValueError('Source fan components do not close the existing fans meter')
        fan = allocate_fan_power(fan_power,
            {r['airloop']: r['priority_design_flows_m3_s'] for r in mapping},
            critical_share_scale=critical_share_scale)
        chw = _read_component(connection, clock, 'Pump Electricity Rate', 'COOLSYS1 PUMP SECONDARY')
        heat = _read_component(connection, clock, 'Pump Electricity Rate', 'HEATSYS1 PUMP')
        if not np.allclose(chw, boundary.distribution_pumps_unresolved_kW, rtol=0, atol=1e-8):
            raise ValueError('CHW secondary pump does not match original included distribution electricity')
        version = connection.execute('SELECT EnergyPlusVersion FROM Simulations').fetchall()
    grouped = build_zone_groups(zones, hourly)
    out = pd.DataFrame({'hour_of_year': hourly.hour_of_year.to_numpy()})
    for carrier, name, power in (('c', 'chw_pump', chw), ('h', 'heat_pump', heat)):
        need = grouped[[f'{g}_{carrier}_kW' for g in GROUPS]].to_numpy()
        total = need.sum(axis=1)
        shares = np.divide(need, total[:, None], out=np.zeros_like(need), where=total[:, None] > 0.)
        shares[total == 0., 3] = 1.
        for i, g in enumerate(GROUPS):
            out[f'{g}_{name}_kW'] = power * shares[:, i]
    for i, g in enumerate(GROUPS):
        out[f'{g}_fan_kW'] = fan[:, i]
        for carrier in ('e', 'h', 'c'):
            out[f'{g}_base_{carrier}_kW'] = grouped[f'{g}_{carrier}_kW'].to_numpy()
    out['source_fans_total_kW'] = measured_fan
    out['source_chw_pump_kW'] = chw
    out['source_heat_pump_kW'] = heat
    component_columns = {}
    for item in mapping:
        slug = re.sub(r'[^A-Z0-9]+', '_', item['airloop']).strip('_')
        column = f'fan_component_{slug}_kW'
        if column in out:
            raise ValueError('Fan component slug collision: ' + slug)
        out[column] = fan_power[item['airloop']]
        shares = dict(item['priority_shares'])
        for g in GROUPS[:3]:
            shares[g] *= float(critical_share_scale)
        shares['P3'] = 1. - sum(shares[g] for g in GROUPS[:3])
        component_columns[item['airloop']] = dict(power_column=column,
            source_fan_key=item['fan'], priority_shares=shares)
    out['auxiliary_added_e_kW'] = heat
    _validate(out, annual=True)
    reassigned = apply_auxiliary_allocation(grouped, out)
    original_e = grouped[[f'{g}_e_kW' for g in GROUPS]].sum(axis=1)
    new_e = reassigned[[f'{g}_e_kW' for g in GROUPS]].sum(axis=1)
    if not np.allclose(new_e - original_e, heat, rtol=0, atol=1e-8):
        raise ValueError('Electricity change differs from explicitly added heat pump')
    out.attrs['metadata'] = dict(contract_id=CONTRACT_ID, critical_share_scale=float(critical_share_scale),
        source_files={str(p): sha256(p) for p in (database, idf, hourly_path, zone_path, boundary_path)},
        root_input_sha256={p.relative_to(root).as_posix(): sha256(p)
            for p in (hourly_path, zone_path, boundary_path)},
        source_engine_versions=version, fan_mapping=mapping,
        fan_component_columns=component_columns,
        zone_multiplier_applied_to_component_flow=False,
        fan_allocation_basis='IDF connected terminals, SQL ComponentSizes design maximum flow; research ownership assumption',
        chw_allocation_basis='V20 current priority cooling request shares; original distribution electricity reassigned',
        heat_allocation_basis='V20 current priority heat request shares; original DOE HeatSys1 pump added as separate circulation proxy',
        heat_pump_transfer_assumption='DOE hot-water circulation electricity transferred to the scaled NMCSD-inspired heat delivery system; not a verified steam-network pump',
        zero_carrier_demand_allocation='P3 retains measured idle pump electricity',
        fan_meter_max_abs_residual_kW=residual,
        fan_annual_kWh=float(measured_fan.sum()), chw_pump_annual_kWh=float(chw.sum()),
        added_heat_pump_annual_kWh=float(heat.sum()),
        all_other_existing_electricity='remains in existing priority grouping, mostly P3; no parent/component double counting',
        excluded_duplicate_plant_electricity='No extra condenser pump/tower fan power: existing EC .77 and ABS .14 kW/RT already include these',
        limitations=['No airflow/ACH/pressure/humidity compliance model',
            'Power allocation is not independently switchable branch evidence',
            'Recorded baseline pump power is a proxy; pump affinity and hydraulic laws are not modeled'])
    return out
