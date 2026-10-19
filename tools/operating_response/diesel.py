"""Finite aggregate diesel shaft/rotor model with an explicit research domain.

Pe is an externally supplied, already connected electrical load. This component
has no hospital environment, dispatch solver, permissions or future event access.
The rotor stores Pm-Pe-loss in kW*s; it is not an additional electrical battery.
H/tau are transferred research parameters, not an identified hospital OEM model.
See the v15 design and DIESEL_SOURCE_AND_CONTRACT_REVIEW.md for source limits.
"""
from __future__ import annotations

import bisect
import copy
import math


DEFAULT_DIESEL_CONFIG = dict(
    inertia_H_seconds=.7359,
    power_factor_base=.8,
    governor_tau_seconds=.059,
    governor_delay_seconds=.05,
    speed_deadband_pu=.001,
    speed_min_pu=.9,
    speed_max_pu=1.1,
    shaft_loss_fraction=.02,
    speed_regulation_pu=.05,
    integration_max_step_seconds=.01,
)
MODEL_CONTRACT_ID = "hospital_ies.v15.finite_diesel_rotor.v1"
PARAMETER_VALIDITY = (
    "Research aggregate transfer: H/tau from Shah et al. arXiv:2204.13894 "
    "Table I; PF base, delay, deadband, speed domain, shaft loss and feedback "
    "scale are registered research assumptions, not hospital OEM settings."
)


def _number(value, name, *, nonnegative=True):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or (nonnegative and value < 0.)):
        raise ValueError(f"{name} must be finite and nonnegative")
    return float(value)


def validate_diesel_config(config=None):
    if config is None:
        config = {}
    if not isinstance(config, dict) or set(config) - set(DEFAULT_DIESEL_CONFIG):
        raise ValueError("Unknown finite diesel parameter")
    result = dict(DEFAULT_DIESEL_CONFIG, **copy.deepcopy(config))
    result = {k: _number(v, k) for k, v in result.items()}
    for key in ("inertia_H_seconds", "power_factor_base", "governor_tau_seconds",
                "speed_regulation_pu", "integration_max_step_seconds"):
        if result[key] <= 0.:
            raise ValueError(f"{key} must be positive")
    if result["power_factor_base"] > 1.:
        raise ValueError("Power-factor base cannot exceed one")
    if not 0. < result["speed_min_pu"] < 1. < result["speed_max_pu"]:
        raise ValueError("Research speed domain must contain nominal speed")
    if result["speed_deadband_pu"] >= min(1. - result["speed_min_pu"],
                                             result["speed_max_pu"] - 1.):
        raise ValueError("Speed deadband must be inside research domain")
    if 0. < result["governor_delay_seconds"] < 1e-6:
        raise ValueError("Positive governor delay below resolved microsecond domain")
    return result


class DieselRotor:
    """Deepcopy-safe, finite past-only governor and mechanical-work ledger.

    Defaults start OFF at rest: no free nominal inertia. spin_up() consumes a
    declared finite shaft-work profile. The owning transition must debit its
    actual remaining budget and preserve the original 15-minute ready/ATS rule.
    """

    def __init__(self, rated_kW=900., config=None):
        self.rated_kW = _number(rated_kW, "rated_kW")
        if self.rated_kW <= 0.:
            raise ValueError("rated_kW must be positive")
        self.config = validate_diesel_config(config)
        self.apparent_base_kVA = self.rated_kW / self.config["power_factor_base"]
        self.nominal_rotor_energy_kWs = (
            self.config["inertia_H_seconds"] * self.apparent_base_kVA)
        self.rotor_energy_kWs = 0.
        self.mechanical_kW = 0.
        self.elapsed_seconds = 0.
        self.mechanical_work_kWh = 0.
        self.electrical_work_kWh = 0.
        self.loss_work_kWh = 0.
        self._history = [(-self.config["governor_delay_seconds"], 0.), (0., 0.)]

    @property
    def speed_pu(self):
        return math.sqrt(max(0., self.rotor_energy_kWs)
                         / self.nominal_rotor_energy_kWs)

    @property
    def nominal_loss_kW(self):
        return self.rated_kW * self.config["shaft_loss_fraction"]

    def as_dict(self):
        return dict(contract_id=MODEL_CONTRACT_ID, rated_kW=self.rated_kW,
                    apparent_base_kVA=self.apparent_base_kVA,
                    nominal_rotor_energy_kWs=self.nominal_rotor_energy_kWs,
                    rotor_energy_kWs=self.rotor_energy_kWs, speed_pu=self.speed_pu,
                    mechanical_kW=self.mechanical_kW,
                    elapsed_seconds=self.elapsed_seconds,
                    mechanical_work_kWh=self.mechanical_work_kWh,
                    electrical_work_kWh=self.electrical_work_kWh,
                    loss_work_kWh=self.loss_work_kWh,
                    config=copy.deepcopy(self.config),
                    delayed_governor_history=[list(row) for row in self._history],
                    parameter_validity=PARAMETER_VALIDITY,
                    work_budget_basis="shaft mechanical electric-equivalent kWh; not liters/kg",
                    electrical_scope="externally verified connected load, no new permissions")

    def _loss(self, energy):
        return self.nominal_loss_kW * max(0., energy) / self.nominal_rotor_energy_kWs

    def _target(self, energy, electrical, active):
        if not active:
            return 0.
        error = math.sqrt(max(0., energy) / self.nominal_rotor_energy_kWs) - 1.
        deadband = self.config["speed_deadband_pu"]
        feedback = math.copysign(max(0., abs(error) - deadband), error)
        target = electrical + self.nominal_loss_kW - (
            self.rated_kW / self.config["speed_regulation_pu"]) * feedback
        # Shaft headroom covers declared friction only; Pe never exceeds rated.
        return min(self.rated_kW + self.nominal_loss_kW, max(0., target))

    def _record_target(self, target):
        now = self.elapsed_seconds
        if self._history and self._history[-1] == (now, target):
            return
        # Equal-time distinct records preserve a real input discontinuity. A
        # new Pe must not be interpolated backwards into pre-event history.
        self._history.append((now, target))
        cutoff = now - self.config["governor_delay_seconds"]
        times = [item[0] for item in self._history]
        first = max(0, bisect.bisect_right(times, cutoff) - 1)
        self._history = self._history[first:]

    def _past_target(self, time):
        times = [item[0] for item in self._history]
        index = bisect.bisect_right(times, time) - 1
        if index < 0:
            return self._history[0][1]
        if index >= len(self._history) - 1:
            return self._history[-1][1]
        left_t, left = self._history[index]
        right_t, right = self._history[index + 1]
        fraction = (time - left_t) / (right_t - left_t)
        return left + fraction * (right - left)

    def _rk4(self, seconds, electrical, active):
        energy, mechanical = self.rotor_energy_kWs, self.mechanical_kW
        time = self.elapsed_seconds
        delay = self.config["governor_delay_seconds"]
        tau = self.config["governor_tau_seconds"]

        def derivative(at, e, pm):
            target = (self._past_target(at - delay) if delay > 0.
                      else self._target(e, electrical, active))
            loss = self._loss(e)
            return pm - electrical - loss, (target - pm) / tau, pm, loss

        a = derivative(time, energy, mechanical)
        b = derivative(time + seconds / 2., energy + seconds * a[0] / 2.,
                       mechanical + seconds * a[1] / 2.)
        c = derivative(time + seconds / 2., energy + seconds * b[0] / 2.,
                       mechanical + seconds * b[1] / 2.)
        d = derivative(time + seconds, energy + seconds * c[0],
                       mechanical + seconds * c[1])
        increment = [seconds * (a[j] + 2. * b[j] + 2. * c[j] + d[j]) / 6.
                     for j in range(4)]
        return energy + increment[0], mechanical + increment[1], increment[2], increment[3]

    def _constant_target_tail(self, seconds, electrical, active):
        """Exact solution of the same linear ODE while governor target is fixed.

        This is a speed optimization, not a fresh dispatch or a state reset.
        Check the complete analytic path (including its one possible extremum)
        stays in the regulator deadband before accepting a long active tail.
        """
        target = self._target(self.rotor_energy_kWs, electrical, active)
        if any(abs(item[1] - target) > 1e-10 for item in self._history):
            return None
        if active and abs(self.speed_pu - 1.) > self.config["speed_deadband_pu"]:
            return None
        e0, pm0 = self.rotor_energy_kWs, self.mechanical_kW
        a = self.nominal_loss_kW / self.nominal_rotor_energy_kWs
        b = 1. / self.config["governor_tau_seconds"]
        difference = pm0 - target

        def at(t):
            decay_b = math.exp(-b * t)
            pm = target + difference * decay_b
            wm = target * t + difference / b * (-math.expm1(-b * t))
            if a == 0.:
                e = e0 + wm - electrical * t
            elif abs(a - b) < 1e-10:
                equilibrium = (target - electrical) / a
                e = equilibrium + (e0 - equilibrium) * math.exp(-a * t)
                e += difference * t * math.exp(-a * t)
            else:
                equilibrium = (target - electrical) / a
                e = equilibrium + (e0 - equilibrium) * math.exp(-a * t)
                e += difference * (decay_b - math.exp(-a * t)) / (a - b)
            # Zero declared loss is exactly zero, not a cancellation residue.
            loss = 0. if a == 0. else wm - electrical * t - (e - e0)
            return e, pm, wm, loss

        points = [0., seconds]
        if a == 0.:
            if difference != 0.:
                ratio = -(target - electrical) / difference
                if 0. < ratio < 1.:
                    points.append(-math.log(ratio) / b)
        elif abs(a - b) > 1e-10:
            equilibrium = (target - electrical) / a
            coeff_a = -a * (e0 - equilibrium - difference / (a - b))
            coeff_b = -b * difference / (a - b)
            if coeff_a * coeff_b < 0.:
                extremum = math.log(-coeff_b / coeff_a) / (b - a)
                if 0. < extremum < seconds:
                    points.append(extremum)
        else:
            # Equal poles are uncommon; retain the resolved integration path.
            return None
        energies = [at(t)[0] for t in points]
        if min(energies) < 0.:
            return None
        speeds = [math.sqrt(e / self.nominal_rotor_energy_kWs) for e in energies]
        if active and any(abs(w - 1.) > self.config["speed_deadband_pu"] for w in speeds):
            return None
        if max(speeds) > self.config["speed_max_pu"]:
            return None
        e, pm, wm, loss = at(seconds)
        return e, pm, wm, loss, min(speeds), max(speeds)

    def _receipt(self, before, requested, actual, wm, we, loss, low, high,
                 *, domain_reason=None, operation="advance"):
        delta = (self.rotor_energy_kWs - before["rotor_energy_kWs"]) / 3600.
        self.mechanical_work_kWh += wm / 3600.
        self.electrical_work_kWh += we / 3600.
        self.loss_work_kWh += loss / 3600.
        return dict(contract_id=MODEL_CONTRACT_ID, operation=operation,
                    requested_seconds=requested, actual_seconds=actual,
                    domain_exceeded=domain_reason is not None, domain_reason=domain_reason,
                    speed_min_pu=low, speed_max_pu=high,
                    mechanical_work_kWh=wm / 3600., electrical_work_kWh=we / 3600.,
                    loss_work_kWh=loss / 3600., rotor_delta_kWh=delta,
                    energy_residual_kWh=(wm - we - loss) / 3600. - delta,
                    state_before=before, state_after=self.as_dict(),
                    parameter_validity=PARAMETER_VALIDITY,
                    original_service_thresholds_changed=False)

    def advance(self, seconds, electrical_kW, *, connected=True, run_enabled=True):
        seconds = _number(seconds, "seconds")
        electrical = _number(electrical_kW, "electrical_kW")
        if type(connected) is not bool or type(run_enabled) is not bool:
            raise ValueError("Connection and run permission must be explicit booleans")
        if electrical > self.rated_kW + 1e-9:
            raise ValueError("Electrical output exceeds unchanged active rating")
        if not connected and electrical > 0.:
            raise ValueError("A disconnected breaker cannot deliver electricity")
        before = self.as_dict()
        active = connected and run_enabled
        initial_speed = self.speed_pu
        if (initial_speed > self.config["speed_max_pu"] + 1e-12
                or (active and initial_speed < self.config["speed_min_pu"] - 1e-12)):
            reason = ("speed_above_research_domain" if initial_speed > 1.
                      else "speed_below_research_domain")
            return self._receipt(before, seconds, 0., 0., 0., 0.,
                                 initial_speed, initial_speed, domain_reason=reason)
        remaining = seconds
        actual = wm = loss = 0.
        low = high = initial_speed
        reason = None
        minimum_e = (self.nominal_rotor_energy_kWs * self.config["speed_min_pu"] ** 2
                     if active else 0.)
        maximum_e = self.nominal_rotor_energy_kWs * self.config["speed_max_pu"] ** 2
        delay = self.config["governor_delay_seconds"]
        step = min(self.config["integration_max_step_seconds"],
                   self.config["governor_tau_seconds"] / 8.)
        if delay > 0.:
            step = min(step, delay)
        while remaining > 1e-12:
            self._record_target(self._target(self.rotor_energy_kWs, electrical, active))
            tail = self._constant_target_tail(remaining, electrical, active)
            if tail is not None:
                e, pm, part_wm, part_loss, tail_low, tail_high = tail
                self.rotor_energy_kWs, self.mechanical_kW = e, pm
                wm += part_wm
                loss += part_loss
                low, high = min(low, tail_low), max(high, tail_high)
                self.elapsed_seconds += remaining
                actual += remaining
                # Preserve exactly the now-constant finite past for next call.
                target = self._target(e, electrical, active)
                self._history = [(self.elapsed_seconds - delay, target),
                                 (self.elapsed_seconds, target)]
                remaining = 0.
                break
            duration = min(step, remaining)
            endpoint = self._rk4(duration, electrical, active)
            if endpoint[0] < minimum_e or endpoint[0] > maximum_e:
                upper = endpoint[0] > maximum_e
                bound = maximum_e if upper else minimum_e
                left, right = 0., duration
                for _ in range(48):
                    middle = (left + right) / 2.
                    value = self._rk4(middle, electrical, active)[0]
                    if (value > bound) == upper:
                        right = middle
                    else:
                        left = middle
                duration = right
                endpoint = self._rk4(duration, electrical, active)
                reason = ("speed_above_research_domain" if upper else
                          "speed_below_research_domain" if active else "rotor_energy_exhausted")
            self.rotor_energy_kWs, self.mechanical_kW = endpoint[:2]
            wm += endpoint[2]
            loss += endpoint[3]
            self.elapsed_seconds += duration
            actual += duration
            remaining = max(0., seconds - actual)
            low, high = min(low, self.speed_pu), max(high, self.speed_pu)
            self._record_target(self._target(self.rotor_energy_kWs, electrical, active))
            if reason is not None:
                break
        return self._receipt(before, seconds, actual, wm, electrical * actual, loss,
                             low, high, domain_reason=reason)

    def spin_up(self, seconds, start_fraction, end_fraction):
        """Charge a finite linear E profile from actual residual startup state.

        Fractions describe progress through the owner's existing startup time.
        A cancelled/restarted start begins with its real residual E, never zero
        or nominal E by assignment. There is no early electrical generation.
        """
        seconds = _number(seconds, "seconds")
        start = _number(start_fraction, "start_fraction")
        end = _number(end_fraction, "end_fraction")
        if not 0. <= start <= end <= 1.:
            raise ValueError("Startup progress must satisfy 0 <= start <= end <= 1")
        if seconds == 0. and end > start:
            raise ValueError("Finite startup cannot add rotor energy in zero seconds")
        before = self.as_dict()
        initial_e = self.rotor_energy_kWs
        fraction = ((end - start) / (1. - start)) if start < 1. else 0.
        # Existing overspeed is retained; startup cannot erase rotating energy.
        delta_e = max(0., self.nominal_rotor_energy_kWs - initial_e) * fraction
        final_e = initial_e + delta_e
        loss = self.nominal_loss_kW * seconds * (initial_e + final_e) / (
            2. * self.nominal_rotor_energy_kWs)
        self.rotor_energy_kWs = final_e
        if seconds > 0.:
            self.mechanical_kW = delta_e / seconds + self._loss(final_e)
            self.elapsed_seconds += seconds
            delay = self.config["governor_delay_seconds"]
            self._history = [(self.elapsed_seconds - delay, self.mechanical_kW),
                             (self.elapsed_seconds, self.mechanical_kW)]
        return self._receipt(before, seconds, seconds, delta_e + loss, 0., loss,
                             math.sqrt(initial_e / self.nominal_rotor_energy_kWs),
                             self.speed_pu, operation="spin_up")
