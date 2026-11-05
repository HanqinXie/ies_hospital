"""Read-only accepted-primal steam accounting for original annual journal blocks.

One block, its row sidecar and referenced domain rows are decoded at a time.
The inherited meter's replacement/start/recovery limitations remain explicit.
No environment, optimizer, synthetic discarded flow or producer write is used.
"""
from __future__ import annotations

import ast
from copy import deepcopy
from functools import lru_cache
import gzip
import hashlib
import json
from pathlib import Path
from types import FunctionType

from tools.annual_readiness import journal
from tools.engineering_basis_study import steam_meter as native
from tools.sci_engineering.design import digest
from hospital_ies.state_transition import EPS as _TIME_EPS_MINUTES
from . import flow_source_contracts
from . import compiled_flow_input

SCHEMA = 'hospital_ies.annual_program.accepted_native_block_steam.v1'
SERVICE_BINDING_PROVENANCE = dict(
    source_observer='E:/hospital_ies_engineering_basis_20261029/analysis/auditing_v03/steam_meter_v03.py',
    source_observer_sha256='f3040f97a1331f072837d381615427833aa904f692c634f0d7bfe83d428e9eac',
    source_lines='290-316 and 420-423',
    change='Separate connected unserved primal from active cuts and total segment ENS; verify both original snapshots.',
    copied_old_observer_results=False)
PRIMAL_LAYOUT_PROVENANCE = dict(M25_48=dict(
    physical_source='tools/operating_response/physical_balance.py',
    physical_source_sha256='fe13fe96a58c0f8816a38419074e4a5c12b65e0207b0d37e85a2d8abf82a76f5',
    reserve_source='tools/model_correction_v25/reserve.py',
    reserve_source_sha256='6754608e317f94fae2f40516dbca033c6be869a01edd8cd257ac1753179f3dba',
    source_lines='physical_balance.py:38-42,54-87,283,305-307',
    steam_coordinate=13,auxiliary_coordinates=[26,37],storage_coordinates=[38,41],
    station_coordinates=[42,45],bridge_debt_coordinate=46,
    appended_coordinate=47,appended_coordinate_name='m25.cold.reserve_shortfall_change_rate',
    appended_coordinate_is_discard=False,full_primal_equivalence_includes_appended_coordinate=True))
EXECUTION_SELECTION_SOURCES = {
    'tools/model_closure_v22/environment.py':'3b4d6a3cbe59b9bdffb3db039cc12cb8c361f016fecd93c2512199da5fda03ea',
    'tools/engineering_completion_v24/environment.py':'a45426db0f1c9a205e438a50d209ea6c702ce139a8147b2d787c7fb5dd2916d7',
    'tools/model_correctness_v23/environment.py':'6db840b548680f1ba942c23d4ed825d67a52afa7b39bc6ff2894e29af5de0d30',
    'tools/essential_fixes/execution.py':'0d1bb1161810a49275b03fbd9c8951bb64727ef9b0f12d7658655d3a615d24f4',
    'tools/engineering_completion_v24/cold_modes.py':'6add409d192ec8cc6f3b10b043c72055075a138548f6efe30682d5d10b89c34c'}

# Reviewed full native function AST, including its original unsupported-context
# guard. Only the explicit flow-only path derives a detached pre-guard view.
_FLOW_NATIVE_ACTION_AST_SHA = '21d52d2381d8128d7ea126386cf5e6e53e845f03dc559ed2c8df74dfd47e4cfb'
_FLOW_STEAM_ACTION_KEYS = ('gt_electric_kW','hrsg_steam_lbph',
    'boiler_steam_lbph','absorption_cooling_kW')


@lru_cache(maxsize=8)
def _flow_native_service_prefix_code(source_bytes,filename,expected_ast_sha,include_coefficient):
    """Cache only successfully reviewed pure code under its exact source bytes.

    Errors/unknowns raise and are never cached. Neither native globals nor any
    service inputs, primal vectors, contexts or audit results enter this cache.
    """
    try:
        tree=ast.parse(source_bytes.decode('utf8'))
    except (OSError,UnicodeError,SyntaxError):
        native._unknown('unsupported_native_steam_flow_prefix_version')
    functions=[node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name=='_action_service']
    if len(functions)!=1:
        native._unknown('unsupported_native_steam_flow_prefix_version')
    function=functions[0]
    signature=hashlib.sha256(ast.dump(function,include_attributes=False).encode('utf8')).hexdigest()
    if signature!=expected_ast_sha:
        native._unknown('unsupported_native_steam_flow_prefix_version')
    guard=ast.parse("if abs(x[43]) > _MATCH_TOL or abs(x[44]) > _MATCH_TOL:\n"
        "    _unknown('active_recovery_or_GT_start_context_not_fully_exported')").body[0]
    guards=[i for i,node in enumerate(function.body)
        if ast.dump(node,include_attributes=False)==ast.dump(guard,include_attributes=False)]
    if len(guards)!=1:
        native._unknown('unsupported_native_steam_flow_prefix_version')
    function.name='_annual_steam_flow_service_prefix'
    returned='return served, expected, steam_ratio' if include_coefficient else 'return served, expected'
    function.body=function.body[:guards[0]]+ast.parse(returned).body
    detached=ast.fix_missing_locations(ast.Module(body=[function],type_ignores=[]))
    return compile(detached,filename+'::<steam-flow-prefix>','exec')


def _flow_native_service_prefix(*,include_coefficient=False):
    """Reuse unchanged native service/ambient/port calculations, not its gate.

    Every invocation rereads the full native source bytes. A bounded cache key
    includes those exact bytes, filename, reviewed AST pin and return mode, so
    even same-size/same-mtime edits must pass the original AST and guard checks.
    Only compiled code is reused; exec uses a fresh current native namespace.
    No module globals or original source/code objects are modified, no raw
    coordinates are substituted, and no result/action matching selects proof.
    """
    filename=str(native.__file__)
    try:
        source_bytes=Path(filename).read_bytes()
    except OSError:
        native._unknown('unsupported_native_steam_flow_prefix_version')
    code=_flow_native_service_prefix_code(source_bytes,filename,
        _FLOW_NATIVE_ACTION_AST_SHA,include_coefficient)
    namespace=dict(native.__dict__)
    exec(code,namespace)
    return namespace['_annual_steam_flow_service_prefix']


def _steam_flow_action_service(context,segment,x):
    """Steam algebra only; no starter eligibility/electric/recovery claim."""
    if x[43]!=0.:
        native._unknown('steam_flow_only_requires_zero_recovery')
    if x[44] not in (0.,1.):
        native._unknown('steam_flow_only_requires_exact_starter_binary')
    served,expected=_flow_native_service_prefix()(context,segment,x)
    for key in _FLOW_STEAM_ACTION_KEYS:
        if not native._same(expected[key],segment['action'][key]):
            native._failed('native_actual_action_mismatch:'+key)
    return served,expected


def _steam_flow_with_actual_cuts(context,segment,x):
    return _service_with_actual_cuts(context,segment,x,action_service=_steam_flow_action_service)


def _read(path):
    with Path(path).open('rt', encoding='utf-8-sig') as stream:
        return native._json(stream)


def _fingerprint(path):
    row = Path(path).stat()
    return row.st_size, row.st_mtime_ns


def prepare_steam_context(producer_directory, *, source_root, verified_journal=None):
    """Bind immutable producer/source metadata once, before streaming blocks.

    ``verified_journal`` may be the existing ``journal.verify_journal`` result;
    it must match the on-disk closed summary. Omitting it runs that original
    verifier. Call ``verify_steam_context`` after consuming the stream for a
    final full SHA check; each block also checks metadata file fingerprints.
    """
    directory, source = Path(producer_directory).resolve(), Path(source_root).resolve()
    evidence = directory/'evidence'
    verified = journal.verify_journal(evidence) if verified_journal is None else verified_journal
    summary = _read(evidence/'SUMMARY.json')
    if not isinstance(verified, dict) or verified.get('integrity_verified') is not True:
        raise ValueError('Original journal verification is required')
    for key in ('identity', 'identity_sha256', 'block_count', 'last_digest', 'raw_files_sha256',
                'segment_count', 'solver_call_count', 'event_count', 'carrier_service', 'storage', 'terminal'):
        if verified.get(key) != summary.get(key):
            raise ValueError('Supplied journal verification differs from closed producer summary: '+key)
    bindings, result = _read(directory/'BINDINGS.json'), _read(directory/'RESULT.json')
    case = _read(directory/'CASE.json')['case']
    model = _read(directory/'RESOLVED_CASE.json')['model_config']
    if not isinstance(bindings.get('source'), dict) or not bindings['source']:
        raise ValueError('Missing producer source bindings')
    source_hashes, protected = {}, {}
    for relative in native.SOURCE_FILES:
        if relative not in bindings['source']:
            raise ValueError('Missing original steam source binding: '+relative)
    for group in ('source', 'inputs'):
        for relative, expected in bindings.get(group, {}).items():
            path = native._child(source, relative)
            if not path.is_file() or native._digest(path) != expected:
                raise ValueError('Frozen source/input SHA mismatch: '+relative)
            source_hashes[relative] = expected
            protected[str(path)] = dict(sha256=expected, fingerprint=_fingerprint(path))
    identity = verified['identity']
    expected = dict(case_id=case['id'], case_sha256=digest(case),
        source_sha256=digest(bindings['source']), input_sha256=digest(bindings.get('inputs', {})),
        git_commit=bindings.get('git_commit'))
    for key, value in expected.items():
        if value is not None and identity.get(key) != value:
            raise ValueError('Journal identity differs from producer bindings: '+key)
        # Original runner RESULT stores the input mapping, whereas its journal
        # identity stores digest(mapping). Preserve both original representations.
        observed = result.get(key)
        if key=='input_sha256' and isinstance(observed,dict):
            observed=digest(observed)
        if key in result and observed != identity.get(key):
            raise ValueError('Producer result differs from journal identity: '+key)
    observation = summary['observation']
    for name in ('requested_end_minute', 'observed_until_minute', 'unknown_tail_minutes'):
        if result.get(name) != observation.get(name):
            raise ValueError('Producer/journal endpoint mismatch: '+name)
    if native._digest(evidence/'IDENTITY.json') != verified['identity_sha256']:
        raise ValueError('Original journal identity file SHA mismatch')
    # Read the small index, not raw histories. Preserve exact original per-call
    # owners: retained mode uses original list indices, bounded mode resets.
    records, cursor, previous = {}, 0, None
    with (evidence/'blocks.jsonl').open('rt', encoding='utf8') as stream:
        for index, line in enumerate(stream):
            if not line.endswith('\n'):
                raise ValueError('Incomplete journal index record')
            record = json.loads(line)
            name = 'blocks/block_'+format(index, '06d')+'.json.gz'
            expected_raw = dict(file=name, sha256=record.get('sha256'))
            if (record.get('index') != index or record.get('file') != name or
                    record.get('previous_digest') != previous or
                    record.get('identity_sha256') != verified['identity_sha256'] or
                    record.get('digest') != journal._record_digest(record) or
                    index >= len(verified['raw_files_sha256']) or
                    verified['raw_files_sha256'][index] != expected_raw):
                raise ValueError('Journal block index identity/hash chain mismatch')
            record['expected_solver_index_base'] = cursor
            records[name] = record
            cursor += record['solver_call_count']
            previous = record['digest']
    if len(records) != verified['block_count'] or previous != verified['last_digest']:
        raise ValueError('Journal index does not cover closed manifest')
    physical = ast.parse((source/native.SOURCE_FILES[0]).read_text(encoding='utf8'))
    coordinates = {node.targets[0].id:node.value.value for node in ast.walk(physical)
        if isinstance(node, ast.Assign) and len(node.targets)==1 and isinstance(node.targets[0],ast.Name)
        and isinstance(node.value,ast.Constant) and node.targets[0].id in ('ia','ib','ig','idg','iw','iu')}
    if any(coordinates.get(k) != v for k,v in dict(ia=6,ib=10,ig=11,idg=12,iw=13,iu=14).items()):
        raise ValueError('Unsupported producer coordinate layout')
    config = _read(source/'configs/plant_configs.json')
    model_ast = ast.parse((source/'src/hospital_ies/model.py').read_text(encoding='utf8'))
    rt = next(ast.literal_eval(node.value) for node in model_ast.body if isinstance(node,ast.Assign)
        and any(isinstance(target,ast.Name) and target.id=='RT_TO_KW' for target in node.targets))
    assumptions, performance = config['engineering_assumptions_not_from_source'], config['historical_pnnl_performance']
    constants = dict(rt=rt, steam=assumptions['kWh_thermal_per_lb_steam'],
        exchanger=assumptions['steam_to_building_heat_exchanger_efficiency'],
        abs_steam=performance['existing_absorption_steam_lb_per_RT_hr'],
        ec_electric=performance['existing_electric_chiller_all_in_kW_per_RT'])
    for name,value in constants.items():
        native._number(value, name)
    if min(constants[k] for k in ('rt','steam','exchanger')) <= 0.:
        raise ValueError('Nonpositive steam conversion')
    for name in ('BINDINGS.json','RESULT.json','CASE.json','RESOLVED_CASE.json',
                 'evidence/IDENTITY.json','evidence/SUMMARY.json','evidence/blocks.jsonl'):
        path = directory/name
        protected[str(path)] = dict(sha256=native._digest(path),fingerprint=_fingerprint(path))
    return dict(directory=directory,source_root=source,bindings=bindings,result=result,model_config=model,
        constants=constants,config=config,source_hashes=source_hashes,protected=protected,
        records=records,raw_files_sha256=deepcopy(verified['raw_files_sha256']),
        observation=deepcopy(observation),service_binding_provenance=deepcopy(SERVICE_BINDING_PROVENANCE))


def verify_steam_context(context, *, full=True):
    """Check all initial source and producer SHA values without retaining data."""
    for name, record in context['protected'].items():
        path = Path(name)
        if not path.is_file():
            native._failed('source_or_producer_file_missing:'+path.name)
        if full or _fingerprint(path) != record['fingerprint']:
            if native._digest(path) != record['sha256']:
                native._failed('source_or_producer_sha_drift:'+path.name)
    return True


def _service_with_actual_cuts(context, segment, x, *, action_service=None):
    """V03 identity fix; inherited native action/auxiliary mapping is unchanged."""
    ledger = segment.get('service_request_ledger')
    if not isinstance(ledger,dict):
        native._unknown('missing_actual_service_ledger')
    loaded = native._matrix(segment['connected_loads_kW'],'connected_loads')
    original = native._matrix(ledger['original_demand_kW'],'original_request')
    cut = native._matrix(ledger['active_cut_kW'],'active_cut')
    connected = native._matrix(ledger['connected_unserved_kW'],'connected_unserved')
    total = native._matrix(segment.get('_annual_original_total_unserved_kW',segment['unserved_kW']),'total_unserved')
    if not native._same(total, [[connected[g][k]+cut[g][k] for k in range(3)] for g in range(4)]):
        native._failed('total_unserved_active_cut_mismatch')
    if (not native._same(cut,native._matrix(segment['disconnected_loads_kW'],'actual_disconnected')) or
            not native._same(original,native._matrix(segment['loads_kW'],'original_load_snapshot'))):
        native._failed('actual_cut_or_original_request_snapshot_mismatch')
    delivered = ledger['delivered_kW']
    if (not native._same(original,[[loaded[g][k]+cut[g][k] for k in range(3)] for g in range(4)]) or
            not native._same(delivered,[[original[g][k]-total[g][k] for k in range(3)] for g in range(4)])):
        native._failed('actual_service_ledger_mismatch')
    detached = dict(segment,unserved_kW=connected)
    return (native._action_service if action_service is None else action_service)(context,detached,x)


def _annual_rows(context, proof, *, location=None):
    ref = proof['attempt']['validation'].get('rows_ref')
    if ref and ref.get('schema') == 'hospital_ies.solver_validation_rows.reference.v1':
        if ref.get('file') != context['sidecar_basename']:
            native._failed('wrong_solver_sidecar_file')
        # The inherited verifier's filename is fixed. Only this detached lookup
        # alias changes; original references and bytes are returned unchanged.
        altered = dict(proof)
        altered['attempt'] = dict(proof['attempt'])
        altered['attempt']['validation'] = dict(proof['attempt']['validation'],
            rows_ref=dict(ref,file='solver_rows.jsonl.gz'))
        row,_ = native._rows(context,altered,location=location)
        return row,ref
    return native._rows(context,proof,location=location)


def _annual_attempts(call, *, allow_m25_48):
    """Reuse original acceptance checks while preserving every M25 coordinate.

    The inherited validator's 47-size gate is satisfied only in a detached
    *validation view*, after the exact original 48-layout was established.
    All returned proof vectors/attempts and candidate equivalence use full48.
    """
    layers=call.get('solver_backend_layers') or []
    if not layers:
        return native._attempts(call)
    attempts=layers[-1].get('attempts',[])
    view=[]; full={}
    for index,attempt in enumerate(attempts):
        x=attempt.get('raw_x')
        if attempt.get('accepted') is True and attempt.get('status')==0 and isinstance(x,list) and len(x)==48:
            if not allow_m25_48:
                native._unknown('unbound_original_M25_48_coordinate_layout')
            if attempt.get('validation',{}).get('integer_indices') != [3,4,5,42,44,45]:
                native._unknown('unsupported_48_coordinate_integer_layout')
            full[index]=[native._number(v,'raw_x',nonnegative=False) for v in x]
            view.append(dict(attempt,raw_x=x[:47]))
        else:
            view.append(attempt)
    altered=dict(call,solver_backend_layers=layers[:-1]+[dict(layers[-1],attempts=view)])
    proofs=native._attempts(altered)
    for proof in proofs:
        index=proof['attempt_index']
        if index in full:
            proof['x']=full[index]
            proof['attempt']=attempts[index]
    return proofs


def _segment_auditor(*, allow_m25_48, final_result_selection_bound=False):
    # Isolated globals avoid mutating any shared native module or another run.
    namespace=dict(native.__dict__,_action_service=_service_with_actual_cuts,_rows=_annual_rows,
        _attempts=lambda call:_annual_attempts(call,allow_m25_48=allow_m25_48))
    if final_result_selection_bound:
        namespace['_REPLACEMENTS']=frozenset()
    namespace['_steam']=FunctionType(native._steam.__code__,namespace,native._steam.__name__)
    return FunctionType(native._segment.__code__,namespace,native._segment.__name__)


def _execution_selection(context, block, segment):
    """Resolve producer-recorded final result IDs, never an action-only match.

    M22 records result.m22_mode_selection on its solve record; the exact
    M24 hook copies that same result selection to the committed segment.
    M24's ordered candidate IDs are freshly allocated for each selection.
    The pinned execution body passes interval ``sol`` to that hook, while
    the later endpoint result is a separate ``terminal`` variable.
    """
    if any(context['source_hashes'].get(name)!=value for name,value in EXECUTION_SELECTION_SOURCES.items()):
        return None
    selection=segment.get('m24_cold_mode_selection')
    if (not isinstance(selection,dict) or not selection.get('candidates') or
            journal._json(selection)!=journal._json(segment.get('m22_mode_selection'))):
        return None
    matches=[]
    phases={'fixed_execution','after_actual_shedding','local_event_split','protection_threshold_split',
        'm23_event_power_probe','diesel_domain_split'}
    for index,call in enumerate(block['solver_calls']):
        if (call.get('phase') not in phases or call.get('time_minute')!=segment['start_minute'] or
                not journal._number(call.get('dt_h')) or
                abs(call['dt_h']*60.-(segment['end_minute']-segment['start_minute']))>1e-12):
            continue
        if journal._json(call.get('m24_cold_mode_selection'))==journal._json(selection):
            matches.append(index)
    if len(matches)!=1:
        return None
    index=matches[0]
    return dict(mode='producer_final_result_candidate_ids',actual_block_call_index=index,
        actual_global_call_index=block['solver_index_base']+index,
        original_phase=block['solver_calls'][index]['phase'],
        original_candidate_call_indices=[row.get('solver_call_index') for row in selection['candidates']],
        numerical_match_used_to_select_actual_call=False,
        source_sha256=deepcopy(EXECUTION_SELECTION_SOURCES),
        source_lines='M22 environment.py:253-259; M24 environment.py:46-50; cold_modes.py:77-90; essential_fixes/execution.py:361-365,420-421')


def _physical_formula_profile(context):
    """Admit only the unchanged leaf or the exact approved logging erasure.

    No leaf function is imported/executed. Formula namespaces below use only
    the separately pinned, pure native meter prefix, never the capture scope.
    """
    leaf='tools/operating_response/physical_balance.py'
    if context['source_hashes'].get(leaf)==PRIMAL_LAYOUT_PROVENANCE['M25_48']['physical_source_sha256']:
        return flow_source_contracts.LEGACY_PROFILE
    observed={name:context['source_hashes'].get(name) for name in flow_source_contracts.LEGACY_NATIVE_SOURCE_CONTRACT}
    recognized=flow_source_contracts.recognized_capture_native_source_contract(observed)
    if recognized is None:
        native._unknown('unsupported_annual_physical_source_profile')
    extras=flow_source_contracts.CAPTURE_AUXILIARY_SOURCE_CONTRACT
    if not isinstance(extras,dict):native._unknown('unapproved_compiled_flow_source_helpers')
    additional=flow_source_contracts.capture_source_extra_contract(observed)
    if additional is None:native._unknown('unapproved_compiled_flow_source_helpers')
    expected=dict(recognized,**extras,**additional);root=Path(context['source_root']).resolve()
    for name,pin in expected.items():
        if context['source_hashes'].get(name)!=pin:
            native._failed('compiled_flow_source_bytes_or_binding_mismatch:'+name)
    key=(str(root),tuple(sorted(expected.items())),flow_source_contracts.CAPTURE_RECORDER_SHA256,
         flow_source_contracts.CAPTURE_LOGGING_EXPRESSION_AST_SHA256)
    if context.get('_compiled_formula_profile_key')==key:return flow_source_contracts.CAPTURE_PROFILE
    for name,pin in expected.items():
        path=native._child(root,name)
        if not path.is_file() or native._digest(path)!=pin:
            native._failed('compiled_flow_source_bytes_or_binding_mismatch:'+name)
    migration=flow_source_contracts.inspect_leaf_migration(native._child(root,leaf).read_bytes())
    if migration['runtime_profile_approved'] is not True:
        native._unknown('unapproved_compiled_flow_leaf_migration')
    context['_compiled_formula_profile_key']=key
    return flow_source_contracts.CAPTURE_PROFILE


def _block_context(context, block, record):
    verify_steam_context(context,full=False)
    path = native._child(context['directory']/'evidence',record['file'])
    if native._digest(path) != record['sha256']:
        native._failed('raw_block_sha_mismatch')
    raw = native._read(path,zipped=True)
    if journal._json(raw) != journal._json(block):
        native._failed('supplied_block_differs_from_bound_raw')
    del raw
    native._finite_tree(block)
    journal._validate_block(block,record['start_minute'])
    formula_profile=_physical_formula_profile(context)
    if (block['end_minute'] != record['end_minute'] or len(block['segments']) != record['segment_count'] or
            len(block['solver_calls']) != record['solver_call_count']):
        native._failed('raw_block_clock_or_count_mismatch')
    base = block.get('solver_index_base')
    start = block.get('solver_call_local_start_index')
    if any(isinstance(v,bool) or not isinstance(v,int) or v < 0 for v in (base,start)):
        native._failed('invalid_solver_index_base_or_offset')
    if base != record['expected_solver_index_base']:
        native._failed('solver_index_base_not_cumulative_original_count')
    semantics = block.get('solver_index_semantics')
    if ((semantics=='global original list index' and start != base) or
            (semantics=='block local; combine with solver_index_base' and start != 0) or
            semantics not in ('global original list index','block local; combine with solver_index_base')):
        native._failed('solver_index_semantics_or_offset_mismatch')
    receipt = block.get('solver_row_sidecar')
    if (not isinstance(receipt,dict) or receipt.get('closed') is not True or receipt.get('status') != 'completed'
            or receipt.get('partial_tail_possible') is not False):
        native._unknown('incomplete_solver_row_sidecar')
    if receipt not in context['result'].get('solver_row_sidecars',[]):
        native._failed('block_sidecar_receipt_not_bound_to_producer_result')
    side_path = Path(receipt.get('path','')).resolve()
    if not side_path.is_relative_to(context['directory']) or side_path.parent != context['directory']:
        native._failed('solver_sidecar_path_escapes_producer')
    if not side_path.is_file():
        native._unknown('missing_solver_row_sidecar')
    if native._digest(side_path) != receipt.get('gzip_sha256'):
        native._failed('solver_sidecar_gzip_sha_mismatch')
    entries, row_count = {},0
    with gzip.open(side_path,'rt',encoding='utf8') as stream:
        for line_number,line in enumerate(stream,1):
            if not line.endswith('\n'):
                native._failed('truncated_solver_sidecar')
            entry = json.loads(line)
            native._finite_tree(entry)
            ident, rows = entry.get('record_id'),entry['rows']
            if (not ident or ident in entries or
                    entry.get('schema') != 'hospital_ies.solver_validation_rows.sidecar.v1' or
                    len(rows) != entry.get('row_count') or native._canonical_sha(rows) != entry.get('rows_sha256')):
                native._failed('solver_sidecar_identity_rows_sha_or_count_mismatch')
            location = deepcopy(entry.get('location'))
            if not isinstance(location,list) or not location:
                native._unknown('missing_solver_sidecar_location')
            owner = location[0]
            if isinstance(owner,bool) or not isinstance(owner,int) or not start <= owner < start+len(block['solver_calls']):
                native._failed('solver_sidecar_location_outside_original_block_calls')
            location[0] -= start
            balance_names=('balance.electric','balance.steam','balance.cooling') if formula_profile==flow_source_contracts.CAPTURE_PROFILE else ('balance.steam',)
            entries[ident] = dict(entry,rows=[r for r in rows if r.get('name') in balance_names],
                location=location,line=line_number)
            row_count += len(rows)
    if len(entries) != receipt.get('entry_count') or row_count != receipt.get('row_count'):
        native._failed('solver_sidecar_receipt_count_mismatch')
    domain = {}
    for name,row in block.get('domain_evidence_files',{}).items():
        domain_path = native._child(context['directory'],name)
        if not domain_path.is_relative_to((context['directory']/'domain_evidence').resolve()):
            native._failed('domain_evidence_path_escapes_producer')
        if not domain_path.is_file():
            native._unknown('missing_domain_row_blob')
        if native._digest(domain_path) != row.get('gzip_sha256'):
            native._failed('domain_blob_gzip_sha_mismatch')
        domain[name] = dict(sha256=row['gzip_sha256'])
    detached = deepcopy(block['segments'])
    for segment in detached:
        for candidate in (segment.get('m24_cold_mode_selection') or {}).get('candidates',[]):
            owner = candidate.get('solver_call_index')
            if owner is None:
                continue  # Inherited meter records missing proof as unknown.
            if isinstance(owner,bool) or not isinstance(owner,int) or not start <= owner < start+len(block['solver_calls']):
                native._failed('cold_candidate_call_index_outside_original_block')
            candidate['solver_call_index'] = owner-start
    observation = block['decision'].get('current_observation')
    if not isinstance(observation,dict):
        native._unknown('missing_current_ambient_observation')
    layout=PRIMAL_LAYOUT_PROVENANCE['M25_48']
    allow_m25_48=(formula_profile in (flow_source_contracts.LEGACY_PROFILE,flow_source_contracts.CAPTURE_PROFILE)
        and context['source_hashes'].get(layout['reserve_source'])==layout['reserve_source_sha256']
        and all((segment.get('m25_reserve') or {}).get('control_rule',{}).get('name')=='sustained_critical'
            for segment in detached))
    return dict(directory=context['directory'],config=context['config'],constants=context['constants'],
        allow_m25_48=allow_m25_48,
        result=dict(domain_evidence=dict(files=domain)), entries=entries,cas_cache={},
        sidecar_basename=side_path.name,payload=dict(segments=detached,solver_calls=block['solver_calls'],
            decisions=[observation],model_config=context['model_config']))


def _actual_source_pair(context,source,bound_recorder):
    """Authenticate declared native bytes through the producer's verified map.

    The registered recorder/source pair, not the running observer file hash,
    controls compatibility with immutable historical witnesses.
    """
    if not isinstance(source,dict) or source.get('supported') is not True:
        native._failed('actual_witness_native_source_contract_mismatch')
    observed=source.get('source_sha256')
    if (not isinstance(observed,dict)
            or set(observed)!=set(flow_source_contracts.LEGACY_NATIVE_SOURCE_CONTRACT)):
        native._failed('actual_witness_native_source_contract_mismatch')
    for relative,declared in observed.items():
        if relative not in context['source_hashes']:
            native._unknown('actual_witness_missing_native_source:'+relative)
        if context['source_hashes'][relative]!=declared:
            native._failed('actual_witness_producer_native_source_mismatch:'+relative)
    pair=flow_source_contracts.classify_recorder_source_pair(bound_recorder,observed)
    if not pair['supported']:
        if pair['source_profile'] is None:native._unknown('unsupported_actual_witness_native_source_profile')
        native._unknown('unsupported_actual_witness_recorder_version')
    if 'source_profile' in source and source['source_profile']!=pair['source_profile']:
        native._failed('actual_witness_native_source_profile_label_mismatch')
    return pair


def _compiled_balance_rows(context,local,segment,x,payload,rows):
    """Compare every coefficient/bound/activity, including inactive columns."""
    closure=payload['closure'];ports=payload['storage_ports'];coeff=payload['port_coefficients']
    if closure is None or ports is None:native._unknown('compiled_flow_requires_explicit_closure_and_storage')
    constants=context['constants'];performance=context['config']['historical_pnnl_performance']
    expected_coeff=dict(RT_TO_KW=constants['rt'],abs_electric_kW_per_rt=performance['existing_absorption_aux_kW_per_RT'],
        ec_electric_kW_per_rt=constants['ec_electric'],abs_steam_lb_per_rt=constants['abs_steam'],
        steam_kWh_per_lb=constants['steam'],exchanger_eff=constants['exchanger'],
        HRSG_availability={key:segment['availability'][key] for key in ('HRSG_1','HRSG_2','HRSG_3')})
    _,_,ratio=_service_with_actual_cuts(local,segment,x,action_service=_flow_native_service_prefix(include_coefficient=True))
    expected_coeff['steam_per_kW']=ratio
    if not native._same(coeff,expected_coeff):native._failed('compiled_flow_port_coefficients_mismatch')
    station=context['model_config']['model_closure_v22']['station']
    for key in ('boiler_fixed_kW','boiler_kW_per_kWsteam','gt_starter_kW_per_unit'):
        if key in station and not native._same(closure[key],station[key]):
            native._failed('compiled_flow_static_closure_mismatch:'+key)
    pump=context['model_config']['sci_engineering']['cold_pump_kW_per_kW']
    if not native._same(ports['cold_pump_kW_per_kW'],pump):native._failed('compiled_flow_cold_pump_coefficient_mismatch')
    n=payload['nvars'];matrix=[[0.]*n for _ in range(3)]
    for i in range(3):matrix[0][i]=1.;matrix[1][i]=ratio*coeff['HRSG_availability']['HRSG_'+str(i+1)]
    matrix[0][6]=-coeff['abs_electric_kW_per_rt']
    for i in range(7,10):matrix[0][i]=-coeff['ec_electric_kW_per_rt']
    matrix[0][11]=matrix[0][12]=matrix[0][38]=1.;matrix[0][39]=-1.
    matrix[0][40]=matrix[0][41]=-pump
    matrix[0][10]=-closure['boiler_kW_per_kWsteam']*coeff['steam_kWh_per_lb']
    matrix[0][42]=-closure['boiler_fixed_kW'];matrix[0][44]=-closure['gt_start_kW']
    matrix[0][45]=-closure['boiler_restart_kW']
    matrix[0][43]=-(closure['recovery_pump_kW_per_kW']+closure['recovery_fan_kW_per_kW'])
    matrix[1][6]=-coeff['abs_steam_lb_per_rt'];matrix[1][10]=1.;matrix[1][13]=-1.
    for i in range(6,10):matrix[2][i]=coeff['RT_TO_KW']
    matrix[2][40]=1.;matrix[2][41]=matrix[2][43]=-1.
    for g in range(4):
        matrix[0][14+3*g]=1.;matrix[1][15+3*g]=1./(coeff['steam_kWh_per_lb']*coeff['exchanger_eff']);matrix[2][16+3*g]=1.
    rhs=[sum(row[0] for row in segment['connected_loads_kW']),
         sum(row[1] for row in segment['connected_loads_kW'])/(coeff['steam_kWh_per_lb']*coeff['exchanger_eff']),
         sum(row[2] for row in segment['connected_loads_kW'])]
    balance=payload['balance_rows']
    if not native._same(balance['Aeq'],matrix):native._failed('compiled_flow_original_Aeq_mismatch')
    if not native._same(balance['beq'],rhs):native._failed('compiled_flow_original_beq_mismatch')
    for index,(name,unit) in enumerate((('balance.electric','kW'),('balance.steam','lb/h'),('balance.cooling','kW'))):
        found=[row for row in rows if row.get('name')==name]
        if len(found)!=1:native._unknown('missing_unique_original_compiled_row:'+name)
        row=found[0]
        if row.get('unit')!=unit or row.get('kind')!='physical':native._failed('wrong_original_compiled_row_kind_or_unit:'+name)
        activity=sum(a*b for a,b in zip(matrix[index],x))
        for field,expected in (('lower',rhs[index]),('upper',rhs[index]),('activity',activity)):
            if not native._same(native._number(row.get(field),name+'.'+field,nonnegative=False),expected):
                native._failed('compiled_flow_owned_row_mismatch:'+name+'.'+field)
        if native._number(row.get('violation'),name+'.violation')>1e-7:
            native._failed('compiled_flow_owned_row_violation:'+name)


def _compiled_flow_packet(context,local,block,segment,proof,metadata,receipt,ci,pair):
    """Verify a final packet independently of its source-verified assertions."""
    packet=metadata.get('compiled_flow_input');binding=metadata.get('compiled_flow_binding')
    if any(key in metadata for key in ('_compiled_flow_input','compiled_flow_context')):
        native._unknown('unsupported_future_compiled_flow_context_schema')
    if packet is None:
        if receipt.get('compiled_flow_input_sha256') is not None:native._failed('compiled_flow_receipt_hash_without_packet')
        if binding is not None and (not isinstance(binding,dict) or binding.get('status')!='unknown'
                or binding.get('payload_content_sha256') is not None):native._failed('contradictory_missing_compiled_flow_packet')
        return None
    if not pair['compiled_source_profile_allowed']:
        native._unknown('unsupported_future_compiled_flow_context_schema')
    if not isinstance(packet,dict) or packet.get('schema')!=compiled_flow_input.SCHEMA:
        native._unknown('unsupported_future_compiled_flow_context_schema')
    fields={'schema','status','reasons','exact_final_result_argument','payload_content_sha256','source_profile',
            'source_root','source_files','leaf_function','actual_hook'}
    if (not isinstance(binding,dict) or set(binding)!=fields
            or binding.get('schema')!='hospital_ies.annual_program.final_compiled_flow_binding.v1'):
        native._unknown('unsupported_compiled_flow_binding_schema')
    if (binding['status']!='recorded' or binding['reasons']!=[] or binding['exact_final_result_argument'] is not True
            or binding['source_profile']!=flow_source_contracts.CAPTURE_PROFILE):native._failed('invalid_compiled_flow_binding_flags')
    try:payload=compiled_flow_input.validate_compiled_flow_input(packet,raw_x=proof['attempt']['raw_x'])
    except (KeyError,TypeError,ValueError) as exc:native._failed('invalid_compiled_flow_input:'+str(exc))
    if (binding['payload_content_sha256']!=payload['content_sha256']
            or receipt.get('compiled_flow_input_sha256')!=payload['content_sha256']):native._failed('compiled_flow_payload_receipt_sha_mismatch')
    if abs(payload['dt_h']*60.-(segment['end_minute']-segment['start_minute']))>_TIME_EPS_MINUTES:
        native._failed('compiled_flow_actual_interval_dt_mismatch')
    _physical_formula_profile(context)
    observed={name:context['source_hashes'].get(name) for name in flow_source_contracts.LEGACY_NATIVE_SOURCE_CONTRACT}
    recognized=flow_source_contracts.recognized_capture_native_source_contract(observed)
    if recognized is None:native._unknown('unsupported_annual_physical_source_profile')
    expected=dict(recognized,**flow_source_contracts.CAPTURE_AUXILIARY_SOURCE_CONTRACT)
    root=str(Path(context['source_root']).resolve());origin=payload['source_origin']
    wanted=dict(schema=compiled_flow_input.SOURCE_SCHEMA,source_root=root,source_files=expected,
        leaf_function='tools.operating_response.physical_balance.solve_physical')
    if (origin!=wanted or binding['source_root']!=root or binding['source_files']!=expected
            or binding['leaf_function']!=wanted['leaf_function']):native._failed('compiled_flow_source_origin_or_binding_mismatch')
    from . import witness
    hook='tools/annual_program/source_runtime.py';hook_pin=witness.ACTUAL_HOOK_SOURCE_CONTRACT.get(hook)
    if (binding['actual_hook']!=dict(file=hook,function='AnnualSourceMixin._after_actual_segment',sha256=hook_pin)
            or context['source_hashes'].get(hook)!=hook_pin
            or native._digest(native._child(Path(context['source_root']),hook))!=hook_pin):native._failed('compiled_flow_actual_hook_source_mismatch')
    validator=flow_source_contracts.CAPTURE_AUXILIARY_SOURCE_CONTRACT['tools/annual_program/compiled_flow_input.py']
    if native._digest(compiled_flow_input.__file__)!=validator:native._unknown('unsupported_compiled_flow_validator_version')
    ref=proof['attempt']['validation'].get('rows_ref')
    entry=local['entries'].get(ref.get('record_id')) if isinstance(ref,dict) else None
    expected_location=[ci,'solver_backend_layers',0,'attempts',proof['attempt_index'],'validation']
    if entry is None or entry.get('location')!=expected_location:native._failed('compiled_flow_owned_row_location_mismatch')
    _compiled_balance_rows(context,local,segment,proof['x'],payload,entry['rows'])
    return payload


def _actual_witness_segment(context,local,block,segment,index,*,steam_flow_only=False):
    """Validate an explicitly opted-in, source-bound final-result log receipt.

    The new recorder owns this call's rows and full primal. Alternative cold
    candidates do not select or overwrite the actual result. Legacy callers
    never enter this resolver, including all frozen historical reports.
    """
    from . import witness
    receipt=segment[witness.FIELD]
    if not isinstance(receipt,dict) or receipt.get('schema')!=witness.SCHEMA:
        native._failed('invalid_actual_witness_schema')
    if receipt.get('status')!='recorded':
        native._unknown('actual_witness_unavailable:'+','.join(map(str,receipt.get('reasons',[]))))
    if (receipt.get('reasons')!=[] or any(receipt.get(key) is not False for key in
            ('primal_selected_by_action_matching','discard_imputed_from_remainder','physical_dynamics_modified'))):
        native._failed('actual_witness_recorded_flags_contradict_contract')
    bound=context['source_hashes'].get('tools/annual_program/witness.py')
    if not bound:native._unknown('actual_witness_recorder_source_not_bound')
    if receipt.get('recorder_source_sha256')!=bound:
        native._failed('actual_witness_recorder_sha_mismatch')
    owner=receipt.get('solver_call_index');start=block['solver_call_local_start_index']
    if isinstance(owner,bool) or not isinstance(owner,int) or not start<=owner<start+len(block['solver_calls']):
        native._failed('actual_witness_call_index_outside_original_block')
    ci=owner-start;call=block['solver_calls'][ci]
    if call.get('phase')!=witness.PHASE or call.get('status')!='success':
        native._failed('actual_witness_call_is_not_a_successful_witness')
    a=native._number(segment['start_minute'],'actual_start');b=native._number(segment['end_minute'],'actual_end')
    receipt_start=native._number(receipt.get('start_minute'),'witness_start')
    receipt_end=native._number(receipt.get('end_minute'),'witness_end')
    call_start=native._number(call.get('time_minute'),'witness_call_start')
    call_dt=native._number(call.get('dt_h'),'witness_call_dt')
    if (receipt_start!=a or receipt_end!=b or call_start!=a or abs(call_dt*60.-(b-a))>1e-12):
        native._failed('actual_witness_interval_mismatch')
    metadata=call.get('annual_actual_witness')
    if not isinstance(metadata,dict) or metadata.get('schema')!=witness.SCHEMA or metadata.get('scope')!='final_actual_interval_result':
        native._failed('actual_witness_log_scope_mismatch')
    pair=_actual_source_pair(context,metadata.get('original_native_source_binding'),bound)
    if metadata.get('recorder_source_sha256')!=bound:native._failed('actual_witness_log_recorder_sha_mismatch')
    layers=call.get('solver_backend_layers') or []
    if (len(layers)!=1 or any(isinstance(value,bool) or not isinstance(value,int) or value!=0
            for value in (metadata.get('logged_layer_index'),receipt.get('logged_layer_index'))) or
            isinstance(layers[0].get('status'),bool) or layers[0].get('status')!=0):
        native._failed('actual_witness_logged_layer_mapping_mismatch')
    original_layer=metadata.get('original_result_layer_index')
    if isinstance(original_layer,bool) or not isinstance(original_layer,int) or original_layer<0 or receipt.get('original_result_layer_index')!=original_layer:
        native._failed('actual_witness_original_layer_mapping_mismatch')
    proofs=_annual_attempts(call,allow_m25_48=local['allow_m25_48'])
    if len(proofs)!=1:native._unknown('nonunique_logged_actual_witness_primal')
    proof=proofs[0];ai=proof['attempt_index'];attempt=proof['attempt'];raw=attempt['raw_x']
    if (any(isinstance(value,bool) or not isinstance(value,int) or value!=ai
            for value in (metadata.get('accepted_attempt_index'),receipt.get('accepted_attempt_index'))) or
            isinstance(attempt.get('status'),bool)):
        native._failed('actual_witness_accepted_attempt_mapping_mismatch')
    digest=witness.vector_sha(raw)
    if any(v!=digest for v in (receipt.get('raw_x_sha256'),metadata.get('raw_x_sha256'))):
        native._failed('actual_witness_primal_sha_mismatch')
    if receipt.get('raw_x_length')!=len(raw) or metadata.get('raw_x_length')!=len(raw):
        native._failed('actual_witness_primal_length_mismatch')
    if metadata.get('unused_steam_coordinate')!=13 or any(isinstance(v,bool) or not journal._number(v) or v!=raw[13]
            for v in (receipt.get('unused_steam_lbph'),metadata.get('unused_steam_lbph'))):
        native._failed('actual_witness_direct_unused_steam_mismatch')
    ref=attempt.get('validation',{}).get('rows_ref')
    if journal._json(receipt.get('rows_ref'))!=journal._json(ref):
        native._failed('actual_witness_row_reference_mismatch')
    location=[ci,'solver_backend_layers',0,'attempts',ai,'validation']
    row,original_ref=_annual_rows(local,proof,location=location)
    if journal._json(metadata.get('original_steam_row'))!=journal._json(row):
        native._failed('actual_witness_original_steam_row_mismatch')
    basis=metadata.get('service_basis') or {};ledger=segment['service_request_ledger']
    for key,expected in (
            ('connected_loads_kW',segment['connected_loads_kW']),
            ('active_cut_kW',segment['disconnected_loads_kW']),
            ('total_unserved_kW',segment['_annual_original_total_unserved_kW']),
            ('connected_unserved_kW',ledger['connected_unserved_kW']),('delivered_kW',ledger['delivered_kW'])):
        if not native._same(basis.get(key),expected):native._failed('actual_witness_service_basis_mismatch:'+key)
    compiled=_compiled_flow_packet(context,local,block,segment,proof,metadata,receipt,ci,pair)
    local.setdefault('_validated_compiled_packets',{})[ci]=compiled
    namespace=dict(native.__dict__,_action_service=(
        _steam_flow_with_actual_cuts if steam_flow_only else _service_with_actual_cuts))
    steam_check=FunctionType(native._steam.__code__,namespace,native._steam.__name__)
    residual=steam_check(local,segment,proof,row)
    return dict(segment_index=index,start_minute=a,end_minute=b,status='passed',reasons=[],
        metered_waste_lbph=raw[13],metered_waste_lb=raw[13]*(b-a)/60.,steam_residual_lbph=residual,
        binding=dict(proof_kind='logged_final_actual_witness',actual_solver_call_indices=[ci],
            actual_primal_locations=[dict(solver_call_index=ci,layer_index=0,attempt_index=ai,
                original_result_layer_index=original_layer)],original_row_references=[original_ref],
            alternative_candidates_used_to_select_primal=False,raw_x_sha256=digest,
            selected_domain_witness=deepcopy(metadata.get('selected_domain')),
            selected_domain_attribution=metadata.get('selected_domain_attribution')))


def audit_steam_block(context, block, *, block_record, use_actual_witness=False,steam_flow_only=False):
    """Audit a supplied original block against its committed raw SHA and owners.

    Missing/unsupported evidence stays unknown; contradictory evidence fails.
    Complete totals are ``None`` if any actual segment cannot be qualified.
    ``steam_flow_only`` requires final actual receipts independently of the
    strict-witness flag. It validates steam algebra only, with exactly zero
    recovery cooling and no startup/electric/whole-system qualification.
    """
    segments = block.get('segments',[]) if isinstance(block,dict) else []
    results, reasons, global_status = [],[],None
    try:
        record = context['records'].get(block_record.get('file'))
        if record is None or any(block_record.get(k) != record[k] for k in ('file','sha256')):
            native._failed('block_record_not_in_verified_manifest')
        local = _block_context(context,block,record)
        for index,segment in enumerate(local['payload']['segments']):
            try:
                if (use_actual_witness or steam_flow_only) and 'annual_program_actual_witness' not in segment:
                    native._unknown('missing_final_actual_witness')
                selection=None if steam_flow_only else _execution_selection(context,block,block['segments'][index])
                selected_context=local
                if selection is not None:
                    # A detached phase view adapts the inherited strict matcher.
                    # Original call ownership, raw vectors and row references
                    # remain unchanged and are still fully checked below.
                    selected=selection['actual_block_call_index']
                    calls=[dict(call,phase='fixed_execution' if i==selected else
                        'uncommitted_interval_trial' if call.get('phase')=='fixed_execution' else call.get('phase'))
                        for i,call in enumerate(block['solver_calls'])]
                    selected_context=dict(local,payload=dict(local['payload'],solver_calls=calls))
                segment_auditor=None if steam_flow_only else _segment_auditor(
                    allow_m25_48=local['allow_m25_48'],final_result_selection_bound=selection is not None)
                # V03 lines 420-423 match a cold candidate against connected
                # unmet service. Retain original total ENS explicitly for the
                # identity checks above; only the inherited lookup view differs.
                lookup = dict(segment,
                    _annual_original_total_unserved_kW=segment['unserved_kW'],
                    unserved_kW=segment['service_request_ledger']['connected_unserved_kW'])
                if use_actual_witness or steam_flow_only:
                    result=_actual_witness_segment(context,local,block,lookup,index,steam_flow_only=steam_flow_only)
                else:
                    result = segment_auditor(selected_context,lookup,index)
                if selection is not None:
                    result['binding']['execution_source_selection']=selection
                result['binding']['actual_global_solver_call_indices'] = [
                    block['solver_index_base']+i for i in result['binding']['actual_solver_call_indices']]
                constants = context['constants'];action=segment['action']
                heat = sum(row[1] for row in segment['service_request_ledger']['delivered_kW'])
                ports = dict(HRSG=action['hrsg_steam_lbph'],boiler=action['boiler_steam_lbph'],
                    building_heat=heat/(constants['steam']*constants['exchanger']),
                    ABS=action['absorption_cooling_kW']/constants['rt']*constants['abs_steam'],
                    discard=result['metered_waste_lbph'])
                result.update(ports_lbph=ports,ports_lb={name:value*(result['end_minute']-result['start_minute'])/60.
                    for name,value in ports.items()})
            except native._Issue as issue:
                result=dict(segment_index=index,status=issue.status,reasons=[issue.reason],
                    metered_waste_lbph=None,metered_waste_lb=None,steam_residual_lbph=None,ports_lbph=None,ports_lb=None)
            except (KeyError,IndexError,TypeError,ValueError) as exc:
                result=dict(segment_index=index,status='unknown',reasons=['missing_or_unsupported_segment_evidence:'+str(exc)],
                    metered_waste_lbph=None,metered_waste_lb=None,steam_residual_lbph=None,ports_lbph=None,ports_lb=None)
            result.setdefault('start_minute',segment.get('start_minute'))
            result.setdefault('end_minute',segment.get('end_minute'))
            results.append(result)
        verify_steam_context(context,full=False)
    except native._Issue as issue:
        global_status=issue.status
        reasons=[issue.reason]
        results=[dict(segment_index=i,start_minute=s.get('start_minute'),end_minute=s.get('end_minute'),
            status=issue.status,reasons=[issue.reason],metered_waste_lbph=None,metered_waste_lb=None,
            steam_residual_lbph=None,ports_lbph=None,ports_lb=None) for i,s in enumerate(segments)]
    except (OSError,KeyError,IndexError,TypeError,ValueError) as exc:
        global_status='failed'
        reasons=['invalid_producer_evidence:'+str(exc)]
        results=[dict(segment_index=i,start_minute=s.get('start_minute'),end_minute=s.get('end_minute'),
            status='failed',reasons=reasons,metered_waste_lbph=None,metered_waste_lb=None,
            steam_residual_lbph=None,ports_lbph=None,ports_lb=None) for i,s in enumerate(segments)]
    statuses=[r['status'] for r in results]
    status='failed' if 'failed' in statuses else 'unknown' if not statuses or 'unknown' in statuses else 'passed'
    if global_status is not None:
        status=global_status
    complete=status=='passed'
    report=dict(schema=SCHEMA,status=status,reasons=reasons,segments=results,
        imputed_from_energy_remainder=False,physical_meter_or_OEM_qualification=False,
        source_sha_checks=context['source_hashes'],block_raw_binding=deepcopy(block_record),
        service_binding_provenance=deepcopy(SERVICE_BINDING_PROVENANCE),
        primal_layout_provenance=deepcopy(PRIMAL_LAYOUT_PROVENANCE),
        observed_prefix_only=True,whole_run_complete=context['observation']['complete'],
        summary=dict(segment_count=len(results),passed_segments=statuses.count('passed'),
            unknown_segments=statuses.count('unknown'),failed_segments=statuses.count('failed'),
            total_metered_waste_lb=sum(r['metered_waste_lb'] for r in results) if complete else None,
            maximum_absolute_steam_residual_lbph=max(abs(r['steam_residual_lbph']) for r in results) if complete else None))
    if steam_flow_only:
        qualification=dict(qualification_scope='algebraic_steam_flow_only',
            full_startup_context_validated=False,electricity_validated=False,whole_system_validated=False,
            recovery_context_validated=False)
        report.update(qualification)
        report['steam_flow_only_provenance']=dict(native_service_function='_action_service',
            native_service_full_ast_sha256=_FLOW_NATIVE_ACTION_AST_SHA,
            prefix_ends_before='active_recovery_or_GT_start_context_not_fully_exported',
            compared_actual_action_keys=list(_FLOW_STEAM_ACTION_KEYS),
            recovery_coordinate_43_requires_exact_zero=True,starter_coordinate_44_requires_exact_binary=True,
            original_full_raw_vector_preserved=True,legacy_action_matching_fallback=False)
        for result in results:result.update(qualification)
    return report


def audit_annual_block(producer_directory, *, source_root, block_index=0,use_actual_witness=False,steam_flow_only=False):
    """Convenience entry for an individual block; constructs no model."""
    context=prepare_steam_context(producer_directory,source_root=source_root)
    if isinstance(block_index,bool) or not isinstance(block_index,int) or not 0 <= block_index < len(context['raw_files_sha256']):
        raise ValueError('Block index outside committed journal')
    record=context['raw_files_sha256'][block_index]
    block=native._read(context['directory']/'evidence'/record['file'],zipped=True)
    report=audit_steam_block(context,block,block_record=record,use_actual_witness=use_actual_witness,
        steam_flow_only=steam_flow_only)
    verify_steam_context(context)
    return report
