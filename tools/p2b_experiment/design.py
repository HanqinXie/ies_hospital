"""Pure P2B specification generation, conservative support and frozen assignments.

No environment is created and no hospital response is evaluated. Distribution
parameters are study assumptions; no real event probability is inferred.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import re
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]

VERSION = 'p2b.experiment.design.v1'
SPLIT_VERSION = 'p2b.preregistered_support_components.v1'
INITIALIZATION_SEED = 20261012
POLICIES = ['priority_milp', 'secure_milp']
ROLES = ('pilot_train', 'pilot_validation', 'development_boundary')
DEVICES = tuple([f'GT_{i}' for i in (1, 2, 3)] +
    [f'HRSG_{i}' for i in (1, 2, 3)] + [f'EC_{i}' for i in (1, 2, 3)] +
    [f'BOILER_{i}' for i in (1, 2, 3)] + ['ABS', 'TOWER', 'GRID', 'DIESEL'] +
    [f'TOWER_{i}' for i in (1, 2, 3, 4)])
SEVERITIES = {'full_outage': 0., 'severe': .25, 'moderate': .5, 'mild': .75}
STUDY = {
    'L2_mixed': dict(event_class='L2', shock_e=[0., 1.8], shock_c=[0., 1.4],
        heat_shock=.4, p0_electric_shock=0., grid_limit_kW=850.,
        response_delay_minutes=0., enable_propagation=False,
        shock_start_minute=60., shock_duration_minutes=240., automatic_fast_shedding=False),
    'L4a_boundary': dict(event_class='L4a', shock_e=[.8, 2.1], shock_c=[.45, 1.25],
        heat_shock=.3, p0_electric_shock=.5, grid_limit_kW=250.,
        response_delay_minutes=45., enable_propagation=True,
        shock_start_minute=60., shock_duration_minutes=240., automatic_fast_shedding=False),
}


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True,
                      separators=(',', ':'), allow_nan=False)


def _hash(value):
    return hashlib.sha256(_canonical(value).encode('utf-8')).hexdigest()


def _rng(seed, *keys):
    return np.random.default_rng(int(_hash([int(seed), *keys])[:32], 16))


def _overlap(a, b):
    return (a['raw_start_minute'] < b['raw_end_minute'] and
            b['raw_start_minute'] < a['raw_end_minute'])


def support_interval(spec):
    """Include all source hours, plus a conservative endpoint lookup hour.

    P1B reset repeats start_hour, not preceding chronological hours. History is
    explicitly run inside duration_steps. The source endpoint hour is included
    conservatively even if a terminal observation does not need to read it.
    """
    start = (int(spec['start_hour']) - 1) * 60
    duration = int(spec['duration_steps']) * float(spec.get('step_minutes', 15))
    if start < 0 or duration <= 0 or start + duration > 8760 * 60:
        raise ValueError('Invalid or out-of-year support')
    end = min(8760 * 60, start + (math.floor(duration / 60) + 1) * 60)
    return dict(raw_start_minute=start, raw_end_minute=end,
        actual_trajectory_interval_minutes=[start, start + duration],
        initialization_source_hours=[int(spec['start_hour'])],
        initialization_historical_minutes=0,
        algebraic_initialization_iterations=max(1, int(spec.get('pre_event_warmup_steps', 0))),
        historical_observation_interval_minutes=spec.get('history_interval_minutes', [0, 0]),
        score_interval_minutes=spec.get('score_interval_minutes', [0, duration]),
        label_tail_interval_minutes=spec.get('tail_interval_minutes', [duration, duration]),
        endpoint_source_hour_reserved=True,
        source_semantics='hourly rows; reset repeats first row; final boundary hour conservatively included')


def known_development_support(root):
    """Read only frozen scenario registries and explicit old diagnostic constants.

    This is an incident exposure registry, not a claim that raw annual inputs
    have never been read. Source paths and exact byte hashes are retained.
    """
    root = Path(root)
    patterns = ('outputs/v43_p0_*/regression_results.json',
                'outputs/v43_p1*_*/**/scenarios.json',
                'outputs/v43_p2a_*/baseline_replay/scenarios.json',
                'outputs/v43_p2a_*/diagnostic_plan.json')
    paths = sorted({p for pattern in patterns for p in root.glob(pattern)})
    rows = []

    def visit(value, path, digest):
        if isinstance(value, dict):
            if 'start_hour' in value and 'duration_steps' in value:
                support = support_interval(value)
                rows.append(dict(raw_start_minute=max(0, support['raw_start_minute'] - 60),
                    raw_end_minute=min(8760*60, support['raw_end_minute'] + 60),
                    source=path, source_sha256=digest,
                    scenario_id=value.get('id'), purpose='known_development',
                    expansion_minutes=60))
            for sub in value.values():
                visit(sub, path, digest)
        elif isinstance(value, list):
            for sub in value:
                visit(sub, path, digest)

    for path in paths:
        raw = path.read_bytes()
        visit(json.loads(raw), path.relative_to(root).as_posix(), hashlib.sha256(raw).hexdigest())
    # Explicit old test/diagnostic fixtures have very short trajectories. A
    # conservative +/-24-hour exclusion avoids underclaiming their support.
    sources = sorted(set(root.glob('tests/test_v43*.py')) |
                     {p for phase in ('p0', 'p1a', 'p1b', 'p2a')
                      for p in (root / 'tools' / (phase + '_audit')).glob('*.py')})
    for path in sources:
        raw = path.read_bytes()
        for match in re.finditer(r'start_hour\s*=\s*(\d+)', raw.decode('utf-8')):
            start = (int(match.group(1)) - 1) * 60
            rows.append(dict(raw_start_minute=max(0, start - 1440),
                raw_end_minute=min(8760*60, start + 1440),
                source=path.relative_to(root).as_posix(),
                source_sha256=hashlib.sha256(raw).hexdigest(),
                scenario_id=None, purpose='known_development', expansion_minutes=1440))
    unique = {_canonical(row): row for row in rows}
    return [unique[key] for key in sorted(unique)]


def assign_components(rows):
    """Union real lineage or full support; template_type alone never unions.

    Conflicting experiment roles fail closed. No test/final role is executable
    in this P2B wrapper, including old development records.
    """
    by_id = {}
    for row in rows:
        if row['role'] not in ROLES:
            raise ValueError('No final/blind assignments are authorized in P2B')
        key = row['scenario_id']
        if key in by_id and by_id[key] != row:
            raise ValueError('Inconsistent duplicate scenario lineage')
        by_id[key] = row
    items = [by_id[key] for key in sorted(by_id)]
    parents = list(range(len(items)))

    def find(i):
        while parents[i] != i:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i

    for i, a in enumerate(items):
        for j, b in enumerate(items[:i]):
            lineage = any(a.get(k) is not None and a.get(k) == b.get(k) for k in
                ('root_family_id', 'parent_instance_id', 'family_id', 'parent_template_id',
                 'base_state_id', 'exogenous_stream_id'))
            # Full raw support plus 60 min either side; this is additional to
            # explicit history and tail, not a replacement for them.
            near = (a['raw_start_minute'] - 60 < b['raw_end_minute'] + 60 and
                    b['raw_start_minute'] - 60 < a['raw_end_minute'] + 60)
            if lineage or near:
                aa, bb = find(i), find(j)
                if aa != bb:
                    parents[max(aa, bb)] = min(aa, bb)
    groups = {}
    for i, row in enumerate(items):
        groups.setdefault(find(i), []).append(row)
    result = {}
    for group in groups.values():
        roles = {r['role'] for r in group}
        if len(roles) != 1:
            raise ValueError('Connected lineage/support component has conflicting roles')
        role = next(iter(roles))
        gid = 'p2b_component:' + _hash(sorted(r['scenario_id'] for r in group))
        for row in group:
            result[row['scenario_id']] = dict(group_id=gid, split=role,
                fit_allowed=role == 'pilot_train', experiment_role=role,
                split_version=SPLIT_VERSION, embargo_minutes_each_side=60,
                raw_start_minute=row['raw_start_minute'], raw_end_minute=row['raw_end_minute'],
                time_block_id=row.get('time_block_id'), purpose='development', is_final_blind=False)
    return result


def _time_blocks(seed, known):
    candidates = []
    for day in range(365):
        block = dict(raw_start_minute=day*1440, raw_end_minute=(day+1)*1440)
        if not any(_overlap(block, row) for row in known):
            candidates.append(day)
    if len(candidates) < 92:
        raise ValueError('Insufficient uncontaminated time blocks for fixed support design')
    order = _rng(seed, 'calendar_only_allocation').permutation(candidates).tolist()
    role_conditions = []
    for condition in STUDY:
        role_conditions += [('pilot_train', condition)] * 24
        role_conditions += [('pilot_validation', condition)] * 6
    role_conditions += [('development_boundary', None)] * 20
    role_conditions += [('final_reserved', None)] * 12
    blocks = []
    for i, (role, condition) in enumerate(role_conditions):
        day = int(order[i])
        blocks.append(dict(time_block_id=f'p2b_day_{day:03d}', role=role, condition=condition,
            raw_start_minute=day*1440, raw_end_minute=(day+1)*1440,
            start_hour=day*24+7, reserved_only=role == 'final_reserved',
            selection='seeded calendar-only permutation; no response/load-value selection'))
    return blocks


def _base(block, key, event_class, root_key, seed):
    family = 'p2b_root:' + _hash([seed, root_key])
    return dict(id='p2b_' + key, stage=int(event_class[1]), event_class=event_class,
        event_schema_version='p2a.events.v1', sample_schema_version=VERSION,
        start_hour=block['start_hour'], duration_steps=32, step_minutes=15,
        history_interval_minutes=[0, 60], score_interval_minutes=[60, 420],
        tail_interval_minutes=[420, 480], label_horizons_minutes=[15, 30, 60],
        pre_event_warmup_steps=6, automatic_fast_shedding=False,
        response_delay_minutes=0., grid_limit_kW=850.,
        enable_propagation=int(event_class[1]) >= 3,
        response_clock_anchor='fault_start_minute' if event_class in ('L1', 'L3') else 'shock_start_minute',
        family_id=family, root_family_id=family, parent_instance_id=family,
        parent_template_id=family, template_type=event_class, design_family=family,
        base_state_id='p2b_initial:' + _hash([block['start_hour'], 6, 'frozen_initialization']),
        exogenous_stream_id='p2b_stream:' + _hash([seed, root_key]),
        time_block_id=block['time_block_id'], experiment_role=block['role'],
        initialization_seed=INITIALIZATION_SEED, sampling_cohort='main', purpose='development',
        p_reference=None, importance_weight=None, q_design_density=None,
        sampling_weight_is_failure_probability=False, demand_background='calendar_pool',
        control_capability_note='existing automatic_fast_shedding=False; no capability parameters changed')


def _events(spec):
    events = []
    if spec.get('faults'):
        dev = spec['initiating_device']
        events.append(dict(origin='exogenous', kind='common_mode_fault' if dev == 'TOWER' else 'device_fault',
            start_minute=spec['fault_start_minute'],
            end_minute=spec['fault_start_minute'] + spec['fault_duration_minutes'],
            device=dev, availability=spec['faults'][dev]))
    if spec.get('shock'):
        events.append(dict(origin='exogenous', kind='load_shock',
            start_minute=spec['shock_start_minute'],
            end_minute=spec['shock_start_minute'] + spec['shock_duration_minutes'],
            shock=spec['shock'], shock_by_priority=spec.get('shock_by_priority', {})))
    spec['exogenous_events'] = events
    spec['base_state_id'] = 'p2b_initial:' + _hash([spec['start_hour'],
        spec['pre_event_warmup_steps'], spec.get('pre_event_grid_limit_kW', spec['grid_limit_kW']),
        spec['initialization_seed'], 'frozen_initialization'])
    spec['actual_p0_load_multiplier'] = {k: (1+spec.get('shock', {}).get(k, 0.)) *
        (1+spec.get('shock_by_priority', {}).get('P0', {}).get(k, 0.)) for k in ('e', 'h', 'c')}
    spec['full_source_support'] = support_interval(spec)
    return spec


def _study_scenarios(blocks, seed):
    scenarios = []
    for block in blocks:
        if block['role'] not in ('pilot_train', 'pilot_validation'):
            continue
        name = block['condition']
        config = STUDY[name]
        for i in range(5):
            key = f"{name}_{block['time_block_id']}_{i}"
            u = _rng(seed, 'study_independent_continuous', key).random(2)
            e = config['shock_e'][0] + float(u[0]) * (config['shock_e'][1] - config['shock_e'][0])
            c = config['shock_c'][0] + float(u[1]) * (config['shock_c'][1] - config['shock_c'][0])
            spec = _base(block, key, config['event_class'], key, seed)
            spec.update(distribution_id='p_study.p2b.independent_conditional.v1',
                sampling_role='fixed_study', sampling_method='balanced_calendar_strata_independent_uniform_continuous',
                study_stratum=name, offline_input_order=['shock_e', 'shock_c', 'calendar_hour'],
                offline_inputs=[e, c, block['start_hour']],
                shock={'e': e, 'h': config['heat_shock'], 'c': c}, shock_mode='mixed',
                shock_by_priority={'P0': {'e': config['p0_electric_shock']}} if config['p0_electric_shock'] else {},
                shock_start_minute=config['shock_start_minute'],
                shock_duration_minutes=config['shock_duration_minutes'],
                stratum='boundary_candidate' if name == 'L4a_boundary' else 'mixed',
                grid_limit_kW=config['grid_limit_kW'],
                response_delay_minutes=config['response_delay_minutes'])
            scenarios.append(_events(spec))
    return scenarios


def _development_scenarios(blocks, seed):
    blocks = [b for b in blocks if b['role'] == 'development_boundary']
    scenarios = []
    for i, dev in enumerate(DEVICES):
        block = blocks[i]
        for j, event_class in enumerate(('L1', 'L3', 'L4b', 'L4b')):
            parent = f'dev_{i:02d}_{event_class}'
            spec = _base(block, parent + f'_variant{j}', event_class, parent, seed)
            severity = tuple(SEVERITIES)[(i+j) % 4]
            spec.update(distribution_id='q_design.p2b.fixed_coverage.v1', sampling_role='development_boundary',
                sampling_method='fixed_device_severity_timing_coverage', study_stratum=None,
                initiating_device=dev, faults={dev: SEVERITIES[severity]},
                fault_severity_layer=severity, contingency_class='common_mode' if dev == 'TOWER' else 'N-1',
                fault_start_minute=60. if event_class != 'L4b' else (60., 90., 120.)[i % 3],
                fault_duration_minutes=(120., 240., 360.)[i % 3],
                response_delay_minutes=0. if event_class == 'L1' else (0., 15., 30., 45.)[i % 4],
                grid_limit_kW=850. if event_class == 'L1' else (160., 250., 350., 500., 850.)[i % 5],
                variant_parent_id=None if j != 3 else 'p2b_' + parent + '_variant2',
                stratum='device_coverage')
            if event_class == 'L4b':
                spec.update(shock={'e': .8 + 1.3 * ((i % 5)/4), 'h': .3, 'c': .45 + .8 * ((i % 4)/3)},
                    shock_by_priority={'P0': {'e': .5}}, shock_start_minute=90.,
                    shock_duration_minutes=240., stratum='boundary_candidate')
                # Same actual exogenous parent and all settings, except the
                # explicit severity variant. This is never a new independent root.
                if j == 3:
                    original = scenarios[-1]
                    spec['response_delay_minutes'] = original['response_delay_minutes']
                    availability = original['faults'][dev]
                    neighbor = availability + .25 if availability < .75 else .5
                    spec['faults'] = {dev: neighbor}
                    spec['fault_severity_layer'] = next(k for k, v in SEVERITIES.items() if v == neighbor)
            scenarios.append(_events(spec))
        event_class = 'L2' if i < 12 else 'L4a'
        key = f'coverage_shock_{i:02d}'
        spec = _base(block, key, event_class, key, seed)
        spec.update(distribution_id='q_design.p2b.fixed_coverage.v1', sampling_role='development_boundary',
            sampling_method='fixed_carrier_modes_and_corners', study_stratum=None,
            shock_start_minute=60., shock_duration_minutes=(30., 120., 240., 360.)[i % 4],
            stratum='carrier_boundary')
        if event_class == 'L2':
            mode = ('e', 'h', 'c', 'mixed')[i % 4]
            fraction = (0., .5, 1.)[i // 4]
            spec.update(shock_mode=mode, shock={k: high*fraction if mode in (k, 'mixed') else 0.
                for k, high in (('e', 1.8), ('h', 1.), ('c', 1.4))})
        else:
            k = i - 12
            spec.update(shock={'e': .8+1.3*(k % 2), 'h': .3, 'c': .45+.8*((k//2) % 2)},
                shock_by_priority={'P0': {'e': .2+.7*(k//4)}},
                response_delay_minutes=45., grid_limit_kW=250., stratum='boundary_candidate')
        scenarios.append(_events(spec))
    return scenarios


def _study_distribution(blocks):
    result = dict(id='p_study.p2b.independent_conditional.v1', p_reference=None,
        p_final_reference=None, importance_weight=None,
        interpretation='Artificial independent conditional study distribution, not hospital frequency',
        mixture_weights={'L2_mixed': .5, 'L4a_boundary': .5}, strata={})
    for name, config in STUDY.items():
        measures = {}
        for measure, role in (('basis_measure', None), ('fit_measure', 'pilot_train'),
                              ('validation_measure', 'pilot_validation')):
            hours = sorted(b['start_hour'] for b in blocks if b['condition'] == name and
                           (role is None or b['role'] == role))
            measures[measure] = dict(shock_e=dict(family='uniform', bounds=config['shock_e']),
                shock_c=dict(family='uniform', bounds=config['shock_c']),
                calendar_hour=dict(family='discrete_uniform', values=hours, probability_per_value=1/len(hours)),
                factorization='P(calendar_hour) * Uniform(shock_e) * Uniform(shock_c)')
        result['strata'][name] = dict(continuous_independence_assumed=True,
            input_order=['shock_e', 'shock_c', 'calendar_hour'],
            input_units=['relative_increment', 'relative_increment', 'one_based_hour'],
            fixed_settings=config, **measures,
            design_distribution='5 independent continuous draws per preregistered calendar stratum',
            validation_interpretation='held-out calendar background conditional measure; not IID pooled study',
            basis_prior_provenance='known preregistered calendar pool; not estimated from validation inputs or outcomes',
            source='scenario_sampler.py L2 and boundary_candidate ranges; fixed settings are research design')
    return result


def _coverage(scenarios, assignments):
    from scenario_sampler import coverage_summary
    result = coverage_summary(scenarios)
    conditions = []
    for name, config in STUDY.items():
        for role in ('pilot_train', 'pilot_validation'):
            group = [s for s in scenarios if s.get('study_stratum') == name and s['experiment_role'] == role]
            row = dict(stratum=name, role=role, count=len(group), time_blocks=len({s['time_block_id'] for s in group}))
            for index, key in enumerate(('shock_e', 'shock_c')):
                low, high = config[key]
                values = [s['offline_inputs'][index] for s in group]
                row[key+'_quartile_counts'] = [sum(min(3, int((v-low)/(high-low)*4)) == q for v in values) for q in range(4)]
                row[key+'_range'] = [min(values), max(values)]
            conditions.append(row)
    q = [s for s in scenarios if s['sampling_role'] == 'development_boundary']
    p = [s for s in scenarios if s['sampling_role'] == 'fixed_study']
    result.update(distribution_id='p2b.explicit_study_and_coverage.v1',
        distribution_counts=dict(Counter(s['distribution_id'] for s in scenarios)),
        q_design=dict(kind='fixed_coverage_not_probability',
            event_class_counts=dict(Counter(s['event_class'] for s in q)),
            L4_stratum_counts=dict(Counter(s.get('stratum') for s in q if s['stage'] == 4))),
        p_study=dict(kind='independent_continuous_given_preregistered_calendar_stratum',
            event_class_counts=dict(Counter(s['event_class'] for s in p)),
            interpretation='Known artificial study marginals, no hospital frequency'),
        continuous_conditions=conditions,
        cross_role_overlapping_support_pairs=sum(_overlap(a, b) and a['split'] != b['split']
            for i, a in enumerate(assignments.values()) for b in list(assignments.values())[:i]),
        q_design_definition='100 fixed coverage specifications; no density or importance weight',
        propagation_model_limit='Frozen GT residual-stress protection, no-export isolation and minimum-output interlock are distinct endogenous causes; exogenous device faults are recorded separately. Non-GT service coupling is not fabricated protection.',
        protection_cause_classes=['residual_stress_protection', 'no_export_isolation',
            'minimum_output_interlock', 'exogenous_device_fault'],
        hospital_uncertain_parameter_dependence='independent study margins only; no Copula or sampled endogenous trip')
    return result


def build_design(root=ROOT, seed=20261014, *, known_support=None):
    known = known_development_support(root) if known_support is None else list(known_support)
    blocks = _time_blocks(seed, known)
    scenarios = _study_scenarios(blocks, seed) + _development_scenarios(blocks, seed)
    from hospital_ies.resilience import validate_scenario
    for spec in scenarios:
        validate_scenario(spec)
    rows = [dict(scenario_id=s['id'], root_family_id=s['root_family_id'],
        parent_instance_id=s['parent_instance_id'], base_state_id=s['base_state_id'],
        exogenous_stream_id=s['exogenous_stream_id'], template_type=s['template_type'],
        role=s['experiment_role'], time_block_id=s['time_block_id'], purpose='development',
        **s['full_source_support']) for s in scenarios]
    assignments = assign_components(rows)
    counts = dict(specifications=len(scenarios), root_families=len({s['root_family_id'] for s in scenarios}),
        used_time_blocks=len({s['time_block_id'] for s in scenarios}), reserved_time_blocks=12,
        connected_groups=len({a['group_id'] for a in assignments.values()}),
        planned_policy_trajectories=len(scenarios)*len(POLICIES),
        role_specifications=dict(Counter(s['experiment_role'] for s in scenarios)))
    if counts['specifications'] > 400:
        raise ValueError('Specification budget exceeded')
    result = dict(version=VERSION, seed=seed, initialization_seed=INITIALIZATION_SEED,
        policies=POLICIES, split_version=SPLIT_VERSION, scenarios=scenarios,
        support_rows=rows, assignments=assignments, time_blocks=blocks,
        known_development_support=known, study_distribution=_study_distribution(blocks),
        coverage=_coverage(scenarios, assignments), counts=counts,
        no_response_evaluated=True, no_final_scenarios=True,
        source_support_assumptions=dict(history_minutes=60, score_minutes=360, tail_minutes=60,
            label_horizons_minutes=[15, 30, 60], algebraic_reset_steps=6,
            reset_reads_previous_hours=False, embargo_minutes_each_side=60),
        pairing='One frozen script, initial seed and execution contract shared by both original policies')
    # coverage_summary has numeric mapping keys; normalize public artifacts so
    # in-memory and JSON-reloaded values are identical.
    result = json.loads(_canonical(result))
    result['design_hash'] = _hash(result)
    return result


def write_design(design, destination):
    """Create once. A partial write remains visible and must not be resumed."""
    destination = Path(destination)
    if destination.exists():
        raise FileExistsError('Immutable design destination already exists')
    expected = dict(design)
    signature = expected.pop('design_hash')
    if _hash(expected) != signature:
        raise ValueError('Design integrity hash mismatch')
    destination.mkdir(parents=True)
    payloads = dict(experiment_design=design, scenario_definitions=design['scenarios'],
        split_assignments=design['assignments'], time_blocks=design['time_blocks'],
        study_distribution=design['study_distribution'], source_support=design['support_rows'],
        coverage=design['coverage'], known_development_support=design['known_development_support'])
    files = []
    for name, content in payloads.items():
        path = destination / (name + '.json')
        raw = (json.dumps(content, indent=2, ensure_ascii=False, allow_nan=False) + '\n').encode('utf-8')
        path.write_bytes(raw)
        files.append(dict(path=path.name, bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest()))
    source = Path(__file__)
    manifest = dict(design_hash=signature, files=files, version=VERSION,
        generator_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        mutation_policy='immutable; new version/directory required for any change')
    (destination / 'design_manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    return manifest


def load_design(destination):
    destination = Path(destination)
    manifest = json.loads((destination / 'design_manifest.json').read_text(encoding='utf-8'))
    for entry in manifest['files']:
        raw = (destination / entry['path']).read_bytes()
        if len(raw) != entry['bytes'] or hashlib.sha256(raw).hexdigest() != entry['sha256']:
            raise ValueError('Design artifact integrity hash mismatch: ' + entry['path'])
    design = json.loads((destination / 'experiment_design.json').read_text(encoding='utf-8'))
    payload = dict(design)
    signature = payload.pop('design_hash')
    if signature != manifest['design_hash'] or _hash(payload) != signature:
        raise ValueError('Design integrity hash mismatch')
    return design


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--seed', type=int, default=20261014)
    args = parser.parse_args()
    design = build_design(ROOT, args.seed)
    write_design(design, args.out)
    print(json.dumps(dict(design_hash=design['design_hash'], counts=design['counts']), ensure_ascii=True))
