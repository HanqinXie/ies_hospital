"""Read-only recovery of the *accepted actual* native unused-steam primal.

This supports the producer-bound M23/V30 47-coordinate execution layout.
Missing or ambiguous proof stays unknown. No balance remainder is a meter.
Only the standard library is used; importing this module invokes no model.
"""
from __future__ import annotations

import ast
import gzip
import hashlib
import json
import math
from pathlib import Path

SOURCE_FILES = (
    'tools/operating_response/physical_balance.py', 'src/hospital_ies/solver_backend.py',
    'tools/model_correctness_v23/domains.py', 'tools/model_closure_v22/environment.py',
    'tools/engineering_completion_v24/cold_modes.py', 'tools/engineering_completion_v24/environment.py',
    'tools/model_correctness_v23/environment.py', 'src/hospital_ies/state_transition.py',
    'tools/sci_engineering/solver_log.py', 'tools/model_correctness_v23/evidence.py',
    'src/hospital_ies/model.py', 'src/hospital_ies/control_env.py',
    'tools/model_closure_v22/physical.py', 'configs/plant_configs.json',
    'src/hospital_ies/solver_diagnostics.py', 'tools/model_correctness_v23/numerics.py',
    'tools/model_closeout/numerics.py')
_REPLACEMENTS = frozenset(('after_actual_shedding', 'local_event_split', 'protection_threshold_split',
    'm23_event_power_probe', 'local_down_endpoint'))
_STORES = ('ups_discharge_kW', 'ups_charge_kW', 'cold_discharge_kW', 'cold_charge_kW')
_MATCH_TOL = 1e-9  # Arithmetic comparison only; stricter than original physical validation.
_STEAM_TOL = 1e-4  # Original physical_balance steam acceptance, unchanged.


class _Issue(Exception):
    def __init__(self, status, reason):
        self.status, self.reason = status, reason
        super().__init__(reason)


def _unknown(reason):
    raise _Issue('unknown', reason)


def _failed(reason):
    raise _Issue('failed', reason)


def _number(value, name, *, nonnegative=True):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        _failed('invalid_finite_number:' + name)
    if nonnegative and value < 0.:
        _failed('negative_number:' + name)
    return float(value)


def _finite_tree(value):
    if isinstance(value, float) and not math.isfinite(value):
        _failed('nonfinite_raw_payload')
    if isinstance(value, dict):
        for item in value.values():
            _finite_tree(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _finite_tree(item)


def _same(a, b):
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isfinite(a) and math.isfinite(b) and abs(a - b) <= _MATCH_TOL
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    return type(a) is type(b) and a == b


def _digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def _json(handle):
    def invalid(token):
        raise ValueError('Nonstandard JSON number: ' + token)
    return json.load(handle, parse_constant=invalid)


def _read(path, *, zipped=False):
    path = Path(path)
    if not path.is_file():
        _unknown('missing_evidence_file:' + path.name)
    opener = gzip.open if zipped else open
    with opener(path, 'rt', encoding='utf8') as handle:
        return _json(handle)


def _canonical_sha(value, *, sorted_keys=False):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, allow_nan=False,
        sort_keys=sorted_keys, separators=(',', ':')).encode('utf8')).hexdigest()


def _child(root, relative):
    root = Path(root).resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        _failed('evidence_path_escapes_producer')
    return path


def _matrix(value, name, *, columns=3):
    if not isinstance(value, list) or len(value) != 4 or any(not isinstance(row, list) or len(row) != columns for row in value):
        _unknown('missing_or_unsupported_matrix:' + name)
    return [[_number(v, name) for v in row] for row in value]


def _sidecar(directory, receipt):
    if not receipt or receipt.get('closed') is not True or receipt.get('status') != 'completed' or receipt.get('partial_tail_possible') is not False:
        _unknown('incomplete_solver_row_sidecar')
    path = directory / 'solver_rows.jsonl.gz'
    if not path.is_file():
        _unknown('missing_solver_row_sidecar')
    if _digest(path) != receipt.get('gzip_sha256'):
        _failed('solver_sidecar_gzip_sha_mismatch')
    entries, count = {}, 0
    with gzip.open(path, 'rt', encoding='utf8') as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.endswith('\n'):
                _failed('truncated_solver_sidecar')
            entry = json.loads(line, parse_constant=lambda token: _failed('nonfinite_solver_sidecar'))
            ident = entry.get('record_id')
            if not ident or ident in entries or entry.get('schema') != 'hospital_ies.solver_validation_rows.sidecar.v1':
                _failed('invalid_solver_sidecar_identity')
            rows = entry['rows']
            if len(rows) != entry['row_count'] or _canonical_sha(rows) != entry['rows_sha256']:
                _failed('solver_sidecar_rows_sha_or_count_mismatch')
            count += len(rows)
            # Integrity checks cover every row; retain just steam rows to bound
            # memory for complex runs with many uncommitted solver candidates.
            entries[ident] = dict(entry, rows=[r for r in rows if r.get('name') == 'balance.steam'], line=line_number)
    if len(entries) != receipt.get('entry_count') or count != receipt.get('row_count'):
        _failed('solver_sidecar_receipt_count_mismatch')
    return entries


def _context(payload, bindings, directory, source_root):
    if not isinstance(bindings, dict) or not isinstance(bindings.get('source'), dict) or not bindings['source']:
        _unknown('missing_source_bindings')
    disk = _read(directory / 'BINDINGS.json')
    if disk != bindings:
        _failed('supplied_bindings_differ_from_producer')
    source_hashes = {}
    for relative in SOURCE_FILES:
        expected = bindings['source'].get(relative)
        if not expected:
            _unknown('missing_source_binding:' + relative)
        path = _child(source_root, relative)
        if not path.is_file():
            _unknown('missing_bound_source:' + relative)
        observed = _digest(path)
        if observed != expected:
            _failed('producer_source_sha_mismatch:' + relative)
        source_hashes[relative] = observed
    physical = ast.parse((source_root / SOURCE_FILES[0]).read_text(encoding='utf8'))
    coordinates = {node.targets[0].id: node.value.value for node in ast.walk(physical)
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
        and isinstance(node.value, ast.Constant) and node.targets[0].id in ('ia', 'ib', 'ig', 'idg', 'iw', 'iu')}
    if any(coordinates.get(k) != v for k, v in dict(ia=6, ib=10, ig=11, idg=12, iw=13, iu=14).items()):
        _unknown('unsupported_producer_coordinate_layout')
    result = _read(directory / 'RESULT.json')
    if result.get('status') != 'completed' or result.get('unknown_tail_minutes') != 0.:
        _unknown('producer_not_complete')
    raw_path = directory / 'trajectory.json.gz'
    if not raw_path.is_file():
        _unknown('missing_bound_raw_payload')
    if _digest(raw_path) != result.get('raw_sha256'):
        _failed('raw_gzip_sha_mismatch')
    raw = _read(raw_path, zipped=True)
    if raw != payload:
        _failed('supplied_payload_differs_from_bound_raw')
    del raw
    _finite_tree(payload)
    config = _read(source_root / 'configs/plant_configs.json')
    model = ast.parse((source_root / 'src/hospital_ies/model.py').read_text(encoding='utf8'))
    rt = next(ast.literal_eval(n.value) for n in model.body if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == 'RT_TO_KW' for t in n.targets))
    control = (source_root / 'src/hospital_ies/control_env.py').read_text(encoding='utf8')
    if "'doe_capacity_matched_exploratory'" not in control:
        _unknown('unsupported_producer_plant_selection')
    assumptions = config['engineering_assumptions_not_from_source']
    performance = config['historical_pnnl_performance']
    constants = dict(rt=_number(rt, 'RT_TO_KW'),
        steam=_number(assumptions['kWh_thermal_per_lb_steam'], 'steam_enthalpy'),
        exchanger=_number(assumptions['steam_to_building_heat_exchanger_efficiency'], 'HX_efficiency'),
        abs_steam=_number(performance['existing_absorption_steam_lb_per_RT_hr'], 'ABS_steam'),
        ec_electric=_number(performance['existing_electric_chiller_all_in_kW_per_RT'], 'EC_electric'))
    if min(constants['rt'], constants['steam'], constants['exchanger']) <= 0.:
        _failed('nonpositive_steam_conversion')
    return dict(payload=payload, result=result, directory=directory,
        source_hashes=source_hashes, constants=constants, config=config,
        entries=_sidecar(directory, result.get('solver_row_sidecar')),
        cas_cache={})


def _attempts(call):
    if call.get('status') not in ('success', 'legal'):
        _unknown('call_not_accepted_success')
    layers = call.get('solver_backend_layers')
    if not layers or layers[-1].get('layer') != 'cost' or layers[-1].get('status') != 0:
        _unknown('missing_successful_final_cost_layer')
    layer = layers[-1]
    if layer.get('backend') != 'gurobipy':
        _unknown('unsupported_native_backend')
    accepted = []
    for ai, attempt in enumerate(layer.get('attempts', [])):
        if attempt.get('accepted') is not True or attempt.get('status') != 0:
            continue
        if attempt.get('native_status') != 2 or attempt.get('objective_consistent') is not True or attempt.get('error') is not None:
            _unknown('incomplete_native_acceptance')
        x = attempt.get('raw_x')
        if x is None:
            _unknown('missing_accepted_raw_x')
        if not isinstance(x, list) or len(x) != 47:
            _unknown('unsupported_primal_layout')
        x = [_number(v, 'raw_x', nonnegative=False) for v in x]
        if x[13] < 0.:
            _failed('negative_native_unused_steam')
        validation = attempt.get('validation')
        if not isinstance(validation, dict):
            _unknown('missing_native_validation')
        for key, limit in (('max_original_violation', 1e-7), ('max_lock_violation', 1e-10), ('max_integrality_distance', 1e-9)):
            if key not in validation:
                _unknown('missing_native_validation:' + key)
            if _number(validation[key], key) > limit:
                _failed('native_validation_exceeds_original_acceptance:' + key)
        accepted.append(dict(layer_index=len(layers)-1, attempt_index=ai, attempt=attempt, x=x))
    if not accepted:
        _unknown('no_accepted_final_native_primal')
    return accepted


def _rows(context, proof, *, location=None):
    ref = proof['attempt']['validation'].get('rows_ref')
    if not ref:
        _unknown('missing_native_row_binding')
    if ref.get('schema') == 'hospital_ies.solver_validation_rows.reference.v1':
        if location is None:
            _unknown('ordered_rows_have_no_verifiable_call_owner')
        if ref.get('file') != 'solver_rows.jsonl.gz':
            _failed('wrong_solver_sidecar_file')
        entry = context['entries'].get(ref.get('record_id'))
        if entry is None:
            _unknown('missing_solver_sidecar_record')
        if 'location' not in entry:
            _unknown('missing_solver_sidecar_location')
        if entry['location'] != location:
            _failed('solver_sidecar_location_mismatch')
        if entry['rows_sha256'] != ref.get('rows_sha256') or entry['row_count'] != ref.get('row_count'):
            _failed('solver_sidecar_reference_mismatch')
        rows = entry['rows']
    elif ref.get('schema') in ('hospital_ies.m23.validation_rows.reference.v1', 'hospital_ies.m23.domain_success_rows.reference.v1'):
        relative = str(Path(ref['directory']) / ref['file'])
        path = _child(context['directory'], relative)
        manifest = context['result'].get('domain_evidence', {}).get('files', {}).get(Path(relative).as_posix())
        if manifest is None:
            _unknown('missing_domain_blob_manifest_binding')
        if not path.is_file():
            _unknown('missing_domain_row_blob')
        if relative not in context['cas_cache']:
            if _digest(path) != manifest.get('sha256'):
                _failed('domain_blob_gzip_sha_mismatch')
            value = _read(path, zipped=True)
            context['cas_cache'][relative] = value
        rows = context['cas_cache'][relative]
        if _canonical_sha(rows, sorted_keys=True) != ref.get('rows_sha256') or len(rows) != ref.get('row_count'):
            _failed('domain_rows_sha_or_count_mismatch')
    else:
        _unknown('unsupported_native_row_reference')
    steam = [row for row in rows if row.get('name') == 'balance.steam']
    if len(steam) != 1 or steam[0].get('unit') != 'lb/h' or steam[0].get('kind') != 'physical':
        _unknown('no_unique_original_steam_balance_row')
    return steam[0], ref


def _action_service(context, segment, x):
    p, constants, config = context['payload'], context['constants'], context['config']
    actual = segment['action']
    loaded = _matrix(segment['connected_loads_kW'], 'connected_loads')
    lost = [[max(0., x[14+3*g+k]) for k in range(3)] for g in range(4)]
    if not _same(lost, _matrix(segment['unserved_kW'], 'unserved')):
        _failed('native_service_vector_mismatch')
    served = [[loaded[g][k]-lost[g][k] for k in range(3)] for g in range(4)]
    if any(v < -_MATCH_TOL for row in served for v in row):
        _failed('native_unserved_exceeds_connected_request')
    ledger = segment.get('service_request_ledger')
    if not isinstance(ledger, dict):
        _unknown('missing_actual_service_ledger')
    original = _matrix(ledger['original_demand_kW'], 'original_request')
    cut = _matrix(ledger['active_cut_kW'], 'active_cut')
    if not _same(original, [[loaded[g][k]+cut[g][k] for k in range(3)] for g in range(4)]) or not _same(ledger['delivered_kW'], served) or not _same(ledger['connected_unserved_kW'], lost):
        _failed('actual_service_ledger_mismatch')
    deps = segment.get('service_dependencies')
    if not isinstance(deps, dict):
        _unknown('missing_auxiliary_service_evidence')
    required = _matrix(deps['auxiliary_required_kW'], 'auxiliary_required')
    aux = [[required[g][k]*x[26+3*g+k] for k in range(3)] for g in range(4)]
    direct = [served[g][0]-sum(aux[g]) for g in range(4)]
    if not _same(aux, deps['auxiliary_delivered_kW']) or not _same(direct, deps['direct_electric_delivered_kW']):
        _failed('native_auxiliary_service_mismatch')
    observations = []
    for decision in p.get('decisions', []):
        if not isinstance(decision, dict) or 'decision_time_min' not in decision:
            _unknown('missing_decision_clock')
        clock = _number(decision['decision_time_min'], 'decision_clock')
        if clock <= segment['start_minute'] and 'outdoor_temperature_C' in decision.get('features', {}):
            observations.append(decision)
    if not observations:
        _unknown('missing_current_ambient_observation')
    now = max(d['decision_time_min'] for d in observations)
    temperatures = {_number(d['features']['outdoor_temperature_C'], 'ambient', nonnegative=False)
                    for d in observations if d['decision_time_min'] == now}
    if len(temperatures) != 1:
        _unknown('ambiguous_current_ambient_observation')
    temp = temperatures.pop()*1.8+32.
    source = config['source_equipment']; perf = config['historical_pnnl_performance']
    scales = config['study_cases']['doe_capacity_matched_exploratory']
    nominal = source['gas_turbines']['rated_electric_kW_each']
    rating = nominal*scales['gas_turbine_scale']*max(0., 1.23836-.00404*temp)
    if rating <= 0.:
        _unknown('unsupported_zero_ambient_GT_rating')
    steam_ratio = perf['hrsg_steam_exponential_constant_lb_per_hr']*math.exp(perf['hrsg_steam_exponential_exponent_per_kW']*nominal)*scales['hrsg_scale']/rating
    gts = [max(0., v) for v in x[:3]]
    availability = [_number(segment['availability'][f'HRSG_{i}'], 'HRSG_availability') for i in (1,2,3)]
    expected = dict(gt_electric_kW=gts, grid_import_kW=x[11], diesel_kW=x[12],
        absorption_cooling_kW=x[6]*constants['rt'], electric_chiller_cooling_kW=[v*constants['rt'] for v in x[7:10]],
        boiler_steam_lbph=x[10], hrsg_steam_lbph=sum(gts[i]*steam_ratio*availability[i] for i in range(3)),
        tower_rejection_kW=x[6]*(constants['rt']+constants['abs_steam']*constants['steam'])+sum(x[7:10])*(constants['rt']+constants['ec_electric']),
        gt_fuel_MMBtu_h=sum(gts)*(29477.-1954.9*math.log(nominal))*.9851*(.965544+.000584*temp if temp<=59 else .910320+.00152*temp)/1e6)
    model = p['model_config']; station = model['model_closure_v22']['station']
    fixed = _number(station['boiler_fixed_kW'], 'boiler_fixed')
    coefficient = _number(station['boiler_kW_per_kWsteam'], 'boiler_auxiliary_coefficient')
    pump = _number(model['sci_engineering']['cold_pump_kW_per_kW'], 'cold_pump_coefficient')
    expected.update({key: x[38+i] for i, key in enumerate(_STORES)})
    expected['cold_storage_pump_kW'] = pump*(x[40]+x[41])
    if abs(x[43]) > _MATCH_TOL or abs(x[44]) > _MATCH_TOL:
        _unknown('active_recovery_or_GT_start_context_not_fully_exported')
    expected.update(boiler_auxiliary_kW=fixed*x[42]+coefficient*constants['steam']*x[10],
        gt_start_electric_kW=0., boiler_restart_electric_kW=fixed*x[45],
        station_auxiliary_kW=fixed*x[42]+coefficient*constants['steam']*x[10]+fixed*x[45],
        recovery_cooling_kW=0., recovery_pump_electric_kW=0., recovery_fan_electric_kW=0.,
        boiler_station_running=bool(x[42]>.5))
    if set(actual) != set(expected):
        _unknown('unsupported_actual_action_components')
    for key in expected:
        if not _same(expected[key], actual[key]):
            _failed('native_actual_action_mismatch:' + key)
    return served, expected


def _steam(context, segment, proof, row):
    served, action = _action_service(context, segment, proof['x'])
    constants = context['constants']; heat_factor = constants['steam']*constants['exchanger']
    demand = sum(v[1] for v in segment['connected_loads_kW'])/heat_factor
    value = action['hrsg_steam_lbph']+action['boiler_steam_lbph']-action['absorption_cooling_kW']/constants['rt']*constants['abs_steam']-proof['x'][13]
    row_activity = value+sum(proof['x'][15+3*g] for g in range(4))/heat_factor
    for key, expected in (('lower', demand), ('upper', demand), ('activity', row_activity)):
        if abs(_number(row.get(key), 'steam_row_'+key, nonnegative=False)-expected) > _STEAM_TOL:
            _failed('original_steam_row_activity_or_bound_mismatch')
    # Report the fixed actual exported ports after validating the native
    # mapping. Equivalent proof vectors never select one residual secretly.
    actual = segment['action']
    actual_value = actual['hrsg_steam_lbph']+actual['boiler_steam_lbph']-actual['absorption_cooling_kW']/constants['rt']*constants['abs_steam']-proof['x'][13]
    residual = actual_value-sum(v[1] for v in served)/heat_factor
    if abs(residual) > _STEAM_TOL:
        _failed('observable_full_steam_balance_failure')
    if 'steam_balance_residual_lbph' not in segment:
        _unknown('missing_original_steam_residual')
    if abs(residual-_number(segment['steam_balance_residual_lbph'], 'reported_steam_residual', nonnegative=False)) > _MATCH_TOL:
        _failed('native_reported_steam_residual_mismatch')
    return residual


def _segment(context, segment, index):
    start = _number(segment['start_minute'], 'segment_start')
    end = _number(segment['end_minute'], 'segment_end')
    if end <= start:
        _failed('nonpositive_actual_interval')
    calls = context['payload']['solver_calls']
    for call in calls:
        if call.get('phase') in _REPLACEMENTS and call.get('time_minute') == start:
            _unknown('actual_interval_has_unresolved_replacement_solve')
    matches = [(ci, call) for ci, call in enumerate(calls) if call.get('phase') == 'fixed_execution'
        and _number(call.get('time_minute'), 'actual_clock') == start and abs(_number(call.get('dt_h'), 'actual_dt')*60.-(end-start)) <= 1e-12]
    if not matches:
        _unknown('no_matching_actual_fixed_execution')
    proofs = [(ci, proof) for ci, call in matches for proof in _attempts(call)]
    if len({proof['x'][13] for _, proof in proofs}) != 1:
        _unknown('ambiguous_actual_primal_unused_steam')
    waste = proofs[0][1]['x'][13]
    residuals, row_refs = [], []
    for ci, proof in proofs:
        location = [ci, 'solver_backend_layers', proof['layer_index'], 'attempts', proof['attempt_index'], 'validation']
        row, ref = _rows(context, proof, location=location)
        residuals.append(_steam(context, segment, proof, row));row_refs.append(ref)
    cold = segment.get('m24_cold_mode_selection', {}).get('candidates', [])
    cold_matches = [(i, item) for i, item in enumerate(cold) if item.get('status') == 'legal'
        and _same(item.get('action'), segment['action']) and _same(item.get('unserved_kW'), segment['unserved_kW'])]
    if not cold_matches:
        _unknown('no_complete_matching_cold_candidate')
    cold_calls = []
    cold_covered = [False]*len(proofs)
    for candidate_index, candidate in cold_matches:
        ci = candidate.get('solver_call_index')
        if isinstance(ci, bool) or not isinstance(ci, int) or not 0 <= ci < len(calls):
            _unknown('missing_cold_candidate_call_index')
        call = calls[ci]
        if call.get('phase') != 'm24_cold_mode_candidate' or _number(call.get('time_minute'), 'candidate_clock') != start or abs(_number(call.get('dt_h'), 'candidate_dt')*60.-(end-start)) > 1e-12:
            _unknown('cold_candidate_call_not_same_interval')
        candidate_proofs = _attempts(call)
        if any(proof['x'][13] != waste for proof in candidate_proofs):
            _unknown('ambiguous_cold_candidate_unused_steam')
        for proof in candidate_proofs:
            try:
                location = [ci, 'solver_backend_layers', proof['layer_index'], 'attempts', proof['attempt_index'], 'validation']
                row, _ = _rows(context, proof, location=location)
                _steam(context, segment, proof, row)
                for actual_index, (_, actual) in enumerate(proofs):
                    if _same(proof['x'], actual['x']):
                        cold_covered[actual_index] = True
            except _Issue as issue:
                if len(cold_matches) > 1 and issue.status == 'unknown':
                    _unknown('cold_candidates_not_completely_equivalent:' + issue.reason)
                raise
        cold_calls.append(ci)
    if not all(cold_covered):
        _unknown('no_complete_cold_candidate_chain_for_each_actual_primal')
    domains = segment.get('m23_boiler_domains', {}).get('domains', [])
    domain_indices = []
    domain_covered = [False]*len(proofs)
    for di, domain in enumerate(domains):
        if domain.get('status') != 'legal':
            continue
        candidate_proofs = _attempts(domain)
        equivalent = [proof for proof in candidate_proofs if any(_same(proof['x'], actual['x']) for _, actual in proofs)]
        if not equivalent:
            continue
        for proof in equivalent:
            row, _ = _rows(context, proof)
            _steam(context, segment, proof, row)
            for actual_index, (_, actual) in enumerate(proofs):
                if _same(proof['x'], actual['x']):
                    domain_covered[actual_index] = True
        domain_indices.append(di)
    if not domain_indices or not all(domain_covered):
        _unknown('no_complete_matching_legal_boiler_domain')
    equivalent = len(matches)>1 or len(proofs)>1 or len(cold_matches)>1 or len(domain_indices)>1
    return dict(segment_index=index, start_minute=start, end_minute=end, status='passed', reasons=[],
        metered_waste_lbph=waste, metered_waste_lb=waste*(end-start)/60.,
        steam_residual_lbph=residuals[0],
        binding=dict(mode='equivalent' if equivalent else 'unique', selected_unique=not equivalent,
            actual_solver_call_indices=[ci for ci, _ in matches],
            actual_primal_locations=[dict(solver_call_index=ci, layer_index=p['layer_index'], attempt_index=p['attempt_index']) for ci,p in proofs],
            cold_candidate_indices=[i for i,_ in cold_matches], cold_candidate_solver_call_indices=cold_calls,
            boiler_domain_indices=domain_indices, original_row_references=row_refs,
            all_evidence_unused_steam_consistent=True, all_action_and_service_components_checked=True))


def audit_steam_meter(payload, *, bindings, producer_directory, source_root):
    """Audit every actual interval; unknown/failed intervals never get zeroes.

    ``bindings`` is the producer's BINDINGS.json object, and both directories
    are read-only. Replaced solves and unsupported layouts stay unknown.
    ``passed`` means original-model numerical evidence, not physical metering.
    """
    segments = payload.get('segments', []) if isinstance(payload, dict) else []
    results, source_checks = [], {}
    try:
        if not isinstance(payload, dict) or not isinstance(segments, list):
            _failed('invalid_raw_payload_structure')
        context = _context(payload, bindings, Path(producer_directory), Path(source_root))
        source_checks = context['source_hashes']
        previous = _number(payload.get('continuation_start_minute', 0.), 'continuation_start')
        for segment in segments:
            start = _number(segment['start_minute'], 'segment_start')
            end = _number(segment['end_minute'], 'segment_end')
            if end <= start or abs(start-previous) > 1e-9:
                _failed('invalid_actual_interval_coverage')
            previous = end
        if not segments:
            _unknown('no_actual_segments')
        required_endpoints = ((payload.get('summary'), 'requested_end_minute', 'payload_requested_end'),
            (context['result'], 'requested_end_minute', 'producer_requested_end'),
            (context['result'], 'observed_until_minute', 'producer_observed_end'))
        for record, key, name in required_endpoints:
            if not isinstance(record, dict) or key not in record:
                _unknown('missing_endpoint_contract:' + name)
            value = _number(record[key], name)
            if abs(previous-value) > 1e-9:
                _failed('actual_tail_endpoint_mismatch:' + name)
        for index, segment in enumerate(segments):
            try:
                results.append(_segment(context, segment, index))
            except _Issue as issue:
                results.append(dict(segment_index=index, status=issue.status, reasons=[issue.reason],
                    metered_waste_lbph=None, metered_waste_lb=None, steam_residual_lbph=None))
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                results.append(dict(segment_index=index, status='unknown', reasons=['missing_or_unsupported_segment_evidence:'+str(exc)],
                    metered_waste_lbph=None, metered_waste_lb=None, steam_residual_lbph=None))
    except _Issue as issue:
        results = [dict(segment_index=i, status=issue.status, reasons=[issue.reason],
            metered_waste_lbph=None, metered_waste_lb=None, steam_residual_lbph=None) for i in range(len(segments))]
        global_issue = issue
    except (OSError, KeyError, IndexError, TypeError, ValueError, StopIteration) as exc:
        global_issue = _Issue('failed', 'invalid_producer_evidence:'+str(exc))
        results = [dict(segment_index=i, status='failed', reasons=[global_issue.reason],
            metered_waste_lbph=None, metered_waste_lb=None, steam_residual_lbph=None) for i in range(len(segments))]
    else:
        global_issue = None
    statuses = [v['status'] for v in results]
    status = 'failed' if 'failed' in statuses else 'unknown' if not statuses or 'unknown' in statuses else 'passed'
    if global_issue is not None:
        status = global_issue.status
    all_passed = status == 'passed'
    return dict(schema='hospital_ies.engineering_basis.accepted_native_steam_meter.v1', status=status,
        imputed_from_energy_remainder=False, physical_meter_or_OEM_qualification=False,
        reasons=[global_issue.reason] if global_issue else ['no_actual_segments'] if not segments else [],
        source_sha_checks=source_checks, segments=results,
        summary=dict(segment_count=len(results), passed_segments=statuses.count('passed'),
            unknown_segments=statuses.count('unknown'), failed_segments=statuses.count('failed'),
            total_metered_waste_lb=sum(v['metered_waste_lb'] for v in results) if all_passed else None,
            maximum_absolute_steam_residual_lbph=max(abs(v['steam_residual_lbph']) for v in results) if all_passed else None))
