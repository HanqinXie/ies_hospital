"""Current supply-path accounting and finite dedicated-DC qualifications.

These functions do not solve, dispatch, debit stores, or advance a plant. A
dedicated-DC request is distinct from a physical start. Actual failed starts
also spend a qualification; cancellation before starting does not. There is
deliberately no recharge/reset operation within an episode.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import math
from typing import Mapping

RT_TO_KW = 3.51685284
BOILER_GT_AC = 'BOILER_GT_START_AC'
LEGACY_BOILER_GT_AC = 'STATION_AC'


def _nonnegative(value, name):
    if isinstance(value, bool) or not math.isfinite(float(value)) or float(value) < 0.:
        raise ValueError('Invalid nonnegative ' + name)
    return float(value)


def boiler_gt_ac_available(availability: Mapping[str, float]) -> bool:
    """The old station name is a boiler/GT-starter branch, not the whole plant.

    Conflicting aliases are rejected rather than granting a repaired path.
    Fractional hardware availability does not qualify this binary branch.
    """
    values = [float(availability[key]) for key in (BOILER_GT_AC, LEGACY_BOILER_GT_AC)
              if key in availability]
    if any(not math.isfinite(v) or not 0. <= v <= 1. for v in values):
        raise ValueError('Invalid boiler/GT AC availability')
    if len(values) == 2 and values[0] != values[1]:
        raise ValueError('conflicting boiler/GT AC branch aliases')
    return not values or values[0] >= 1. - 1e-9


def audit_supply(action, plant, *, building_delivered_electric_kW, tolerance_kW=1e-4):
    """Independently audit an actual settled action, never a planned permission.

    The solver's UPS cap is actual P0/P1 building electricity. Together with
    the complete electric balance it excludes every plant port, including EC
    and ABS, from UPS supply even though the hub uses aggregated electricity.
    Building fan/distribution-pump electricity remains in building delivery.
    """
    def value(key):
        return _nonnegative(action.get(key, 0.), key)
    source = sum(_nonnegative(v, 'GT electric') for v in action.get('gt_electric_kW', ()))
    source += value('grid_import_kW') + value('diesel_kW')
    ec = sum(_nonnegative(v, 'EC cooling') for v in action.get('electric_chiller_cooling_kW', ()))
    ec *= float(plant.ec_electric_kW_per_rt) / RT_TO_KW
    absorption = value('absorption_cooling_kW') * float(plant.abs_electric_kW_per_rt) / RT_TO_KW
    ports = dict(EC_main_and_all_in_auxiliary_kW=ec, ABS_and_tower_aggregate_auxiliary_kW=absorption)
    for key in ('boiler_auxiliary_kW', 'gt_start_electric_kW', 'boiler_restart_electric_kW',
                'cold_storage_pump_kW', 'recovery_pump_electric_kW', 'recovery_fan_electric_kW'):
        ports[key] = value(key)
    plant_electric = sum(ports.values())
    building = _nonnegative(building_delivered_electric_kW, 'building delivery')
    residual = source + value('ups_discharge_kW') - building - plant_electric - value('ups_charge_kW')
    deficit = max(0., plant_electric + value('ups_charge_kW') - source)
    tolerance = _nonnegative(tolerance_kW, 'audit tolerance')
    return dict(non_UPS_source_kW=source, plant_electric_kW=plant_electric,
        plant_ports_kW=ports, non_UPS_plant_deficit_kW=deficit,
        plant_source_eligible=deficit <= tolerance, electric_balance_residual_kW=residual,
        electric_balance_valid=abs(residual) <= tolerance,
        building_auxiliaries_in_building_delivery=True,
        qualification='aggregate source allocation proof; not a detailed switchboard model')


@dataclass(frozen=True)
class DedicatedDCState:
    """Finite functional rights, not an Ah/kWh/CCA starter-battery model.

    ``minute`` is episode time; the 1440-minute deadline never moves after
    hardware restoration. ``start_token`` is a unique actual start identity,
    normally the existing recorded diesel_start_minute plus an episode ID.
    Returning new states makes candidate probes harmless until assigned.
    """
    autonomy_minutes: float = 1440.
    qualified_starts: int = 2
    starts_used: int = 0
    pending: bool = False
    observed_until_minute: float = 0.
    committed_start_tokens: tuple[str, ...] = ()

    def __post_init__(self):
        _nonnegative(self.autonomy_minutes, 'DC autonomy')
        _nonnegative(self.observed_until_minute, 'DC observation time')
        for name in ('qualified_starts', 'starts_used'):
            number = getattr(self, name)
            if isinstance(number, bool) or not isinstance(number, int) or number < 0:
                raise ValueError('Invalid integer ' + name)
        if self.starts_used > self.qualified_starts or self.starts_used != len(self.committed_start_tokens):
            raise ValueError('Inconsistent finite DC start ledger')
        if len(set(self.committed_start_tokens)) != len(self.committed_start_tokens):
            raise ValueError('Duplicate finite DC start identities')

    def available(self, minute, *, hardware_available=True):
        minute = _nonnegative(minute, 'DC query time')
        return bool(hardware_available and minute < self.autonomy_minutes
                    and self.starts_used < self.qualified_starts)

    def request(self, requested):
        if type(requested) is not bool:
            raise ValueError('DC request must be boolean')
        return replace(self, pending=requested)

    def commit(self, minute, *, hardware_available, start_token):
        """Record an actual attempted start, including an unsuccessful one.

        Re-observation of the same committed event is idempotent. A queued or
        cancelled request must never call this method as an actual start.
        """
        if not isinstance(start_token, str) or not start_token:
            raise ValueError('Actual start requires a nonempty identity')
        if start_token in self.committed_start_tokens:
            return self
        minute = _nonnegative(minute, 'actual DC start time')
        if minute < self.observed_until_minute:
            raise ValueError('Actual DC start cannot precede settled time')
        if not self.pending:
            raise ValueError('Actual start has no pending request')
        if not self.available(minute, hardware_available=hardware_available):
            raise ValueError('Actual DC start is unqualified')
        return replace(self, starts_used=self.starts_used + 1, pending=False,
            observed_until_minute=minute, committed_start_tokens=self.committed_start_tokens + (start_token,))

    def advance(self, minute):
        """Advance observed time only; hardware recovery never adds rights."""
        minute = _nonnegative(minute, 'DC observation time')
        if minute < self.observed_until_minute:
            raise ValueError('DC observation cannot move backwards')
        return replace(self, observed_until_minute=minute)

    def observe(self):
        return dict(diesel_starts_used=self.starts_used,
            diesel_starts_remaining=self.qualified_starts - self.starts_used,
            control_autonomy_minutes=self.autonomy_minutes,
            pending_start_request=self.pending,
            observed_until_minute=self.observed_until_minute,
            qualification_is_research_assumption=True,
            recharge_model_present=False)
