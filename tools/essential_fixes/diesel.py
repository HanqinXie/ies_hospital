"""M15 rotor with an explicit, irreversible fuel-exhaustion shutdown.

The new minute-scale research assumption opens the electrical breaker and
cuts fuel torque at the resolved budget boundary. Mechanical power Pm is a
power state, not a separate stored-energy reservoir: cutting it removes no
rotor energy. The existing rotor energy is retained and dissipates through
the already declared shaft loss. This is not an OEM shutdown transient.

This component owns no hospital fuel budget, event queue or permissions. The
owning transition must locate and debit the exact valid powered prefix, call
exhaust_fuel(), and record any numerical budget settlement separately.
"""
from __future__ import annotations

import math

from tools.operating_response.diesel import (
    DieselRotor as M15DieselRotor,
    MODEL_CONTRACT_ID as PARENT_CONTRACT_ID,
    _number,
)


MODEL_CONTRACT_ID = "hospital_ies.v16.fuel_exhaustion_continuation.v1"
SHUTDOWN_ASSUMPTION = (
    "Minute-scale research shutdown: immediate breaker opening and fuel torque "
    "cut at the resolved shaft-work budget boundary; Pm is power, not stored "
    "energy. Retain rotor energy and dissipate it only through declared shaft "
    "loss. No refueling or restart is modeled; not an OEM shutdown transient."
)


class DieselRotor(M15DieselRotor):
    """Preserve M15 while fueled; coast irreversibly after exhaust_fuel()."""

    def __init__(self, rated_kW=900., config=None):
        self.fuel_exhausted = False
        super().__init__(rated_kW=rated_kW, config=config)

    def as_dict(self):
        value = super().as_dict()
        value.update(contract_id=MODEL_CONTRACT_ID,
                     parent_contract_id=PARENT_CONTRACT_ID,
                     fuel_exhausted=self.fuel_exhausted,
                     shutdown_assumption=SHUTDOWN_ASSUMPTION)
        return value

    def _receipt(self, *args, **kwargs):
        value = super()._receipt(*args, **kwargs)
        value.update(contract_id=MODEL_CONTRACT_ID,
                     parent_contract_id=PARENT_CONTRACT_ID,
                     fuel_exhausted=self.fuel_exhausted,
                     shutdown_assumption=SHUTDOWN_ASSUMPTION)
        return value

    def _clear_torque_history(self):
        delay = self.config["governor_delay_seconds"]
        self._history = [(self.elapsed_seconds - delay, 0.),
                         (self.elapsed_seconds, 0.)]

    def exhaust_fuel(self):
        """Cut torque and open the breaker without changing energy or time.

        Repeated calls are idempotent. The receipt follows the ordinary rotor
        work ledger and reports no budget debit; the caller owns that budget.
        """
        before = self.as_dict()
        already = self.fuel_exhausted
        self.fuel_exhausted = True
        self.mechanical_kW = 0.
        self._clear_torque_history()
        value = self._receipt(before, 0., 0., 0., 0., 0.,
                              self.speed_pu, self.speed_pu,
                              operation="fuel_exhaustion")
        value.update(already_fuel_exhausted=already,
                     mechanical_kW_before_cut=before["mechanical_kW"],
                     actual_connected=False,
                     fuel_budget_debit_kWh_equivalent=0.)
        return value

    def _exhausted_coast(self, seconds, *, operation, request_rejected=False):
        before = self.as_dict()
        initial_energy = self.rotor_energy_kWs
        decay_rate = self.nominal_loss_kW / self.nominal_rotor_energy_kWs
        self.rotor_energy_kWs = initial_energy * math.exp(-decay_rate * seconds)
        loss = initial_energy - self.rotor_energy_kWs
        self.mechanical_kW = 0.
        self.elapsed_seconds += seconds
        self._clear_torque_history()
        value = self._receipt(before, seconds, seconds, 0., 0., loss,
                              self.speed_pu, before["speed_pu"],
                              operation=operation)
        value.update(actual_connected=False, fuel_budget_debit_kWh_equivalent=0.)
        if request_rejected:
            value["request_rejected_reason"] = "fuel_exhausted"
        return value

    def advance(self, seconds, electrical_kW, *, connected=True, run_enabled=True):
        if not self.fuel_exhausted:
            return super().advance(seconds, electrical_kW,
                                   connected=connected, run_enabled=run_enabled)
        seconds = _number(seconds, "seconds")
        electrical = _number(electrical_kW, "electrical_kW")
        if type(connected) is not bool or type(run_enabled) is not bool:
            raise ValueError("Connection and run permission must be explicit booleans")
        if electrical > self.rated_kW + 1e-9:
            raise ValueError("Electrical output exceeds unchanged active rating")
        value = self._exhausted_coast(
            seconds, operation="fuel_exhausted_coast",
            request_rejected=electrical > 0. or (connected and run_enabled))
        value.update(requested_electrical_kW=electrical,
                     requested_connected=connected, requested_run_enabled=run_enabled)
        return value

    def spin_up(self, seconds, start_fraction, end_fraction):
        if not self.fuel_exhausted:
            return super().spin_up(seconds, start_fraction, end_fraction)
        seconds = _number(seconds, "seconds")
        start = _number(start_fraction, "start_fraction")
        end = _number(end_fraction, "end_fraction")
        if not 0. <= start <= end <= 1.:
            raise ValueError("Startup progress must satisfy 0 <= start <= end <= 1")
        if seconds == 0. and end > start:
            raise ValueError("Finite startup cannot add rotor energy in zero seconds")
        value = self._exhausted_coast(
            seconds, operation="fuel_exhausted_startup_rejected", request_rejected=True)
        value.update(requested_start_fraction=start, requested_end_fraction=end)
        return value
