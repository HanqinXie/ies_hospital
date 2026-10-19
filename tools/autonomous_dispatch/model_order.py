"""Opt-in M1 boundary ordering and a mathematical surplus diagnostic.

M1 settles the existing interlocks after already-due commands and before the
next observation. It is a model-contract choice, not a correction to M0.
No actuator rate, delay, topology, dispatch decision or energy sink is added.
"""
from __future__ import annotations

import math

import numpy as np

from hospital_ies.state_transition import TransitionCore


class OrderedBoundaryMixin:
    """Compose before a transition core: ``class M1(OrderedBoundaryMixin, Core)``.

    The parent boundary retains its existing command/permission/capacity order.
    A same-time command already in that queue therefore precedes the interlock;
    a later-generated same-time command cannot undo a settled protection lock.
    The inherited advance-time check remains in place and cannot retrip an
    already isolated source. Environment factories must select this explicitly.
    """

    def boundary(self):
        result = super().boundary()
        _, row, loads, availability = self.data_at(self.time)
        connected = np.maximum(0.0, loads - self.state.disconnected_loads)
        self._surplus_interlock(connected, availability, row)
        return result


class OrderedTransitionCore(OrderedBoundaryMixin, TransitionCore):
    """Standalone M1 core for bounded probes or explicit environment factories."""


def finite_down_excess_energy_lower_bound_kWh(
    surplus_kW: float,
    down_rate_kW_per_hour: float,
    delay_minutes: float = 0.0,
) -> float:
    """Integrate unavoidable excess for delayed finite removal of a surplus.

    Conditional mathematical diagnostic only: after all other allowed source
    changes, the residual surplus is ``s``; net absorption stays bounded by its
    post-change level until removal completes. The remaining source cannot fall
    for ``delay_minutes`` and then falls no faster than ``r`` kW/hour. The
    rectangle plus triangle gives ``s*delay/60 + s**2/(2*r)`` kWh. This function
    neither assigns a diesel rate nor permits imbalance in a physical solve.
    """
    surplus = float(surplus_kW)
    rate = float(down_rate_kW_per_hour)
    delay = float(delay_minutes)
    if (not all(math.isfinite(v) for v in (surplus, rate, delay))
            or surplus < 0.0 or rate <= 0.0 or delay < 0.0):
        raise ValueError("Surplus/delay must be finite and nonnegative; rate must be finite and positive")
    return surplus * delay / 60.0 + 0.5 * surplus * (surplus / rate)
