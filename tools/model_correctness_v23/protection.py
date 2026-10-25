"""Low-order causal functional dependency and continuous powered restart.

This is the existing boiler restart abstraction made transactional. The
default .5 kWe and 1 minute are inherited registered research assumptions.
Other equipment may use this interface only with its own already supported
restart parameters. No fault-class label, unmet-load proxy, future repair
clock, or permanent equipment damage appears in the transition.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import math


def _duration(value, name, *, positive=False):
    if isinstance(value, bool) or not math.isfinite(float(value)) or float(value) < 0.:
        raise ValueError('Invalid ' + name)
    if positive and float(value) <= 0.:
        raise ValueError('Positive actual interval required')
    return float(value)


@dataclass(frozen=True)
class RestartRequest:
    permission_arrived: bool
    source_available: bool
    flow_available: bool
    device_available: bool
    can_restart: bool
    requested_start_power_kW: float
    ready_before: bool

    @property
    def path_available(self):
        return self.source_available and self.flow_available and self.device_available


@dataclass(frozen=True)
class RestartState:
    restart_minutes: float = 1.
    restart_power_kW: float = .5
    ready: bool = False
    continuous_powered_minutes: float = 0.
    protective_latch: bool = False
    status: str = 'dispatch_stopped'

    def __post_init__(self):
        _duration(self.restart_minutes, 'restart duration')
        _duration(self.restart_power_kW, 'restart power')
        _duration(self.continuous_powered_minutes, 'continuous restart clock')
        if self.status not in ('dispatch_stopped', 'functional_unavailable', 'protective_latch',
                               'restarting', 'ready'):
            raise ValueError('Unknown functional restoration status')

    @property
    def permanent_damage(self):
        return False

    def advance(self, *, source_available=True, flow_available=True, device_available=True):
        """Observe a boundary loss without inventing an actual energy interval."""
        if source_available and flow_available and device_available:
            return self
        return replace(self, ready=False, continuous_powered_minutes=0.,
            protective_latch=bool(self.protective_latch or self.ready or self.continuous_powered_minutes > 0.),
            status='functional_unavailable')

    def request(self, *, permission_arrived, source_available=True, flow_available=True, device_available=True):
        """Propose startup demand from current observations and arrived permission."""
        values = (permission_arrived, source_available, flow_available, device_available)
        if any(type(v) is not bool for v in values):
            raise ValueError('Restoration conditions must be boolean current observations')
        can_restart = all(values) and not self.ready
        return RestartRequest(permission_arrived, source_available, flow_available, device_available,
            can_restart, self.restart_power_kW if can_restart else 0., self.ready)

    def commit(self, minutes, request, *, restart_power_supplied):
        """Settle one real interval; insufficient startup power resets the clock.

        The caller must derive ``restart_power_supplied`` from actual settled
        source-side power and must end the interval at any source/flow change.
        Startup permission alone never authorizes delivery before ``ready``.
        """
        minutes = _duration(minutes, 'actual restoration interval', positive=True)
        if not isinstance(request, RestartRequest) or request.ready_before != self.ready:
            raise ValueError('Restoration request belongs to another state snapshot')
        if type(restart_power_supplied) is not bool:
            raise ValueError('Actual startup-power settlement must be boolean')
        if not request.path_available:
            return self.advance(source_available=request.source_available,
                flow_available=request.flow_available, device_available=request.device_available)
        if not request.permission_arrived:
            return replace(self, ready=False, continuous_powered_minutes=0.,
                status='protective_latch' if self.protective_latch else 'dispatch_stopped')
        if self.ready:
            return replace(self, status='ready')
        if not restart_power_supplied:
            return replace(self, continuous_powered_minutes=0.,
                status='protective_latch' if self.protective_latch else 'functional_unavailable')
        progress = min(self.restart_minutes, self.continuous_powered_minutes + minutes)
        ready = progress >= self.restart_minutes - 1e-9
        return replace(self, ready=ready, continuous_powered_minutes=progress,
            protective_latch=False if ready else self.protective_latch,
            status='ready' if ready else 'restarting')

    def observe(self):
        return dict(boiler_ready=self.ready,
            boiler_continuous_power_minutes=self.continuous_powered_minutes,
            functional_status=self.status, protective_latch=self.protective_latch,
            permanent_equipment_damage=False,
            restart_minutes=self.restart_minutes, restart_power_kW=self.restart_power_kW)
