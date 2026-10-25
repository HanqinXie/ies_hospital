"""Preregistered V23 comparison; service, money and retained stores do not cancel.

All bands apply only to interpreting raw results, never solver feasibility,
materiality, control, inventory settlement or recovery confirmation criteria.
"""
from __future__ import annotations

import copy
import math

from .metrics import CONTRACT_ID as RECOVERY_CONTRACT, CARRIERS, PRIORITIES

CONTRACT_ID = 'hospital_ies.engineering_comparison.v23.v1'
ENS_ABSOLUTE_KWH = .001
ENS_REQUEST_FRACTION = 1e-6
TIME_MINUTES = 1./60.
MONEY_ABSOLUTE_USD = .01
CO2_ABSOLUTE_KG = .01
INVENTORY_ABSOLUTE_KWH = .001
RELATIVE_BAND = 1e-6
NUMERICAL_COMPARE = 1e-9
REQUIRED_INVENTORIES = frozenset(('ups', 'cold_store', 'DG_shaft_budget'))


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def with_comparison_inputs(episode_metrics, reliability_v23, inventories):
    """New offline view; inventories explicitly include UPS, cold and DG budget.

    Each store has ``initial_kWh``, ``scale_kWh`` (capacity or initial DG shaft
    budget), and ``final_kWh``. DG remains a mechanical-work-equivalent budget,
    not chemical fuel or an invented purchase/salvage price. Original ledgers
    and caller objects are retained rather than rewritten.
    """
    result = copy.deepcopy(episode_metrics)
    result['reliability_v23'] = copy.deepcopy(reliability_v23)
    result['comparison_inventory'] = copy.deepcopy(inventories)
    return result


def compare(left, right):
    """Conditional pairwise dominance under identical evidence and definitions.

    Missing/censored recovery cannot become zero. Two no-interruption rows are
    comparable as N/A; when just one is N/A, actual ENS/episode quantities carry
    the difference and there is no fabricated recovery timestamp to subtract.
    """
    result = dict(contract_id=CONTRACT_ID, choice='unknown', decisive_tier=None,
        reason=None, critical_engineering_equivalent=None, critical_dimensions={},
        general_dimensions={}, economics_dimensions={}, inventory_dimensions={},
        recovery_statuses={}, bands=dict(ENS_absolute_kWh=ENS_ABSOLUTE_KWH,
            ENS_requested_fraction=ENS_REQUEST_FRACTION, time_minutes=TIME_MINUTES,
            money_absolute_USD=MONEY_ABSOLUTE_USD, CO2_absolute_kg=CO2_ABSOLUTE_KG,
            inventory_absolute_kWh=INVENTORY_ABSOLUTE_KWH, relative=RELATIVE_BAND))

    def unknown(reason):
        result['reason'] = reason
        return result

    def dimension(target, name, a, b, band, *, more_is_better=False):
        if not _number(a) or not _number(b) or min(a, b) < 0.:
            raise ValueError('Missing or invalid dimension: '+name)
        delta = float(a-b)
        sign = -1 if delta < -band-NUMERICAL_COMPARE else 1 if delta > band+NUMERICAL_COMPARE else 0
        target[name] = dict(left=a, right=b, left_minus_right=delta, band=band,
            engineering_direction=-sign if more_is_better else sign)

    def time_rows(target, label, a, b):
        sa, sb = a.get('recovery_status'), b.get('recovery_status')
        result['recovery_statuses'][label] = dict(left=sa, right=sb)
        if sa not in ('observed', 'not_applicable') or sb not in ('observed', 'not_applicable'):
            raise ValueError('Missing or censored engineering recovery: '+label)
        for field in ('material_minutes', 'engineering_minutes', 'longest_engineering_minutes'):
            dimension(target, label+'.'+field, a.get(field), b.get(field), TIME_MINUTES)
        dimension(target, label+'.engineering_interruption_count',
            a.get('engineering_interruption_count'), b.get('engineering_interruption_count'), 0.)
        if sa == sb == 'observed':
            for field in ('recovery_absolute_minute', 'recovery_confirmation_minute'):
                dimension(target, label+'.'+field, a.get(field), b.get(field), TIME_MINUTES)
        # External clearance is a shared exogenous fact. Actual source return
        # is an execution outcome and can legitimately differ by policy; its
        # reference wait is reported, never used to normalize an absolute win.
        for key in ('external_fault_clear_minute',):
            ra, rb = a.get(key), b.get(key)
            if _number(ra) and _number(rb) and abs(ra-rb) > NUMERICAL_COMPARE:
                raise ValueError('Different evaluation fact: '+label+'.'+key)

    def assess(rows, tier):
        signs = {row['engineering_direction'] for row in rows.values()}-{0}
        if not signs:
            return False
        result.update(choice='conditional' if len(signs) > 1 else 'left' if -1 in signs else 'right',
            decisive_tier=tier, reason='Opposing material dimensions' if len(signs) > 1 else 'Dominance within registered engineering bands')
        return True

    try:
        a, b = left['reliability_v23'], right['reliability_v23']
        for value in (a, b):
            if value.get('contract_id') != RECOVERY_CONTRACT:
                return unknown('Missing V23 recovery contract')
            if not value.get('complete') or not value.get('support_complete'):
                return unknown('Incomplete observed episode or unknown support')
            end, observed = value.get('requested_end_minute'), value.get('observed_until_minute')
            if not all(_number(v) for v in (end, observed)) or end <= 0. or abs(end-observed) > NUMERICAL_COMPARE:
                return unknown('Full-episode flag contradicts observed support')
        if a.get('definition') != b.get('definition'):
            return unknown('Different engineering recovery definitions')
        for key in ('requested_end_minute', 'observed_until_minute', 'observed_from_minute'):
            if a.get(key) != b.get(key):
                return unknown('Different comparison time support: '+key)
        for priority in PRIORITIES:
            target = result['critical_dimensions'] if priority in PRIORITIES[:2] else result['general_dimensions']
            for carrier in CARRIERS:
                x, y = a['carrier_service'][priority][carrier], b['carrier_service'][priority][carrier]
                ra, rb = x.get('requested_kWh'), y.get('requested_kWh')
                if not all(_number(v) and v >= 0. for v in (ra, rb)) or abs(ra-rb) > NUMERICAL_COMPARE:
                    return unknown('Different or missing original request: '+priority+'.'+carrier)
                label = priority+'.'+carrier
                dimension(target, label+'.ENS_kWh', x.get('ENS_kWh'), y.get('ENS_kWh'),
                    max(ENS_ABSOLUTE_KWH, ENS_REQUEST_FRACTION*ra))
                time_rows(target, label, x, y)
            time_rows(target, priority+'.union', a['priority_union'][priority], b['priority_union'][priority])
        time_rows(result['critical_dimensions'], 'critical_union', a['critical_union'], b['critical_union'])
        result['critical_engineering_equivalent'] = not any(
            value['engineering_direction'] for value in result['critical_dimensions'].values())
        if assess(result['critical_dimensions'], 'critical_service'):
            return result
        if assess(result['general_dimensions'], 'general_service'):
            return result
        ia, ib = left['comparison_inventory'], right['comparison_inventory']
        if not isinstance(ia, dict) or not isinstance(ib, dict) or set(ia) != set(ib) or not REQUIRED_INVENTORIES <= set(ia):
            return unknown('Unknown/different inventory scope, including mandatory DG shaft budget')
        for name in ia:
            x, y = ia[name], ib[name]
            for field in ('initial_kWh', 'scale_kWh'):
                vx, vy = x.get(field), y.get(field)
                if not all(_number(v) and v >= 0. for v in (vx, vy)) or abs(vx-vy) > NUMERICAL_COMPARE:
                    return unknown('Different or unknown inventory '+field+': '+name)
            dimension(result['inventory_dimensions'], name+'.terminal_kWh', x.get('final_kWh'), y.get('final_kWh'),
                max(INVENTORY_ABSOLUTE_KWH, RELATIVE_BAND*x['scale_kWh']), more_is_better=True)
        ea, eb = left['economics'], right['economics']
        for key in ('price_year', 'price_currency'):
            if not ea.get(key) or ea.get(key) != eb.get(key):
                return unknown('Different or missing monetary basis: '+key)
        ca, cb = ea.get('energy_cost_USD'), eb.get('energy_cost_USD')
        if not all(_number(v) for v in (ca, cb)):
            return unknown('Missing energy cost')
        dimension(result['economics_dimensions'], 'energy_cost_USD', ca, cb,
            max(MONEY_ABSOLUTE_USD, RELATIVE_BAND*max(ca, cb)))
        if assess(dict(result['economics_dimensions'], **result['inventory_dimensions']), 'terminal_inventory_and_cost'):
            return result
        fa, fb = ea['emissions_conditional'], eb['emissions_conditional']
        for key in ('emissions_factor_sha256', 'CO2_boundary'):
            if not fa.get(key) or fa.get(key) != fb.get(key):
                return unknown('Different or missing conditional-emissions basis: '+key)
        ca, cb = fa.get('total_CO2_kg_conditional'), fb.get('total_CO2_kg_conditional')
        if not all(_number(v) for v in (ca, cb)):
            return unknown('Missing conditional CO2')
        dimension(result['economics_dimensions'], 'CO2_kg_conditional', ca, cb,
            max(CO2_ABSOLUTE_KG, RELATIVE_BAND*max(ca, cb)))
        if assess({'CO2': result['economics_dimensions']['CO2_kg_conditional']}, 'conditional_emissions'):
            return result
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        return unknown(str(exc))
    result.update(choice='tie', decisive_tier='all_registered_dimensions', reason='Within all registered engineering bands')
    return result
