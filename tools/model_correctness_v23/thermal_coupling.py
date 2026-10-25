"""Current-reference thermal energy port; no planner or physical-core mutation.

Unvalidated temperature states are diagnostic only. The plant must serve all
original service locks before optionally delivering separately metered recovery
cooling. Lost cold service is retained independently of this thermal ledger.
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import asdict
import math
from pathlib import Path

import numpy as np

from .thermal_data import allocate_priority_delivery
from .thermal_model import TwoState, commit, recovery_request_kW


def _nonnegative(name, value):
    if isinstance(value, bool) or not math.isfinite(float(value)) or float(value) < 0:
        raise ValueError(f'Invalid {name}')
    return float(value)


def load_reference_baseline(csv_by_zone, start_timestamp):
    """Keep real 10min interval powers; exclude all wall/room temperature fields.

    Native files label interval ends. The returned keys label interval starts,
    aligned to an explicitly supplied IES start timestamp. This is a prescribed
    normal-reference power profile, not access to an accident's future state.
    """
    import pandas as pd
    start = pd.Timestamp(start_timestamp)
    reference = {}
    clock = None
    for zone, path in csv_by_zone.items():
        frame = pd.read_csv(Path(path), index_col=0, parse_dates=True)
        if len(frame) < 2 or not np.all(np.diff(frame.index.asi8) == 600_000_000_000):
            raise ValueError('Reference requires real complete native ten-minute powers')
        if clock is None:
            clock = frame.index
        elif not clock.equals(frame.index):
            raise ValueError('Reference zones do not share the same native clock')
        for stamp, row in frame.iterrows():
            minute = float((stamp - start).total_seconds() / 60. - 10.)
            reference.setdefault(minute, {})[zone] = dict(heating_kW=float(row.heating_kW),
                                                         cooling_kW=float(row.cooling_kW))
    return reference


class ThermalCoupling:
    def __init__(self, reference, parameters, *, group_priority=0,
                 recovery_horizon_h=1., max_recovery_kW=100.):
        if group_priority not in range(4):
            raise ValueError('Invalid thermal priority group')
        self.group_priority = int(group_priority)
        self.horizon = float(recovery_horizon_h)
        if not math.isfinite(self.horizon) or self.horizon <= 0:
            raise ValueError('Invalid recovery horizon')
        self.maximum = _nonnegative('maximum recovery power', max_recovery_kW)
        self.multipliers = {z: float(m) for z, m in parameters['zone_multipliers'].items()}
        scales = parameters['zone_scales']
        if set(scales) != set(self.multipliers) or any(m <= 0 for m in self.multipliers.values()):
            raise ValueError('Invalid complete thermal zone map')
        self._states = {z: TwoState(0., 0., *(float(p) * float(scales[z])
                        for p in parameters['reference_parameters'])) for z in self.multipliers}
        self._reference = {}
        for minute, row in reference.items():
            if set(row) != set(self._states):
                raise ValueError('Incomplete reference zone scope')
            self._reference[float(minute)] = {
                z: {name: _nonnegative(name, row[z][name]) for name in ('heating_kW', 'cooling_kW')}
                for z in self._states}
        self._clock = sorted(self._reference)
        if len(self._clock) < 2 or not np.allclose(np.diff(self._clock), 10., rtol=0, atol=1e-9):
            raise ValueError('Reference must have native continuous ten-minute intervals')
        self._revision = 0
        self._last_end_minute = 0.

    def observe(self):
        return dict(revision=self._revision, last_end_minute=self._last_end_minute,
                    zones={z: asdict(s) for z, s in self._states.items()},
                    use_status='Exploratory energy-interface diagnostic; no critical guarantee or safety use')

    def next_reference_boundary(self, minute):
        index = bisect_right(self._clock, float(minute))
        return self._clock[index] if index < len(self._clock) else self._clock[-1] + 10.

    def prepare_current(self, row, loads, actual_available=None):
        """Pure candidate context. Calls never change mass, temperature or clocks."""
        values = row if isinstance(row, dict) else vars(row)
        if 'time_minute' not in values:
            raise ValueError('Current time_minute is required; no future temperature fallback')
        minute = _nonnegative('time_minute', values['time_minute'])
        index = bisect_right(self._clock, minute) - 1
        if index < 0 or minute >= self._clock[-1] + 10.:
            raise ValueError('Current time has no native reference power interval')
        reference = self._reference[self._clock[index]]
        demand = np.asarray(loads, dtype=float)
        if demand.shape != (4, 3) or not np.isfinite(demand).all() or np.any(demand < 0):
            raise ValueError('Expected current nonnegative four-by-three E/H/C loads')
        normal = dict(cooling_kW=float(demand[self.group_priority, 2]),
                      heating_kW=float(demand[self.group_priority, 1]))
        weights = {}
        for name in ('cooling_kW', 'heating_kW'):
            weights[name] = {z: reference[z][name] * self.multipliers[z] for z in self._states}
            other = normal[name] - sum(weights[name].values())
            if other < -1e-7:
                raise ValueError(f'Native OR reference exceeds complete priority {name} scope')
            weights[name]['OTHER'] = max(0., other)
        maximum = self.maximum if actual_available is None else min(self.maximum,
                             _nonnegative('current available recovery power', actual_available))
        requests = {z: recovery_request_kW(state, known_horizon_h=self.horizon,
                     max_power_kW=maximum / self.multipliers[z]) for z, state in self._states.items()}
        total = sum(requests[z] * self.multipliers[z] for z in requests)
        if total > maximum:
            requests = {z: q * maximum / total for z, q in requests.items()}
            total = maximum
        return dict(revision=self._revision, time_minute=minute,
                    next_reference_boundary_minute=self._clock[index] + 10.,
                    recovery_cooling_request_kW=total, zone_recovery_request_kW=requests,
                    current_reference={z: dict(v) for z, v in reference.items()},
                    normal_priority=normal, priority_weights=weights,
                    use_status='Exploratory recovery demand after original service locks only')

    def commit_actual(self, prepared, dt_h, actual_priority_cooling_kW,
                      actual_extra_recovery_kW, *, actual_priority_heating_kW=None):
        """Atomically commit only one actually executed, reference-aligned segment.

        The cold service scalar excludes recovery. If actual heat is omitted,
        the ledger explicitly assumes its normal reference is maintained.
        Caller should pass actual served priority heat whenever it is known.
        """
        if prepared['revision'] != self._revision:
            raise ValueError('Reject stale or duplicate thermal actual commit')
        if abs(prepared['time_minute'] - self._last_end_minute) > 1e-7:
            raise ValueError('Actual thermal clock must be continuous without replay or gaps')
        dt = float(dt_h)
        if not math.isfinite(dt) or dt <= 0:
            raise ValueError('Invalid actual thermal interval')
        if prepared['time_minute'] + dt * 60. > prepared['next_reference_boundary_minute'] + 1e-7:
            raise ValueError('Actual segment crosses a native reference boundary; split the segment')
        cold = _nonnegative('actual priority cold delivery', actual_priority_cooling_kW)
        extra = _nonnegative('actual separately metered recovery cooling', actual_extra_recovery_kW)
        request = prepared['recovery_cooling_request_kW']
        if extra > request + 1e-7:
            raise ValueError('Recovery delivery exceeds the current requested port')
        heat_omitted = actual_priority_heating_kW is None
        heat = (prepared['normal_priority']['heating_kW'] if heat_omitted else
                _nonnegative('actual priority heat delivery', actual_priority_heating_kW))
        multipliers = dict(self.multipliers, OTHER=1.)
        allocated_cold = allocate_priority_delivery(prepared['priority_weights']['cooling_kW'], multipliers, cold)
        allocated_heat = allocate_priority_delivery(prepared['priority_weights']['heating_kW'], multipliers, heat)
        zones = {}
        next_states = {}
        for z, state in self._states.items():
            reference = prepared['current_reference'][z]
            zone_extra = extra * prepared['zone_recovery_request_kW'][z] / request if request else 0.
            forcing = ((allocated_heat[z] - reference['heating_kW']) -
                       (allocated_cold[z] - reference['cooling_kW']))
            result = commit(state, zone_extra, dt, sensible_delta_kW=forcing)
            next_states[z] = result.state
            zones[z] = dict(state=asdict(result.state), multiplier=self.multipliers[z],
                            baseline_heating_kW=reference['heating_kW'], baseline_cooling_kW=reference['cooling_kW'],
                            main_delivered_heating_kW=allocated_heat[z], main_delivered_cooling_kW=allocated_cold[z],
                            actual_extra_recovery_kW=zone_extra, sensible_delta_kW=forcing,
                            sensible_input_kWh=result.sensible_input_kWh,
                            actual_extra_cooling_kWh=result.actual_extra_cooling_kWh,
                            passive_heat_rejection_kWh=result.passive_heat_rejection_kWh,
                            thermal_energy_change_kWh=result.thermal_energy_change_kWh,
                            balance_residual_kWh=result.balance_residual_kWh)
        # No state changes occur until every actual delivery and zone passes.
        self._states = next_states
        self._revision += 1
        self._last_end_minute = prepared['time_minute'] + dt * 60.
        total = lambda name: sum(zones[z][name] * self.multipliers[z] for z in zones)
        return dict(time_minute=prepared['time_minute'], end_minute=prepared['time_minute'] + dt * 60.,
                    revision=self._revision, zones=zones,
                    other_main_delivered_cooling_kW=allocated_cold['OTHER'],
                    other_main_delivered_heating_kW=allocated_heat['OTHER'],
                    allocated_main_cooling_kW=sum(allocated_cold[z] * multipliers[z] for z in allocated_cold),
                    allocated_extra_recovery_kW=total('actual_extra_recovery_kW'),
                    actual_extra_cooling_kWh=total('actual_extra_cooling_kWh'),
                    sensible_input_kWh=total('sensible_input_kWh'),
                    thermal_energy_change_kWh=total('thermal_energy_change_kWh'),
                    passive_heat_rejection_kWh=total('passive_heat_rejection_kWh'),
                    thermal_balance_residual_kWh=total('balance_residual_kWh'),
                    heat_delivery_boundary=('normal_reference_maintained_assumption' if heat_omitted else 'actual_priority_delivery'),
                    cold_ENS_offset_kWh=0.,
                    use_status='Exploratory recovery energy-interface diagnostic only')
