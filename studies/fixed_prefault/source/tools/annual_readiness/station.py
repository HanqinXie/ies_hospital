"""Opt-in actual-supply functional rights, without a DC energy model.

The caller proves normal-source support from completed physical intervals.
This module neither dispatches power nor credits a battery charge. The legacy
station and its boiler restart interface remain unchanged.
"""
from __future__ import annotations

import math
from numbers import Real

from tools.model_closure_v22.station import StationState


def _nonnegative(value, name):
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(name + ' must be a finite nonnegative number')
    number = float(value)
    if not math.isfinite(number) or number < 0.:
        raise ValueError(name + ' must be a finite nonnegative number')
    return 0. if number == 0. else number


def _boolean(value, name):
    if type(value) is not bool:
        raise ValueError(name + ' must be boolean')
    return value


class ContinuousStationState(StationState):
    """Finite unsupported-time and actual-start qualifications.

    ``available`` takes a current source proof explicitly. Compatibility
    ``control_available`` reads only the last settled support flag; its time
    argument never advances the state. Only ``commit_supply`` advances the
    actual supply ledger. Optional requalification is a named functional
    assumption, not a battery/charger or free daily refill model.
    """

    def __init__(self, config=None, *, warm_boiler=False, requalification_minutes=None):
        if config is not None and not isinstance(config, dict):
            raise ValueError('Station config must be a dictionary')
        for key, value in (config or {}).items():
            if key not in ('gt_control_dc_available', 'dg_start_dc_available'):
                _nonnegative(value, key)
        _boolean(warm_boiler, 'warm_boiler')
        if requalification_minutes is not None:
            requalification_minutes = _nonnegative(requalification_minutes, 'requalification_minutes')
            if requalification_minutes == 0.:
                raise ValueError('requalification_minutes must be positive')
        super().__init__(config, warm_boiler=warm_boiler)
        self.requalification_minutes = requalification_minutes
        self.unsupported_minutes = 0.
        self.normal_source_supported = False
        self.last_supply_end = 0.
        self.continuous_supported_minutes = 0.
        self.requalification_epoch = 0
        self.total_diesel_starts_used = 0
        self.claimed_actual_tokens = set()

    def available(self, *, hardware=True, normal_source_now=False):
        """Read current functional availability without advancing any ledger."""
        _boolean(hardware, 'hardware')
        _boolean(normal_source_now, 'normal_source_now')
        return bool(hardware and (normal_source_now
                    or self.unsupported_minutes < self.config['control_autonomy_minutes']))

    def control_available(self, minute, hardware=True):
        """Compatibility query using the last completed supply interval."""
        _nonnegative(minute, 'minute')
        return self.available(hardware=hardware, normal_source_now=self.normal_source_supported)

    def diesel_start_available(self, minute, hardware=True):
        _nonnegative(minute, 'minute')
        _boolean(hardware, 'hardware')
        return bool(self.config['dg_start_dc_available']
                    and self.control_available(minute, hardware)
                    and self.diesel_starts_used < self.config['dg_qualified_starts'])

    def commit_supply(self, start, end, *, normal_source_supported):
        """Settle one positive, contiguous actual interval exactly once."""
        start = _nonnegative(start, 'start')
        end = _nonnegative(end, 'end')
        _boolean(normal_source_supported, 'normal_source_supported')
        if end <= start:
            raise ValueError('Actual supply interval must have positive duration')
        if start != self.last_supply_end:
            raise ValueError('Actual supply intervals must be contiguous and settled once')

        minutes = end - start
        if normal_source_supported:
            self.continuous_supported_minutes += minutes
            debt = self.unsupported_minutes > 0. or self.diesel_starts_used > 0
            if (self.requalification_minutes is not None and debt
                    and self.continuous_supported_minutes >= self.requalification_minutes):
                self.unsupported_minutes = 0.
                self.diesel_starts_used = 0
                self.continuous_supported_minutes = 0.
                self.requalification_epoch += 1
        else:
            self.unsupported_minutes += minutes
            self.continuous_supported_minutes = 0.
        self.normal_source_supported = normal_source_supported
        self.last_supply_end = end
        return self.observe()

    def claim_diesel_start(self, minute, hardware=True, *, token=None):
        """Count a unique actual attempted start; replay is idempotent.

        A failed actual attempt still calls this method. A candidate, queued,
        or cancelled request must not. Re-observing an already recorded event
        succeeds without granting a new start, even in a later epoch.
        """
        minute = _nonnegative(minute, 'minute')
        _boolean(hardware, 'hardware')
        if token is None:
            token = 'minute:' + format(minute, '.17g')
        if not isinstance(token, str) or not token:
            raise ValueError('Actual start token must be a nonempty string')
        if token in self.claimed_actual_tokens:
            return True
        if minute < self.last_supply_end:
            raise ValueError('New actual start cannot precede settled supply time')
        if not self.diesel_start_available(minute, hardware):
            return False
        self.claimed_actual_tokens.add(token)
        self.diesel_starts_used += 1
        self.total_diesel_starts_used += 1
        # Previously settled support cannot requalify a newly spent start.
        self.continuous_supported_minutes = 0.
        return True

    def observe(self):
        value = super().observe()
        value.update(
            qualification_model='actual_normal_source_functional',
            unsupported_minutes=self.unsupported_minutes,
            control_autonomy_remaining_minutes=max(0., self.config['control_autonomy_minutes']
                                                   - self.unsupported_minutes),
            normal_source_supported=self.normal_source_supported,
            last_supply_end=self.last_supply_end,
            continuous_supported_minutes=self.continuous_supported_minutes,
            requalification_minutes=self.requalification_minutes,
            requalification_epoch=self.requalification_epoch,
            total_diesel_starts_used=self.total_diesel_starts_used,
            claimed_actual_tokens=sorted(self.claimed_actual_tokens),
            functional_requalification_only=True,
            recharge_model_present=False,
            physical_charge_energy_credited=False,
            DC_energy_kWh=None,
            DC_charge_power_kW=None)
        return value
