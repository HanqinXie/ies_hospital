"""Continuous standing loss with actual powers held over an interval in hours.

Integration contract: dE/dh = -lambda E + eta_c Pc - Pd/eta_d.
E is kWh, lambda is 1/h, Pc is charger input kW and Pd is useful output kW.
Only the explicit class selects this model; FiniteStore remains historical.
No capacity, efficiency, power, mode or inventory tolerance is changed.
"""
from __future__ import annotations
import math

from tools.sci_engineering.storage import FiniteStore


class ContinuousLossStore(FiniteStore):
    integration_contract = 'single_fault.continuous_loss_held_flow.v1'

    @staticmethod
    def _quantity(value, *, positive=False):
        try:
            valid = not isinstance(value, bool) and math.isfinite(value) and (value > 0. if positive else value >= 0.)
        except (TypeError, ValueError):
            valid = False
        if not valid:
            raise ValueError('Finite storage power in kW and nonnegative interval in hours required')
        return float(value)

    def _inputs(self, discharge_kW, charge_kW, dt_h, *, positive=False):
        discharge = self._quantity(discharge_kW)
        charge = self._quantity(charge_kW)
        hours = self._quantity(dt_h, positive=positive)
        if min(discharge, charge) > 1e-7:
            raise ValueError('Simultaneous storage charge/discharge')
        if max(discharge, charge) > self.config['power_kW'] + 1e-7:
            raise ValueError('Storage power exceeded')
        return discharge, charge, hours

    def flow_hours(self, dt_h):
        """Integral exp(-lambda*s) ds, with a stable zero-loss/tiny-dt limit."""
        hours = self._quantity(dt_h)
        x = self.config['loss_per_hour'] * hours
        return hours if x == 0. else hours * (-math.expm1(-x) / x)

    def project_energy(self, *, discharge_kW, charge_kW, dt_h):
        """Pure unconstrained energy preview; crossing is located before commit."""
        discharge, charge, hours = self._inputs(discharge_kW, charge_kW, dt_h)
        c = self.config
        retained = self.energy_kWh * math.exp(-c['loss_per_hour'] * hours)
        return retained + (charge * c['charge_efficiency'] - discharge / c['discharge_efficiency']) * self.flow_hours(hours)

    def limits(self, dt_h, *, charge_allowed):
        hours = self._quantity(dt_h, positive=True)
        c = self.config
        x = c['loss_per_hour'] * hours
        retained = self.energy_kWh * math.exp(-x)
        flow = self.flow_hours(hours)
        # Rate form keeps lambda*E representable even when lambda*dt
        # underflows; E*(1-exp(-lambda*dt))/phi is exactly E*lambda.
        charge_rate = ((c['capacity_kWh'] - self.energy_kWh) / flow
                       + self.energy_kWh * c['loss_per_hour']) / c['charge_efficiency']
        return dict(discharge_kW=0. if charge_allowed else min(c['power_kW'], retained * c['discharge_efficiency'] / flow),
                    charge_kW=min(c['power_kW'], max(0., charge_rate)) if charge_allowed else 0.)

    def boundary_hours(self, *, discharge_kW, charge_kW, max_hours):
        discharge, charge, hours = self._inputs(discharge_kW, charge_kW, max_hours, positive=True)
        return super().boundary_hours(discharge_kW=discharge, charge_kW=charge, max_hours=hours)

    def commit(self, *, discharge_kW, charge_kW, dt_h):
        discharge, charge, hours = self._inputs(discharge_kW, charge_kW, dt_h, positive=True)
        c = self.config
        if c['loss_per_hour'] == 0.:
            return super().commit(discharge_kW=discharge, charge_kW=charge, dt_h=hours)
        before = self.energy_kWh
        after = self.project_energy(discharge_kW=discharge, charge_kW=charge, dt_h=hours)
        if not math.isfinite(after) or after < -1e-7 or after > c['capacity_kWh'] + 1e-7:
            raise ValueError('Actual storage energy overdraft or overfill')
        x = c['loss_per_hour'] * hours
        # lambda*integral(E dh). The series avoids cancellation in dt-phi;
        # it includes the residence time of newly charged/withdrawn energy.
        if x < 1e-4:
            lost_flow_hours = hours * x * (.5 + x * (-1./6. + x * (1./24. + x * (-1./120. + x / 720.))))
        else:
            lost_flow_hours = hours - self.flow_hours(hours)
        net = charge * c['charge_efficiency'] - discharge / c['discharge_efficiency']
        standing = before * -math.expm1(-x) + net * lost_flow_hours
        charged, discharged = charge * hours, discharge * hours
        conversion = charged * (1. - c['charge_efficiency']) + discharged * (1. / c['discharge_efficiency'] - 1.)
        self.energy_kWh = min(c['capacity_kWh'], max(0., after))
        return dict(initial_kWh=before, final_kWh=self.energy_kWh, charged_input_kWh=charged,
                    discharged_kWh=discharged, standing_loss_kWh=standing,
                    conversion_loss_kWh=conversion,
                    balance_residual_kWh=before + charged - discharged - standing - conversion - self.energy_kWh)

    def observe(self):
        return dict(super().observe(), integration_contract=self.integration_contract)
