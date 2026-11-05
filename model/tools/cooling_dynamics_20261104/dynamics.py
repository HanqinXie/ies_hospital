"""First-order deliverable-capability proxies, not a calibrated thermal model.

The input reference is an already legal *desired actual output*, not a permit
ceiling. Trial calls never warm equipment. A physical trip clips immediately;
only increases/recovery are delayed. Existing performance coefficients account
for actual production. No unmeasured startup energy, water volume or OEM timer
is inferred by this module.
"""
from copy import deepcopy
import math


DEVICES = ('ABS', 'EC_1', 'EC_2', 'EC_3')
MODEL_ID = 'cooling_capability_first_order_engineering_20261104'


def default_config():
    return {
        'model_id': MODEL_ID,
        'cooling_tau_seconds': [137.717, 120., 120., 120.],
        'tower_support_tau_seconds': 360.,
        'pump_support_tau_seconds': 60.,
        'qualification': 'Literature-supported low-order engineering proxy; not hospital/OEM calibration',
        'parameter_provenance': {
            'ABS': {
                'selected_seconds': 137.717,
                'type': 'reference_apparatus_parameter_used_as_explicit_proxy',
                'doi': '10.1016/j.enconman.2026.121191',
                'location': 'Section 5.2.1 Eq(85), Table 2; operating single-effect hot-water chiller',
                'limitation': 'Not a calibrated steam-chiller startup or repair timer'},
            'EC_1_EC_2_EC_3': {
                'selected_seconds': 120.,
                'type': 'engineering_assumption_not_reported_or_fitted_in_reference',
                'doi': '10.1016/j.ijrefrig.2008.03.006',
                'location': 'Figures 6/7 provide component-dynamics context only',
                'limitation': 'Paper does not supply a whole-machine first-order tau; no capacity-ratio scaling'},
            'tower_support': {
                'selected_seconds': 360.,
                'type': 'engineering_mapping_of_reference_water_side_temperature_response',
                'doi': '10.1016/j.jprocont.2018.04.005',
                'location': 'Table 4 water-side exchanger temperature time constant',
                'limitation': 'Normalized support preparation, not tower temperature, hydraulic transit or measured repair time'},
            'pump_support': {
                'selected_seconds': 60.,
                'type': 'engineering_mapping_of_reference_pressure_response',
                'doi': '10.1016/j.jprocont.2018.04.005',
                'location': 'Table 3 pressure dynamics',
                'limitation': 'Normalized support preparation, not calibrated pump flow or restart timer'},
        },
        'unknowns': ['OEM minimum-on/off and reset lockouts', 'startup-specific additional electricity/heat',
                     'hospital loop water volume/UA/transport delay', 'hospital dynamic calibration'],
    }


def _finite(value, label, *, positive=False):
    if isinstance(value, bool):
        raise ValueError(label + ' must be a finite number')
    try:
        out = float(value)
    except (ValueError, TypeError):
        raise ValueError(label + ' must be a finite number') from None
    if not math.isfinite(out) or out < 0. or (positive and out == 0.):
        raise ValueError(label + ' is outside its physical domain')
    return out


def _vector(values, label):
    if len(values) != 4:
        raise ValueError(label + ' must contain ABS and three electric chillers')
    return [_finite(v, label) for v in values]


def vector_availability(availability):
    return [_availability(availability, key) for key in DEVICES]


def _availability(values, key):
    val = _finite(values.get(key, 1.), key)
    if val > 1.:
        raise ValueError(key + ' availability exceeds one')
    return val


def _rising_segment(start, target, dt_seconds, tau_seconds):
    """Return the exact held-target interval mean and endpoint; cuts are immediate."""
    if target <= start:
        return target, target
    z = dt_seconds / tau_seconds
    rise = -math.expm1(-z)
    # Avoid cancellation at very short event-split intervals.
    mean_rise = (z / 2 - z*z / 6 + z*z*z / 24) if z < 1e-5 else 1 - rise / z
    delta = target - start
    return start + delta * mean_rise, start + delta * rise


class CoolingDynamics:
    def __init__(self, config=None, initial_cooling_kW=None, initial_availability=None):
        self.config = deepcopy(default_config() if config is None else config)
        if self.config.get('model_id') != MODEL_ID:
            raise ValueError('Explicit cooling dynamics model identity required')
        self._taus = [_finite(v, 'cooling tau', positive=True)
                      for v in self.config['cooling_tau_seconds']]
        if len(self._taus) != 4:
            raise ValueError('Four cooling time constants required')
        self._tower_tau = _finite(self.config['tower_support_tau_seconds'], 'tower support tau', positive=True)
        self._pump_tau = _finite(self.config['pump_support_tau_seconds'], 'pump support tau', positive=True)
        av = initial_availability or {}
        self._q = _vector([0.] * 4 if initial_cooling_kW is None else initial_cooling_kW, 'initial cooling')
        self._q = [q if a > 0. else 0. for q, a in zip(self._q, vector_availability(av))]
        self._tower_ready = 1. if self._tower_available(av) > 0. else 0.
        self._pump_ready = 1. if _availability(av, 'AUX_CHW_PUMP') > 0. else 0.
        self._elapsed = 0.
        self._delivered_kWh = [0.] * 4

    @staticmethod
    def _tower_available(availability):
        bank = sum(_availability(availability, f'TOWER_{i}') for i in range(1, 5)) / 4
        return _availability(availability, 'TOWER') * bank

    def observe(self):
        return {'model_id': MODEL_ID, 'cooling_kW': list(self._q),
                'tower_ready': self._tower_ready, 'pump_ready': self._pump_ready,
                'elapsed_minutes': self._elapsed,
                'actual_cooling_kWh_by_device': list(self._delivered_kWh)}

    def preview(self, dt_minutes, reference_kW, availability):
        dt = _finite(dt_minutes, 'dynamic interval', positive=True)
        reference = _vector(reference_kW, 'cooling reference')
        av = vector_availability(availability)
        mean, end = [], []
        for q0, target, tau, available in zip(self._q, reference, self._taus, av):
            # Reference was already clipped to instantaneous physical capacity;
            # multiplying partial availability again would derate it twice.
            pair = _rising_segment(q0, target, dt*60, tau) if available > 0. else (0., 0.)
            mean.append(pair[0]); end.append(pair[1])
        tower = (_rising_segment(self._tower_ready, 1., dt*60, self._tower_tau)
                 if self._tower_available(availability) > 0. else (0., 0.))
        pump = (_rising_segment(self._pump_ready, 1., dt*60, self._pump_tau)
                if _availability(availability, 'AUX_CHW_PUMP') > 0. else (0., 0.))
        return {'model_id': MODEL_ID, 'mean_cooling_limits_kW': mean, 'end_cooling_kW': end,
                'tower_mean_factor': tower[0], 'pump_mean_factor': pump[0],
                'tower_end_factor': tower[1], 'pump_end_factor': pump[1],
                'next_event_minutes': None, 'dt_minutes': dt,
                'reference_kW': reference, 'state_committed': False}

    def commit_actual(self, actual_cooling_kW, dt_minutes, reference_kW, availability,
                      support_running=None):
        actual = _vector(actual_cooling_kW, 'actual cooling')
        p = self.preview(dt_minutes, reference_kW, availability)
        before = self.observe()
        endpoints = []
        for value, mean, end in zip(actual, p['mean_cooling_limits_kW'], p['end_cooling_kW']):
            tolerance = 1e-6 * max(1., mean)
            if value > mean + tolerance:
                raise ValueError('Actual output exceeds dynamic attainable mean')
            # Actual resource shortfall does not prewarm an idle converter.
            ratio = min(1., value / mean) if mean > 0. else 0.
            endpoints.append(end * ratio)
        if support_running is None:
            # Standalone default: production requires both cooling supports.
            # Runtime must explicitly include pump operation for storage-only
            # cooling, and must derive flags from actual executed resources.
            producing = any(q > 0. for q in actual)
            support_running = {'tower': producing, 'pump': producing}
        if (set(support_running) != {'tower', 'pump'}
                or any(type(v) is not bool for v in support_running.values())):
            raise ValueError('Actual tower/pump running flags must be explicit booleans')
        self._q = endpoints
        self._tower_ready = p['tower_end_factor'] if support_running['tower'] else 0.
        self._pump_ready = p['pump_end_factor'] if support_running['pump'] else 0.
        self._elapsed += p['dt_minutes']
        energy = [q * p['dt_minutes'] / 60. for q in actual]
        self._delivered_kWh = [a+b for a, b in zip(self._delivered_kWh, energy)]
        return {'schema': 'cooling_dynamics.actual_commit.v1', 'model_id': MODEL_ID,
                'initial_state': before, 'final_state': self.observe(), 'preview': p,
                'actual_mean_cooling_kW': actual, 'actual_cooling_kWh': energy,
                'actual_support_running': dict(support_running),
                'commit_count': 1, 'resource_clipped_endpoint_policy': 'actual/predicted mean ratio',
                'startup_extra_energy_qualified': False, 'hospital_calibrated': False}
