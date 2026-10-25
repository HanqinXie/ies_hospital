"""Engineering comparison does not buy service or inventory with tiny fees."""
from copy import deepcopy
import importlib

import pytest

from tools.model_correctness_v23.metrics import build_reliability_metrics


def api():
    try:
        return importlib.import_module('tools.model_correctness_v23.comparison')
    except ModuleNotFoundError as exc:
        if exc.name == 'tools.model_correctness_v23.comparison':
            pytest.fail('V23 comparison with engineering recovery and all inventories is missing')
        raise


def actual(shortage=None):
    demand = [[100., 100., 100.] for _ in range(4)]
    cut = [[0., 0., 0.] for _ in range(4)]
    if shortage:
        priority, carrier, power = shortage
        cut[int(priority[1])]['ehc'.index(carrier)] = power
    def seg(a, b, loss):
        return dict(start_minute=a, end_minute=b, service_request_ledger=dict(
            original_demand_kW=deepcopy(demand), active_cut_kW=loss,
            connected_unserved_kW=[[0., 0., 0.] for _ in range(4)],
            delivered_kW=[[100.-x for x in row] for row in loss]))
    raw = dict(segments=[seg(0., 1., cut), seg(1., 4., [[0., 0., 0.] for _ in range(4)])],
        summary=dict(requested_end_minute=4.))
    return build_reliability_metrics(raw)


def view(shortage=None):
    episode = dict(economics=dict(energy_cost_USD=100., price_year=2001, price_currency='USD',
        emissions_conditional=dict(total_CO2_kg_conditional=50.,
            emissions_factor_sha256='same-factor', CO2_boundary='same-boundary')))
    inventories = {name: dict(initial_kWh=scale, scale_kWh=scale, final_kWh=final)
        for name, scale, final in [('ups', 40., 20.), ('cold_store', 60., 30.), ('DG_shaft_budget', 600., 400.)]}
    return api().with_comparison_inputs(episode, actual(shortage), inventories)


def test_no_interruption_is_not_applicable_and_comparable_without_recovery_zero():
    left, right = view(), view()
    before = deepcopy(left)
    compared = api().compare(left, right)
    assert compared['choice'] == 'tie'
    assert compared['critical_engineering_equivalent'] is True
    assert left['reliability_v23']['critical_union']['recovery_status'] == 'not_applicable'
    assert left['reliability_v23']['critical_union']['recovery_absolute_minute'] is None
    assert left == before


def test_subcent_ten_gram_and_subwh_dg_differences_are_recorded_but_equivalent():
    left, right = view(), view()
    left['economics']['energy_cost_USD'] -= .000223406
    left['economics']['emissions_conditional']['total_CO2_kg_conditional'] -= .005
    left['comparison_inventory']['DG_shaft_budget']['final_kWh'] += .000744624
    result = api().compare(left, right)
    assert result['choice'] == 'tie'
    assert result['economics_dimensions']['energy_cost_USD']['left_minus_right'] != 0.
    assert result['economics_dimensions']['energy_cost_USD']['band'] == .01
    assert result['inventory_dimensions']['DG_shaft_budget.terminal_kWh']['band'] == .001


def test_cost_and_every_inventory_are_noncompensating_after_service_equivalence():
    left, right = view(), view()
    left['economics']['energy_cost_USD'] -= 1.
    left['comparison_inventory']['DG_shaft_budget']['final_kWh'] -= 1.
    result = api().compare(left, right)
    assert (result['choice'], result['decisive_tier']) == ('conditional', 'terminal_inventory_and_cost')
    assert set(result['inventory_dimensions']) == {'ups.terminal_kWh', 'cold_store.terminal_kWh', 'DG_shaft_budget.terminal_kWh'}
    left['economics']['energy_cost_USD'] = 100.
    left['comparison_inventory']['ups']['final_kWh'] += 1.
    assert api().compare(left, right)['choice'] == 'conditional'


def test_material_critical_shortage_is_not_bought_with_lower_cost_or_general_service():
    left, right = view(('P0', 'c', 25.)), view(('P3', 'c', 50.))
    left['economics']['energy_cost_USD'] = 1.
    result = api().compare(left, right)
    assert (result['choice'], result['decisive_tier']) == ('right', 'critical_service')
    assert result['critical_dimensions']['P0.c.engineering_interruption_count']['left_minus_right'] == 1.


def test_original_six_critical_ens_bands_keep_tiny_raw_values():
    left, right = view(), view()
    left['reliability_v23']['carrier_service']['P0']['e']['ENS_kWh'] = .00005
    right['reliability_v23']['carrier_service']['P3']['c']['ENS_kWh'] = 22.
    result = api().compare(left, right)
    assert (result['choice'], result['decisive_tier']) == ('left', 'general_service')
    assert result['critical_dimensions']['P0.e.ENS_kWh']['left_minus_right'] == .00005


@pytest.mark.parametrize('kind', ['censored', 'missing', 'missing_dg', 'different_initial', 'different_factors'])
def test_missing_or_censored_evidence_never_turns_into_a_rank(kind):
    left, right = view(), view()
    if kind in ('censored', 'missing'):
        left['reliability_v23']['carrier_service']['P0']['c']['recovery_status'] = kind
    elif kind == 'missing_dg':
        del left['comparison_inventory']['DG_shaft_budget']
    elif kind == 'different_initial':
        left['comparison_inventory']['DG_shaft_budget']['initial_kWh'] = 599.
    else:
        left['economics']['emissions_conditional']['emissions_factor_sha256'] = 'other'
    result = api().compare(left, right)
    assert result['choice'] == 'unknown'
    assert result['reason']


def test_real_cost_emission_and_inventory_differences_remain_directional():
    left, right = view(), view()
    left['economics']['energy_cost_USD'] = 99.
    assert api().compare(left, right)['choice'] == 'left'
    left['economics']['energy_cost_USD'] = 100.
    left['economics']['emissions_conditional']['total_CO2_kg_conditional'] = 49.9
    assert api().compare(left, right)['decisive_tier'] == 'conditional_emissions'
    left['economics']['emissions_conditional']['total_CO2_kg_conditional'] = 50.
    left['comparison_inventory']['cold_store']['final_kWh'] += .1
    assert api().compare(left, right)['choice'] == 'left'


def test_actual_source_restoration_is_an_outcome_not_a_shared_exogenous_fact():
    left, right = view(('P0', 'c', 25.)), view(('P0', 'c', 25.))
    for value, source in ((left, .25), (right, .75)):
        rel = value['reliability_v23']
        rows = [rel['carrier_service']['P0']['c'], rel['priority_union']['P0'], rel['critical_union']]
        for row in rows:
            row.update(external_fault_clear_minute=0., actual_source_restore_minute=source,
                recovery_wait_after_actual_source_restore_minutes=1.-source)
    left['economics']['energy_cost_USD'] = 99.
    result = api().compare(left, right)
    assert (result['choice'], result['decisive_tier']) == ('left', 'terminal_inventory_and_cost')
    assert left['reliability_v23']['critical_union']['actual_source_restore_minute'] == .25
    assert right['reliability_v23']['critical_union']['actual_source_restore_minute'] == .75
    right['reliability_v23']['critical_union']['external_fault_clear_minute'] = .5
    assert api().compare(left, right)['choice'] == 'unknown'
