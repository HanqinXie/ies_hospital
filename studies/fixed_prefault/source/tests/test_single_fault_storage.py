"""Continuous held-flow inventory contract, independently evaluated in Decimal.

The first RED execution deliberately bound STORE_CLASS to historical FiniteStore.
The assertions survive unchanged when the explicit new model is selected.
"""
import copy
from decimal import Decimal, localcontext
from pathlib import Path
import sys

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
from tools.sci_engineering.storage import FiniteStore

from tools.single_fault_model_20261102.storage import ContinuousLossStore

STORE_CLASS = ContinuousLossStore


def store(**changes):
    config = dict(capacity_kWh=100., power_kW=20., initial_soc=.4,
                  charge_efficiency=.9, discharge_efficiency=.8, loss_per_hour=.01)
    config.update(changes)
    return STORE_CLASS(config)


def oracle(initial, charge, discharge, hours, config):
    """Independent high precision solution of dE/dh=-lambda E+eta_c Pc-Pd/eta_d."""
    with localcontext() as ctx:
        ctx.prec = 65
        d = lambda value: Decimal(str(value))
        t, loss = d(hours), d(config['loss_per_hour'])
        flow = d(charge) * d(config['charge_efficiency']) - d(discharge) / d(config['discharge_efficiency'])
        if not loss:
            return float(d(initial) + flow * t)
        decay = (-loss * t).exp()
        return float(d(initial) * decay + flow * (1 - decay) / loss)


@pytest.mark.parametrize('charge,discharge', [(10., 0.), (0., 10.), (0., 0.)])
def test_held_flow_matches_independent_analytic_inventory(charge, discharge):
    s = store()
    expected = oracle(s.energy_kWh, charge, discharge, 2., s.config)
    receipt = s.commit(charge_kW=charge, discharge_kW=discharge, dt_h=2.)
    assert s.energy_kWh == pytest.approx(expected, rel=1e-13, abs=1e-12)
    assert receipt['balance_residual_kWh'] == pytest.approx(0., abs=1e-12)
    assert receipt['standing_loss_kWh'] >= 0.


@pytest.mark.parametrize('charge,discharge', [(10., 0.), (0., 10.), (0., 0.)])
def test_held_flow_semigroup_one_interval_equals_two_halves(charge, discharge):
    one, halves = store(), store()
    one.commit(charge_kW=charge, discharge_kW=discharge, dt_h=2.)
    first = halves.commit(charge_kW=charge, discharge_kW=discharge, dt_h=1.)
    second = halves.commit(charge_kW=charge, discharge_kW=discharge, dt_h=1.)
    assert one.energy_kWh == pytest.approx(halves.energy_kWh, rel=1e-13, abs=1e-12)
    expected_loss = 40. + 2. * (charge * .9 - discharge / .8) - one.energy_kWh
    assert first['standing_loss_kWh'] + second['standing_loss_kWh'] == pytest.approx(expected_loss, abs=1e-12)


def test_limits_and_first_boundaries_use_the_continuous_inventory():
    for charge_allowed in (True, False):
        s = store(initial_soc=.95 if charge_allowed else .1)
        limits = s.limits(2., charge_allowed=charge_allowed)
        charge, discharge = limits['charge_kW'], limits['discharge_kW']
        expected = oracle(s.energy_kWh, charge, discharge, 2., s.config)
        assert expected == pytest.approx(100. if charge_allowed else 0., abs=1e-12)
        assert min(charge, discharge) == 0.
        before = s.energy_kWh
        hours = s.boundary_hours(charge_kW=20. if charge_allowed else 0.,
                                 discharge_kW=0. if charge_allowed else 20., max_hours=2.)
        expected = oracle(before, 20. if charge_allowed else 0., 0. if charge_allowed else 20., hours, s.config)
        assert expected == pytest.approx(100. if charge_allowed else 0., abs=1e-12)
        assert 0. < hours < 2. and s.energy_kWh == before
        s.commit(charge_kW=20. if charge_allowed else 0., discharge_kW=0. if charge_allowed else 20., dt_h=hours)
        assert s.energy_kWh == pytest.approx(expected, abs=1e-12)


@pytest.mark.parametrize('loss,hours', [(0., 2.), (.01, 1e-12), (.01, 1e-20), (1e-200, 1e-200)])
def test_projection_and_flow_hours_are_pure_and_stable(loss, hours):
    s = store(loss_per_hour=loss)
    before = copy.deepcopy(s.__dict__)
    assert callable(getattr(s, 'flow_hours', None)), 'continuous model must expose integrated held-flow hours'
    assert callable(getattr(s, 'project_energy', None)), 'boundary and preview need the same pure inventory map'
    expected = oracle(s.energy_kWh, 10., 0., hours, s.config)
    assert s.project_energy(charge_kW=10., discharge_kW=0., dt_h=hours) == pytest.approx(expected, abs=1e-13)
    assert s.flow_hours(hours) == pytest.approx(hours, rel=1e-12, abs=0.)
    assert s.project_energy(charge_kW=10., discharge_kW=0., dt_h=0.) == s.energy_kWh
    assert s.__dict__ == before
    receipt = s.commit(charge_kW=10., discharge_kW=0., dt_h=hours)
    assert receipt['standing_loss_kWh'] >= 0.
    assert abs(receipt['balance_residual_kWh']) < 1e-12


def test_continuous_contract_and_zero_loss_degenerate_to_original_map():
    s = store(loss_per_hour=0.)
    assert getattr(s, 'integration_contract', None) == 'single_fault.continuous_loss_held_flow.v1'
    legacy = FiniteStore(s.config)
    for charge, discharge, hours in [(10., 0., .25), (0., 10., .5), (0., 0., 1.)]:
        assert s.commit(charge_kW=charge, discharge_kW=discharge, dt_h=hours) == legacy.commit(charge_kW=charge, discharge_kW=discharge, dt_h=hours)


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), -1., True])
def test_invalid_intervals_and_nonfinite_powers_rejected_without_mutation(bad):
    s = store()
    before = copy.deepcopy(s.__dict__)
    with pytest.raises(ValueError):
        s.commit(charge_kW=0., discharge_kW=0., dt_h=bad)
    with pytest.raises(ValueError):
        s.limits(bad, charge_allowed=True)
    with pytest.raises(ValueError):
        s.boundary_hours(charge_kW=0., discharge_kW=0., max_hours=bad)
    with pytest.raises(ValueError):
        s.commit(charge_kW=bad, discharge_kW=0., dt_h=1.)
    assert s.__dict__ == before


@pytest.mark.parametrize('charge,discharge,hours', [(21., 0., .1), (1., 1., .1), (20., 0., 10.), (0., 20., 10.), (0., 0., 0.)])
def test_original_power_mutual_exclusion_and_inventory_guards_survive(charge, discharge, hours):
    s = store()
    with pytest.raises(ValueError):
        s.commit(charge_kW=charge, discharge_kW=discharge, dt_h=hours)


def test_boundary_closure_resolves_changed_power_without_mutating_inventory():
    from tools.model_correctness_v23.environment import close_storage_interval
    s = store(initial_soc=.008)
    probes = []
    def solve(minutes):
        probes.append(minutes)
        return {'action': {'cold_discharge_kW': 10. + minutes * .5, 'cold_charge_kW': 0.}}
    before = copy.deepcopy(s.__dict__)
    minute, result, receipt = close_storage_interval(0., 10., solve, [('cold', s)])
    discharge = result['action']['cold_discharge_kW']
    assert oracle(s.energy_kWh, 0., discharge, minute / 60., s.config) == pytest.approx(0., abs=1e-10)
    assert receipt['probes'][-1]['projected_inventory_kWh']['cold'] == pytest.approx(0., abs=1e-10)
    assert receipt['converged'] and receipt['inventory_committed'] is False
    assert len(probes) > 2 and s.__dict__ == before


def test_reserve_context_adds_held_flow_hours_only_for_the_explicit_model():
    from test_v25_reserve import reserve_case
    from tools.model_correction_v25.reserve import solver_context, validate_solver_context
    loads = np.zeros((4, 3)); loads[0] = [20., 0., 100.]
    env, _ = reserve_case(loads)
    old = solver_context(env.core, 2.)
    keys = {'target_kWh', 'initial_energy_kWh', 'retained_energy_kWh', 'charge_efficiency', 'discharge_efficiency'}
    assert all(set(item) == keys for item in old.values())
    for name in ('ups', 'cold_store'):
        config = getattr(env.core, name).config.copy(); config['loss_per_hour'] = .01
        setattr(env.core, name, STORE_CLASS(config))
    context = solver_context(env.core, 2.)
    for name, item in context.items():
        assert set(item) == keys | {'net_flow_hours'}
        assert item['net_flow_hours'] == pytest.approx(float((Decimal(1) - Decimal('-.02').exp()) / Decimal('.01')), rel=1e-13)
    assert validate_solver_context({'_model_correction_v25_reserve': context}) == context
    bad = copy.deepcopy(context); bad['ups']['unknown_flow_time'] = 2.
    with pytest.raises(ValueError):
        validate_solver_context({'_model_correction_v25_reserve': bad})


def test_actual_reserve_solver_matrix_uses_integrated_flow_rate_and_legacy_is_unchanged(tmp_path):
    from test_v25_reserve import reserve_case, solve_case
    from hospital_ies.solver_diagnostics import LayerCollector, collect_layers
    loads = np.zeros((4, 3)); loads[0] = [20., 0., 100.]
    for model, explicit in ((FiniteStore, False), (STORE_CLASS, True)):
        env, permission = reserve_case(loads, grid=50.)
        for name in ('ups', 'cold_store'):
            old = getattr(env.core, name)
            config = old.config.copy(); config['loss_per_hour'] = .01
            new = model(config); new.energy_kWh = old.energy_kWh
            setattr(env.core, name, new)
        collector = LayerCollector()
        with collect_layers(collector):
            solve_case(env, permission, minutes=15.)
        first = collector.layers[0]
        variables = {v['name']: v['index'] for v in first['variables']}
        rate = float((Decimal(1) - Decimal('-.0025').exp()) / Decimal('.0025')) if explicit else 1.
        for name in ('ups', 'cold'):
            row = next(i for i, metadata in enumerate(first['rows']) if metadata['name'] == f'm25.{name}.uncapped_shortfall_change_rate')
            vector = first['matrix'].getrow(row).toarray().ravel()
            assert vector[variables[f'{name}_discharge_kW']] == pytest.approx(rate / .95, rel=1e-13)
            assert vector[variables[f'{name}_charge_kW']] == pytest.approx(-rate * .95, rel=1e-13)
            assert vector[variables[f'm25.{name}.reserve_shortfall_change_rate']] == -1.
        assert [r['name'] for r in first['rows'] if r['kind'] == 'priority_lock'] == []
        assert first['variables'][14]['name'] == 'P0.unserved.e'
        # Detached actual compiler input, available in the E-local pytest artifact tree.
        np.savez_compressed(tmp_path / f'{"explicit" if explicit else "legacy"}_first_matrix.npz', matrix=first['matrix'].toarray(),
                            lower=first['lower'], upper=first['upper'], objective=first['objective'],
                            bounds_lower=first['bounds_lower'], bounds_upper=first['bounds_upper'])


@pytest.mark.parametrize('loss,hours', [(.01, 5e-324), (1e-200, 1e-200)])
def test_full_inventory_charge_limit_keeps_representable_maintenance_rate_when_loss_duration_underflows(loss, hours):
    s = store(initial_soc=1., loss_per_hour=loss)
    before = copy.deepcopy(s.__dict__)
    expected = loss * s.energy_kWh / s.config['charge_efficiency']
    assert s.limits(hours, charge_allowed=True)['charge_kW'] == pytest.approx(expected, rel=1e-13, abs=0.)
    assert s.__dict__ == before


def reserve_quantities(flow=None, *, continuous=True):
    item = dict(target_kWh=10., initial_energy_kWh=5., retained_energy_kWh=4.99,
                charge_efficiency=.9, discharge_efficiency=.8)
    if continuous:
        item['net_flow_hours'] = flow
    return {'_model_correction_v25_reserve': {'ups': item.copy(), 'cold': item.copy()}}


@pytest.mark.parametrize('flow', [None, '0.1', [], 0., -1., True, float('nan'), float('inf')])
def test_continuous_reserve_flow_time_rejects_invalid_types_and_nonpositive_values_with_value_error(flow):
    from tools.model_correction_v25.reserve import validate_solver_context
    with pytest.raises(ValueError):
        validate_solver_context(reserve_quantities(flow))


def test_continuous_reserve_flow_time_is_bounded_by_actual_interval_without_changing_legacy_context():
    from tools.model_correction_v25.reserve import validate_solver_context
    legacy = reserve_quantities(continuous=False)
    assert validate_solver_context(legacy, dt_h=.1) is legacy['_model_correction_v25_reserve']
    good = reserve_quantities(.09)
    assert validate_solver_context(good, dt_h=.1) is good['_model_correction_v25_reserve']
    with pytest.raises(ValueError):
        validate_solver_context(reserve_quantities(.11), dt_h=.1)
    for bad_dt in (None, '0.1', [], 0., -1., True, float('nan'), float('inf')):
        if bad_dt is None:
            # Omitting dt remains valid for standalone inspection.
            assert validate_solver_context(good) is good['_model_correction_v25_reserve']
        else:
            with pytest.raises(ValueError):
                validate_solver_context(good, dt_h=bad_dt)


def test_actual_physical_solve_rejects_flow_time_larger_than_its_interval():
    from types import SimpleNamespace
    import pandas as pd
    from hospital_ies.model import load_plant
    from hospital_ies.resilience import RuntimeState, empty_faults
    from tools.operating_response.physical_balance import solve_physical
    plant = load_plant(ROOT / 'configs/plant_configs.json', 'doe_capacity_matched_exploratory')
    row = SimpleNamespace(timestamp=pd.Timestamp('2017-07-10 12:00'), outdoor_temperature_C=25.)
    state = RuntimeState(np.zeros(3), 2400., set(), np.zeros(3), {})
    config = reserve_quantities(.11)
    config.update(_model_closure_v22={'preserve_bridge': True}, _sci_storage_ports={})
    with pytest.raises(ValueError):
        solve_physical(row, plant, np.zeros((4, 3)), empty_faults(), state, .1, 100., False, 900., config=config)
