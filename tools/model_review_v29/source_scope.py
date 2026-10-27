"""Read-only, offline source-quality scope for V29 result evaluation.

Nothing in this module is an observation, action, dispatch input or added energy
source. Native hourly flags locate limited source support; they do not recover
the unknown within-hour timing of unmet thermostat service.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
from numbers import Real
from pathlib import Path


_METADATA_PATH = 'inputs/model_review_v29/SOURCE_QUALITY_METADATA.json'
_METADATA_SHA = 'af3b7c276bb6ace2f48e50af80ef6259f221a3afb30a6989a1db0e6e980d53fd'
_BINDINGS = {
    'cooling_unmet': (
        'inputs/model_review_v29/SOURCE_COOLING_UNMET_HOURS.csv',
        '8a33b9c050b8adf81aea5649328757cc71163a2ff96ef3d1cf900471d1c97799'),
    'screening': (
        'inputs/model_review_v29/ALL55_ZONES_NATIVE_UNMET_SUMMARY.csv',
        '92a5346bb8954d3ba9cb6eb229b1c9ac8ab89b7b2c6cb3b308fbb554f0e6c2ba'),
    'thermal_reference': (
        'inputs/model_review_v28/THERMAL_SERVICE_COMPONENTS_8760.csv',
        '27b608272ed268c16fc6fb86ce5fbcf66e4f4f177f3ed7feefacc15c823b11f4'),
}
_LOCAL_REGIONS = ['Kitchen_Flr_5', 'Radiology_Flr_4']
_THERMAL_COLUMNS = {
    'laundry_source_reference_kWh': 'local_laundry_source_reference_NaturalGas_input_kW',
    'booster_electric_already_in_direct_kWh': 'local_booster_source_reference_electric_input_already_in_Dref_kW',
    'booster_upstream_preheat_useful_kWh': 'booster_unmodelled_upstream_preheat_useful_kW',
}


def _bound_bytes(root, relative, expected_sha):
    try:
        data = (Path(root) / relative).read_bytes()
    except OSError as exc:
        raise ValueError('Required source-quality input missing: ' + relative) from exc
    if hashlib.sha256(data).hexdigest() != expected_sha:
        raise ValueError('Frozen source-quality input changed: ' + relative)
    return data


def _rows(data):
    try:
        rows = list(csv.DictReader(data.decode('utf8').splitlines()))
    except (UnicodeError, csv.Error) as exc:
        raise ValueError('Invalid source CSV') from exc
    if not rows or any(None in row or any(value is None for value in row.values()) for row in rows):
        raise ValueError('Empty or malformed source CSV')
    return rows


def _finite(value, name, minimum=None, integer=False):
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError('Finite numeric value required: ' + name)
    number = float(value)
    if not math.isfinite(number) or (minimum is not None and number < minimum):
        raise ValueError('Invalid finite range: ' + name)
    if integer and not number.is_integer():
        raise ValueError('Integer required: ' + name)
    return int(number) if integer else number


def _cell(value, name, minimum=None, integer=False):
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError('Invalid source numeric field: ' + name) from exc
    return _finite(number, name, minimum=minimum, integer=integer)


def _reject_constant(value):
    raise ValueError('Nonfinite source metadata: ' + value)


def load_quality(root):
    """Validate frozen local evidence and return unchanged native zone-hour rows.

    The three quality input bytes and the adopted thermal reference are pinned.
    No original source path outside ``root`` is needed for portable evaluation.
    """
    data = _bound_bytes(root, _METADATA_PATH, _METADATA_SHA)
    try:
        metadata = json.loads(data.decode('utf8'), parse_constant=_reject_constant)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError('Invalid source-quality metadata') from exc
    if (metadata.get('schema') != 'hospital_ies.v29.source_quality_scope.v1'
            or metadata.get('source_version') != 'V28'
            or metadata.get('source_mode') != 'delivery_proxy'
            or metadata.get('units', {}).get('native_unmet_zone_hours') != 'zone*h'):
        raise ValueError('Unregistered source version, mode or units')
    payload = {}
    for name, (relative, expected_sha) in _BINDINGS.items():
        binding = metadata.get(name, {})
        if binding.get('path') != relative or binding.get('sha256') != expected_sha:
            raise ValueError('Unregistered source binding: ' + name)
        payload[name] = _bound_bytes(root, relative, expected_sha)

    native = _rows(payload['cooling_unmet'])
    screening = _rows(payload['screening'])
    identities = set()
    non_numeric = {'zone', 'air_loop', 'cooling_coil', 'water_inlet_node', 'standard_hour_end'}
    for row in native:
        hour = _cell(row['hour_of_year'], 'hour_of_year', 1, integer=True)
        unmet = _cell(row['native_unmet_zone_hours'], 'native_unmet_zone_hours', 0)
        identity = (row['zone'], hour)
        if hour > 8760 or not 0 < unmet <= 1 or identity in identities:
            raise ValueError('Invalid or duplicate native zone-hour support')
        identities.add(identity)
        for column, value in row.items():
            if column not in non_numeric:
                _cell(value, column)
        _cell(row['accepted_system_intervals'], 'accepted_system_intervals', 1, integer=True)
    regions = sorted({row['zone'] for row in native})
    hours = sorted({int(row['hour_of_year']) for row in native})
    total = math.fsum(float(row['native_unmet_zone_hours']) for row in native)
    if len(native) != 26 or len(hours) != 25 or regions != _LOCAL_REGIONS or not math.isclose(total, 9.5, rel_tol=0, abs_tol=1e-12):
        raise ValueError('Frozen native source summary does not close')

    zones = {row['zone'] for row in screening}
    pairs = {(row['zone'], row['carrier']) for row in screening}
    if len(zones) != 55 or len(screening) != 110 or pairs != {(zone, carrier) for zone in zones for carrier in ('Heating', 'Cooling')}:
        raise ValueError('Complete 55-zone heating/cooling screening required')
    for row in screening:
        amount = _cell(row['total_native_zone_hours'], 'total_native_zone_hours', 0)
        count = _cell(row['hours_with_nonzero_native_unmet'], 'hours_with_nonzero_native_unmet', 0, integer=True)
        _cell(row['peak_native_unmet_h'], 'peak_native_unmet_h', 0)
        localized = [item for item in native if item['zone'] == row['zone']] if row['carrier'] == 'Cooling' else []
        expected = math.fsum(float(item['native_unmet_zone_hours']) for item in localized)
        if count != len(localized) or not math.isclose(amount, expected, rel_tol=0, abs_tol=1e-12):
            raise ValueError('All-zone screening and localized native rows differ')
    return dict(
        source_mode='delivery_proxy', zone_hour_record_count=len(native),
        unique_hour_count=len(hours), screened_zone_count=len(zones),
        native_unmet_zone_hours=total, regions=regions,
        zone_hour_records=native, metadata=metadata,
    )


def _support(start, end, *, status):
    return dict(absolute_start_minute=start, absolute_end_minute=end,
                duration_minutes=end - start, status=status)


def _intersection(rows, start, end):
    selected = []
    intersections = []
    hour_minutes = {}
    for row in rows:
        hour = int(row['hour_of_year'])
        left = max(start, (hour - 1) * 60)
        right = min(end, hour * 60)
        if left < right:
            selected.append(row.copy())
            intersections.append(dict(zone=row['zone'], hour_of_year=hour,
                absolute_start_minute=left, absolute_end_minute=right,
                support_overlap_minutes=right - left))
            hour_minutes[hour] = right - left
    total = math.fsum(float(row['native_unmet_zone_hours']) for row in selected)
    full_support = all(math.isclose(value, 60, rel_tol=0, abs_tol=1e-12) for value in hour_minutes.values())
    return dict(
        overlap_hours=sorted(hour_minutes),
        regions=sorted({row['zone'] for row in selected}),
        zone_hour_records=selected, support_intersections=intersections,
        support_overlap_hours=math.fsum(hour_minutes.values()) / 60,
        source_native_unmet_zone_hours_in_intersecting_rows=total,
        exact_unmet_within_window_zone_hours=total if full_support else None,
        within_hour_unmet_is_prorated=False,
    )


def _thermal_rows(root):
    relative, expected_sha = _BINDINGS['thermal_reference']
    rows = _rows(_bound_bytes(root, relative, expected_sha))
    if len(rows) != 8760 or any(_cell(row['hour_of_year'], 'hour_of_year', 1, integer=True) != index for index, row in enumerate(rows, 1)):
        raise ValueError('Exactly ordered adopted 8760 thermal source hours required')
    for row in rows:
        for column in _THERMAL_COLUMNS.values():
            _cell(row[column], column, 0)
    return rows


def _reference(rows, start, end):
    weights = []
    if start < end:
        for hour in range(math.floor(start / 60) + 1, math.ceil(end / 60) + 1):
            weight = (min(end, hour * 60) - max(start, (hour - 1) * 60)) / 60
            if weight > 0:
                weights.append((hour, weight))
    return {name: math.fsum(float(rows[hour - 1][column]) * weight for hour, weight in weights)
            for name, column in _THERMAL_COLUMNS.items()}


def window_scope(root, scenario, observed_until_minute=None):
    """Evaluate planned and optional observed-prefix source support offline.

    ``start_hour`` is 1-based; ``observed_until_minute`` is elapsed scenario
    time in [0, duration_steps * step_minutes]. None means no observation has
    been supplied. Windows are half-open in absolute standard-year minutes.
    Local kWh are integrations of the normal source hourly reference, never
    additions to the actual central IES ledger or outage-service guarantees.
    """
    if not isinstance(scenario, dict):
        raise ValueError('Complete scenario mapping required')
    try:
        hour = _finite(scenario['start_hour'], 'start_hour', 1, integer=True)
        steps = _finite(scenario['duration_steps'], 'duration_steps', 1, integer=True)
        step = _finite(scenario['step_minutes'], 'step_minutes', 0)
    except KeyError as exc:
        raise ValueError('Complete scenario time support required') from exc
    duration = steps * step
    start = (hour - 1) * 60
    end = start + duration
    if step <= 0 or not math.isfinite(duration) or hour > 8760 or end > 8760 * 60:
        raise ValueError('Scenario outside finite 8760 source support')
    observed = None
    if observed_until_minute is not None:
        observed = _finite(observed_until_minute, 'observed_until_minute', 0)
        if observed > duration:
            raise ValueError('Observed prefix exceeds planned duration')

    quality = load_quality(root)
    thermal = _thermal_rows(root)
    planned_support = _support(start, end, status='planned')
    observed_support = None if observed is None else _support(
        start, start + observed, status='complete' if observed == duration else 'prefix')
    return dict(
        schema='hospital_ies.v29.offline_source_scope.v1',
        evaluation_only=True, controller_input=False,
        time_support=dict(coordinate='absolute standard-year minutes; start_hour is 1-based',
            interval='[start,end)', observed_until_minute_is_scenario_elapsed=True,
            planned=planned_support, observed=observed_support),
        source_quality=dict(source_mode='delivery_proxy',
            full_target_demand_qualified=False,
            humidity_hydraulics_cleanliness_qualified=False,
            local_source_unmet_is_whole_hospital_outage=False,
            source_hours_deleted=False, empirical_cooling_scale_applied=False,
            screened_zone_count=quality['screened_zone_count'],
            annual_native_unmet_zone_hours=quality['native_unmet_zone_hours'],
            annual_zone_hour_record_count=quality['zone_hour_record_count'],
            annual_unique_hour_count=quality['unique_hour_count'],
            metadata_binding=dict(path=_METADATA_PATH, sha256=_METADATA_SHA),
            planned=_intersection(quality['zone_hour_records'], start, end),
            observed=None if observed is None else _intersection(quality['zone_hour_records'], start, start + observed)),
        energy_service_boundary=dict(
            central_IES_actual='separate actual ledger',
            source_reference_is_actual_IES_supply=False,
            local_dynamic_service_assurance=False,
            upstream_preheat_central_assurance=False,
            thermal_coupling=None,
            reference_binding=quality['metadata']['thermal_reference'].copy(),
            reference_integration='normal source hourly mean kW * half-open overlap minutes / 60; not fault actual supply',
            planned_source_reference=_reference(thermal, start, end),
            observed_source_reference=None if observed is None else _reference(thermal, start, start + observed),
            unknown_upstream_energy_kWh=None, unknown_upstream_cost=None,
            unknown_upstream_CO2_kg=None,
            additional_booster_electric_charge_kWh=0.0,
            additional_charge_reason='already_in_direct_reference_no_duplication',
        ),
    )
