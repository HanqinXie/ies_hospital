"""Selection bands are not a change to the original physical/service standards."""
from copy import deepcopy

import pytest

from tools.model_closure_v22.equivalence import compare, with_recovery_metrics


def metrics():
    times = dict(material_minutes=0., longest_material_minutes=0., intervals=[])
    return dict(complete=True, requested_end_minute=120., observed_until_minute=120.,
        carrier_service={f'P{p}': {k: dict(requested_kWh=100., ENS_kWh=0., **deepcopy(times))
            for k in ('e', 'h', 'c')} for p in range(4)},
        priority_union={f'P{p}': deepcopy(times) for p in range(4)},
        critical_union=deepcopy(times), storage={}, initial_storage={'ups': 0., 'cold_store': 0.},
        economics=dict(energy_cost_USD=100.,
            emissions_conditional=dict(total_CO2_kg_conditional=50.,
                emissions_factor_sha256='registered-factor', CO2_boundary='operational_CO2')))


def test_fifty_milliwatt_hours_cannot_override_twenty_two_kwh_general_service():
    left, right = metrics(), metrics()
    left['carrier_service']['P0']['e']['ENS_kWh'] = .00005
    right['carrier_service']['P3']['c']['ENS_kWh'] = 22.
    result = compare(with_recovery_metrics(left, 60.), with_recovery_metrics(right, 60.))
    assert result['choice'] == 'left'
    assert result['decisive_tier'] == 'general_service'
    assert result['critical_engineering_equivalent'] is True
    assert result['critical_dimensions']['P0.e.ENS_kWh']['left_minus_right'] == .00005
    assert left['carrier_service']['P0']['e']['ENS_kWh'] == .00005


def test_material_critical_loss_is_not_bought_with_lower_fees_or_general_service():
    left, right = metrics(), metrics()
    left['carrier_service']['P1']['h']['ENS_kWh'] = .01
    left['economics']['energy_cost_USD'] = 1.
    right['carrier_service']['P2']['c']['ENS_kWh'] = 22.
    result = compare(with_recovery_metrics(left, 60.), with_recovery_metrics(right, 60.))
    assert (result['choice'], result['decisive_tier']) == ('right', 'critical_service')


def test_carriers_cannot_cancel_each_other():
    left, right = metrics(), metrics()
    left['carrier_service']['P0']['e']['ENS_kWh'] = .02
    right['carrier_service']['P0']['c']['ENS_kWh'] = .02
    result = compare(with_recovery_metrics(left, 60.), with_recovery_metrics(right, 60.))
    assert result['choice'] == 'conditional'
    assert result['decisive_tier'] == 'critical_service'


def test_duration_band_and_recovery_are_retained_separately():
    left, right = metrics(), metrics()
    right['carrier_service']['P1']['c'].update(intervals=[[60., 60.1]],
        material_minutes=.1, longest_material_minutes=.1)
    result = compare(with_recovery_metrics(left, 60.), with_recovery_metrics(right, 60.))
    assert result['choice'] == 'left'
    assert result['critical_dimensions']['P1.c.recovery_minutes']['left_minus_right'] == pytest.approx(-.1)
    assert result['critical_dimensions']['P1.c.recovery_minutes']['band'] == 1./60.


@pytest.mark.parametrize('kind', ['incomplete', 'censored', 'different_request', 'missing_fee'])
def test_unknowns_or_incomparable_requests_do_not_turn_into_wins(kind):
    left, right = metrics(), metrics()
    if kind == 'incomplete':
        left['complete'] = False
    elif kind == 'censored':
        left['carrier_service']['P0']['c']['intervals'] = [[60., 120.]]
    elif kind == 'different_request':
        left['carrier_service']['P0']['e']['requested_kWh'] = 101.
    else:
        left['economics']['energy_cost_USD'] = None
    result = compare(with_recovery_metrics(left, 60.), with_recovery_metrics(right, 60.))
    assert result['choice'] == 'unknown'
    assert result['reason']


def test_equal_results_retain_tie_and_return_a_new_recovery_view():
    left, right = metrics(), metrics()
    view = with_recovery_metrics(left, 60.)
    assert 'recovery_minutes' not in left['carrier_service']['P0']['e']
    assert compare(view, with_recovery_metrics(right, 60.))['choice'] == 'tie'


def test_native_conditional_emissions_field_and_accounting_basis_are_checked():
    left, right = metrics(), metrics()
    left['economics']['emissions_conditional']['total_CO2_kg_conditional'] = 40.
    result = compare(with_recovery_metrics(left, 60.), with_recovery_metrics(right, 60.))
    assert (result['choice'], result['decisive_tier']) == ('left', 'conditional_emissions')
    left['economics']['emissions_conditional']['emissions_factor_sha256'] = 'another-factor'
    assert compare(with_recovery_metrics(left, 60.), with_recovery_metrics(right, 60.))['choice'] == 'unknown'


def test_unverified_complete_flag_cannot_hide_a_matching_unknown_tail():
    left, right = metrics(), metrics()
    left['requested_end_minute'] = right['requested_end_minute'] = 240.
    assert compare(with_recovery_metrics(left, 60.), with_recovery_metrics(right, 60.))['choice'] == 'unknown'


@pytest.mark.parametrize('container', ['economics', 'emissions_conditional'])
def test_unknown_accounting_containers_do_not_raise_or_rank(container):
    left, right = metrics(), metrics()
    if container == 'economics':
        left['economics'] = None
    else:
        left['economics']['emissions_conditional'] = None
    assert compare(with_recovery_metrics(left, 60.), with_recovery_metrics(right, 60.))['choice'] == 'unknown'


def test_lower_fee_with_less_terminal_inventory_is_a_tradeoff():
    left, right = metrics(), metrics()
    for value, final in ((left, 10.), (right, 20.)):
        value['initial_storage']['ups'] = 40./.95
        value['storage']['ups'] = dict(last_receipt=dict(final_kWh=final))
    left['economics']['energy_cost_USD'] = 99.
    result = compare(with_recovery_metrics(left, 60.), with_recovery_metrics(right, 60.))
    assert (result['choice'], result['decisive_tier']) == ('conditional', 'terminal_inventory_and_cost')
