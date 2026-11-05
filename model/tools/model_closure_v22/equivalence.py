"""Preregistered engineering selection over unchanged, full precision metrics.

No feasibility, solver, service-materiality or physical tolerance is changed.
The 1 Wh / one part per million requested-energy bands apply to each carrier
and priority separately. Time bands are one second, below the original native
15 second transfer and minute storage events. Missing/censored data stay unknown.
"""
from __future__ import annotations

import copy
import math

CONTRACT_ID = 'hospital_ies.engineering_equivalence.v22.v1'
ENS_ABSOLUTE_KWH = .001
ENS_REQUESTED_FRACTION = 1e-6
TIME_MINUTES = 1. / 60.
NUMERICAL_COMPARE = 1e-9
MONEY_USD = 1e-6
CO2_KG = 1e-6


def _number(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def with_recovery_metrics(metrics, recovery_reference_minute):
    """New view: elapsed time from declared comparison origin to last loss end.

    This origin is evaluation-only: generally the externally recorded repair
    boundary. It is never passed to a controller. Early recovery is zero elapsed
    after that origin. A loss touching the observed horizon is right censored,
    so its recovery remains unknown rather than being declared recovered there.
    All original intervals/ENS remain unchanged. An absent interval ledger is
    unknown, not an empty ledger.
    """
    if not _number(recovery_reference_minute) or recovery_reference_minute < 0.:
        raise ValueError('Recovery comparison origin must be finite nonnegative minutes')
    out = copy.deepcopy(metrics)
    horizon = out.get('observed_until_minute')
    out['recovery_reference_minute'] = float(recovery_reference_minute)

    def update(row):
        intervals = row.get('intervals')
        recovered = None
        if isinstance(intervals, list) and _number(horizon):
            if not intervals:
                recovered = 0.
            elif (all(isinstance(pair, list) and len(pair) == 2 and
                      all(_number(v) for v in pair) and 0. <= pair[0] < pair[1] <= horizon + NUMERICAL_COMPARE
                      for pair in intervals) and
                  max(pair[1] for pair in intervals) < horizon - NUMERICAL_COMPARE):
                recovered = max(0., max(pair[1] for pair in intervals) - recovery_reference_minute)
        row['recovery_minutes'] = recovered

    for group in out.get('carrier_service', {}).values():
        for row in group.values():
            update(row)
    for row in out.get('priority_union', {}).values():
        update(row)
    if isinstance(out.get('critical_union'), dict):
        update(out['critical_union'])
    return out


def compare(left, right):
    """Compare full episodes; return tie, conditional or unknown without a rank.

    Critical ENS, duration, longest and recovery are a noncompensating vector.
    General service is considered only after critical engineering equivalence;
    then fees, then conditional CO2. Opposing material dimensions retain a
    conditional choice. This pairwise rule deliberately does not aggregate
    scenarios or impose a unique tournament winner.
    """
    result = dict(contract_id=CONTRACT_ID, choice='unknown', decisive_tier=None,
        reason=None, critical_engineering_equivalent=None,
        critical_dimensions={}, general_dimensions={}, economics_dimensions={},
        inventory_dimensions={})

    def unknown(reason):
        result['reason'] = reason
        return result

    if not left.get('complete') or not right.get('complete'):
        return unknown('Incomplete episode or unknown tail')
    for value in (left, right):
        end, observed = value.get('requested_end_minute'), value.get('observed_until_minute')
        if (not _number(end) or not _number(observed) or end <= 0. or
                abs(end-observed) > NUMERICAL_COMPARE):
            return unknown('Complete flag contradicts actual observed support')
    for name in ('requested_end_minute', 'observed_until_minute', 'recovery_reference_minute'):
        if (not _number(left.get(name)) or not _number(right.get(name)) or
                abs(left[name] - right[name]) > NUMERICAL_COMPARE):
            return unknown('Missing or different comparison support: ' + name)

    def dimension(target, name, a, b, band):
        if not _number(a) or not _number(b) or a < 0. or b < 0.:
            raise ValueError('Missing, censored or invalid dimension: ' + name)
        delta = float(a-b)
        direction = (-1 if delta < -band-NUMERICAL_COMPARE else
                     1 if delta > band+NUMERICAL_COMPARE else 0)
        target[name] = dict(left=a, right=b, left_minus_right=delta, band=band,
                            engineering_direction=direction)

    def time_rows(target, label, a, b):
        for field in ('material_minutes', 'longest_material_minutes', 'recovery_minutes'):
            dimension(target, label+'.'+field, a.get(field), b.get(field), TIME_MINUTES)

    try:
        for p in ('P0', 'P1', 'P2', 'P3'):
            target = result['critical_dimensions'] if p in ('P0', 'P1') else result['general_dimensions']
            for carrier in ('e', 'h', 'c'):
                a, b = left['carrier_service'][p][carrier], right['carrier_service'][p][carrier]
                ra, rb = a.get('requested_kWh'), b.get('requested_kWh')
                if (not _number(ra) or not _number(rb) or min(ra, rb) < 0. or
                        abs(ra-rb) > NUMERICAL_COMPARE):
                    return unknown('Different or unknown original request: '+p+'.'+carrier)
                dimension(target, p+'.'+carrier+'.ENS_kWh', a.get('ENS_kWh'), b.get('ENS_kWh'),
                          max(ENS_ABSOLUTE_KWH, ENS_REQUESTED_FRACTION*ra))
                time_rows(target, p+'.'+carrier, a, b)
            time_rows(target, p+'.union', left['priority_union'][p], right['priority_union'][p])
        time_rows(result['critical_dimensions'], 'critical_union', left['critical_union'], right['critical_union'])
    except (KeyError, TypeError, ValueError) as exc:
        return unknown(str(exc))

    def assess(rows, tier):
        signs = {row['engineering_direction'] for row in rows.values()} - {0}
        if signs:
            result.update(choice=('conditional' if len(signs) > 1 else 'left' if -1 in signs else 'right'),
                          decisive_tier=tier, reason='Opposing material dimensions' if len(signs) > 1 else
                          'Dominance within registered engineering bands')
            return True
        return False

    result['critical_engineering_equivalent'] = not any(
        row['engineering_direction'] for row in result['critical_dimensions'].values())
    if assess(result['critical_dimensions'], 'critical_service'):
        return result
    if assess(result['general_dimensions'], 'general_service'):
        return result
    try:
        if (not isinstance(left.get('storage'), dict) or not isinstance(right.get('storage'), dict) or
                not isinstance(left.get('initial_storage'), dict) or not isinstance(right.get('initial_storage'), dict)):
            return unknown('Missing initial or terminal inventory ledger')
        if set(left['storage']) != set(right['storage']) or set(left['initial_storage']) != set(right['initial_storage']):
            return unknown('Different inventory accounting scope')
        for name in left['initial_storage']:
            a, b = left['initial_storage'][name], right['initial_storage'][name]
            if not _number(a) or not _number(b) or abs(a-b) > NUMERICAL_COMPARE:
                return unknown('Different or unknown paid initial inventory: '+name)
        for name in left['storage']:
            a = left['storage'][name]['last_receipt'].get('final_kWh')
            b = right['storage'][name]['last_receipt'].get('final_kWh')
            dimension(result['inventory_dimensions'], name+'.terminal_kWh', a, b, ENS_ABSOLUTE_KWH)
            # More retained energy is preferable. It is not assigned an
            # invented monetary salvage price to turn a tradeoff into a win.
            result['inventory_dimensions'][name+'.terminal_kWh']['engineering_direction'] *= -1
        if not isinstance(left.get('economics'), dict) or not isinstance(right.get('economics'), dict):
            return unknown('Missing or invalid accounting container')
        dimension(result['economics_dimensions'], 'energy_cost_USD',
                  left['economics'].get('energy_cost_USD'), right['economics'].get('energy_cost_USD'), MONEY_USD)
        combined = dict(result['economics_dimensions'], **result['inventory_dimensions'])
        if assess(combined, 'terminal_inventory_and_cost' if result['inventory_dimensions'] else 'energy_cost'):
            return result
        # The factors and origin remain conditional; callers must compare like
        # registered accounting bases. No missing emission is treated as zero.
        ea, eb = left['economics']['emissions_conditional'], right['economics']['emissions_conditional']
        if not isinstance(ea, dict) or not isinstance(eb, dict):
            return unknown('Missing or invalid conditional-emissions container')
        if any(not ea.get(key) or ea.get(key) != eb.get(key)
               for key in ('emissions_factor_sha256', 'CO2_boundary')):
            return unknown('Different or missing conditional-emissions basis')
        dimension(result['economics_dimensions'], 'CO2_kg_conditional',
                  ea.get('total_CO2_kg_conditional'), eb.get('total_CO2_kg_conditional'), CO2_KG)
        if assess({'CO2': result['economics_dimensions']['CO2_kg_conditional']}, 'conditional_emissions'):
            return result
    except (KeyError, TypeError, ValueError) as exc:
        return unknown(str(exc))
    result.update(choice='tie', decisive_tier='all_registered_dimensions', reason='Within all registered bands')
    return result
