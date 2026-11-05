"""Legal V22 ownership and actual-power transforms over frozen V21 sources.

This is source accounting only. It grants no actuator, service, or fault-path
permissions. Original inputs and the V21 allocation are never rewritten.
"""
from __future__ import annotations

from collections.abc import Mapping
import copy
import hashlib
import math
from pathlib import Path

import numpy as np

from tools.sci_engineering.auxiliary import (
    compute_auxiliary_allocation, sha256,
)

GROUPS = ('P0', 'P1', 'P2', 'P3')
SOURCES = dict(fan='source_fans_total_kW', chw_pump='source_chw_pump_kW',
               heat_pump='source_heat_pump_kW')
CONTRACT_ID = 'hospital_ies.auxiliary_transforms.v22.v1'


def fan_path_availability(row, metadata, faults, priority):
    """Frozen physical path weights, independent of electricity attribution.

    Design columns retain the original source components before consumption
    scaling. They describe the existing dependency approximation; redistributing
    who bears fan electricity cannot reroute a failed ER/OR/ICU air system.
    """
    if priority not in GROUPS:
        raise ValueError('Unknown fan-path priority')
    def availability(name):
        value = _number(faults.get(name, 1.), name)
        if value > 1.:
            raise ValueError('Fan availability must be within [0,1]')
        return value
    remaining = availability('AUX_FAN')
    total = float(row[metadata['fan_fault_design_priority_columns'][priority]])
    for short, loop in (('OR', 'VAV_OR'), ('ER', 'VAV_ER'), ('ICU', 'VAV_ICU')):
        name = 'AUX_FAN_' + short
        if name not in faults:
            continue
        item = metadata['fan_fault_design_components'][loop]
        contribution = float(row[item['power_column']]) * item['priority_shares'][priority]
        if total > 1e-7:
            remaining -= (1.-availability(name))*contribution/total
    return max(0., remaining)


def _number(value, label, *, positive=False):
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f'{label} must be finite and nonnegative') from exc
    if isinstance(value, bool) or not math.isfinite(result) or result < 0. or (positive and result == 0.):
        raise ValueError(f'{label} must be finite and {"positive" if positive else "nonnegative"}')
    return result


def _pump_ownership(values, total, exponent):
    # Subtracting the largest log weight avoids all weights underflowing for a
    # large positive exponent. Zero source ownership always stays exactly zero.
    logs = np.full(values.shape, -np.inf)
    np.log(values, out=logs, where=values > 0.)
    largest = logs.max(axis=1)
    largest[~np.isfinite(largest)] = 0.
    weights = np.exp((logs - largest[:, None]) * exponent)
    denominator = weights.sum(axis=1)
    shares = np.divide(weights, denominator[:, None], out=np.zeros_like(weights),
                       where=denominator[:, None] > 0.)
    result = shares * total[:, None]
    # Settle the floating-point remainder on an already supported largest share.
    # This cannot assign electricity to a previously unconnected priority.
    result[np.arange(len(result)), weights.argmax(axis=1)] += total - result.sum(axis=1)
    return result


def compute_allocation(root, *, pump_weight_exponent=1., actual_power_scale=None,
                       fan_priority_shares=None):
    """Return V21-compatible annual fields plus a separate actual power delta.

    ``pump_weight_exponent`` changes only CHW/heat-pump ownership. Explicit fan
    ownership accepts a mapping of real loop names to nonnegative priority shares
    summing to one, using only that loop's original connected priorities.
    ``actual_power_scale`` independently changes real ``fan``, ``chw_pump`` and
    ``heat_pump`` power. ``actual_power_delta_e_kW`` is relative to demand that
    already contains the V21 auxiliary baseline. From raw group loads, install
    actual fan/CHW power once and add the new heat-pump total once instead.

    The new ``allocation_sha256`` hashes UTF-8 CSV bytes produced with
    ``frame.to_csv(index=False, lineterminator='\\n')``. Original source digests
    remain in metadata, with the old allocation digest separately named.
    """
    exponent = _number(pump_weight_exponent, 'pump_weight_exponent', positive=True)
    if actual_power_scale is not None and not isinstance(actual_power_scale, Mapping):
        raise ValueError('actual_power_scale must map known equipment kinds')
    requested_scales = {} if actual_power_scale is None else dict(actual_power_scale)
    if set(requested_scales) - set(SOURCES):
        raise ValueError('Unknown actual_power_scale equipment kind')
    scales = {kind: _number(requested_scales.get(kind, 1.), f'{kind} actual power scale')
              for kind in SOURCES}
    if fan_priority_shares is not None and not isinstance(fan_priority_shares, Mapping):
        raise ValueError('fan_priority_shares must map real fan loops')
    overrides = {} if fan_priority_shares is None else dict(fan_priority_shares)

    root = Path(root).resolve()
    baseline = compute_auxiliary_allocation(root, critical_share_scale=1.)
    out = baseline.copy(deep=True)
    meta = copy.deepcopy(baseline.attrs['metadata'])
    components = meta['fan_component_columns']
    # Preserve both frozen design weights and source power before any mutable
    # ownership or consumption transform. Rows carry only these few columns;
    # no annual numeric arrays are copied into metadata or controller context.
    fault_components = copy.deepcopy(components)
    for item in fault_components.values():
        original_column = item['power_column']
        item['power_column'] = 'm22_design_' + original_column
        out[item['power_column']] = baseline[original_column]
    design_priority_columns = {}
    for group in GROUPS:
        design_priority_columns[group] = 'm22_design_' + group + '_fan_kW'
        out[design_priority_columns[group]] = baseline[group + '_fan_kW']
    connected = {loop: [g for g in GROUPS if item['priority_shares'][g] > 0.]
                 for loop, item in components.items()}
    if set(overrides) - set(components):
        raise ValueError('Unknown fan loop in priority shares')

    validated = {}
    for loop, requested in overrides.items():
        if not isinstance(requested, Mapping) or set(requested) - set(GROUPS):
            raise ValueError('Fan priority shares must use P0/P1/P2/P3')
        shares = {g: _number(requested.get(g, 0.), f'{loop} fan share') for g in GROUPS}
        if not math.isclose(sum(shares.values()), 1., rel_tol=0., abs_tol=1e-12):
            raise ValueError('Fan priority shares must sum to one; no clipping or normalization')
        support = components[loop]['priority_shares']
        if any(shares[g] > 0. and support[g] == 0. for g in GROUPS):
            raise ValueError('Fan share assigned to a priority not connected to this loop')
        validated[loop] = shares

    if exponent != 1.:
        for kind in ('chw_pump', 'heat_pump'):
            columns = [f'{g}_{kind}_kW' for g in GROUPS]
            out[columns] = _pump_ownership(baseline[columns].to_numpy(dtype=float),
                baseline[SOURCES[kind]].to_numpy(dtype=float), exponent)
    for loop, shares in validated.items():
        component = components[loop]
        component['priority_shares'] = shares
        for item in meta['fan_mapping']:
            if item['airloop'] == loop:
                item['priority_shares'] = dict(shares)
    if validated:
        # Rebuild from nonnegative component terms instead of subtracting from
        # rounded aggregate CSV values. A legitimately zero owner stays zero.
        for group in GROUPS:
            out[f'{group}_fan_kW'] = sum(
                baseline[item['power_column']] * item['priority_shares'][group]
                for item in components.values())

    delta = np.zeros(len(out))
    for kind, source in SOURCES.items():
        scale = scales[kind]
        delta += baseline[source].to_numpy(dtype=float) * (scale - 1.)
        if scale != 1.:
            for column in [f'{g}_{kind}_kW' for g in GROUPS] + [source]:
                out[column] *= scale
            if kind == 'fan':
                for component in components.values():
                    out[component['power_column']] *= scale
    if scales['heat_pump'] != 1.:
        out['auxiliary_added_e_kW'] *= scales['heat_pump']
    out['actual_power_delta_e_kW'] = delta

    for kind, source in SOURCES.items():
        values = out[[f'{g}_{kind}_kW' for g in GROUPS]].to_numpy(dtype=float)
        if not np.isfinite(values).all() or (values < 0.).any():
            raise ValueError('Auxiliary transform produced invalid power')
        if not np.allclose(values.sum(axis=1), out[source], rtol=0., atol=1e-10):
            raise ValueError('Auxiliary ownership does not conserve actual hourly power')
    meta.update(v22_contract_id=CONTRACT_ID, pump_weight_exponent=exponent,
        actual_power_scale=scales, fan_priority_shares=validated,
        fan_connected_priorities=connected,
        fan_fault_design_components=fault_components,
        fan_fault_design_priority_columns=design_priority_columns,
        baseline_allocation_sha256=meta['allocation_sha256'],
        source_metadata_sha256=sha256(root / 'inputs/sci_engineering/auxiliary_metadata.json'),
        actual_power_delta_annual_kWh=float(delta.sum()),
        ownership_changes_total_power=False,
        actual_power_scaling_changes_total_power=True,
        pump_allocation_basis='Positive baseline ownership weights raised to exponent and normalized per hour; original zero support retained',
        fan_annual_kWh=float(out.source_fans_total_kW.sum()),
        chw_pump_annual_kWh=float(out.source_chw_pump_kW.sum()),
        added_heat_pump_annual_kWh=float(out.source_heat_pump_kW.sum()),
        fan_meter_residual_scope='Original frozen source-meter reconciliation before actual-power scaling',
        allocation_hash_serialization='pandas CSV, index=False, UTF-8 without BOM, LF newline')
    meta['allocation_sha256'] = hashlib.sha256(
        out.to_csv(index=False, lineterminator='\n').encode('utf-8')).hexdigest()
    out.attrs['metadata'] = meta
    return out
