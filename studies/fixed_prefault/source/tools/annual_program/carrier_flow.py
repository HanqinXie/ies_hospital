"""Owned final-primal electric/cold algebra, independent of steam qualification.

No model, optimizer, context/snapshot query, action-based primal selection or
remainder-imputed port. The static v1 scope excludes active recovery and GT
starter electricity; cold algebra is independent of the latter. Stock dynamics
and recorded-action arithmetic are separate layers, never whole-system proof.
"""
from __future__ import annotations

import ast
from collections import Counter
from copy import deepcopy
import gzip
import hashlib
import json
from pathlib import Path

from tools.operating_rules_study import ledger
from . import steam, witness

native=steam.native
SCHEMA='hospital_ies.annual_program.actual_carrier_flow.v1'
QUALIFICATION=dict(qualification_scope='algebraic_electric_cold_flow_only',
    full_startup_context_validated=False,recovery_context_validated=False,
    electricity_system_validated=False,whole_system_validated=False,
    physical_meter_or_OEM_qualification=False)
_RESOLVER_AST_SHA='76347736d66215580fa311ad1ce93667165eb59fa91c5d46fe02104e395d365f'
_LEDGER_SOURCE_SHA='6aa0109b686523403bbdfdb796ac1b028009fc0dd6961d3bcaa32639558f76a4'
_STATIC_SOURCES={
    'tools/operating_response/physical_balance.py':'fe13fe96a58c0f8816a38419074e4a5c12b65e0207b0d37e85a2d8abf82a76f5',
    'tools/model_closure_v22/physical.py':'460cdb4ef8210336ffab791557ec33ee34ad5d59270c3f394743490923b6f3d9'}


def _observer_sources():
    return {str(Path(module.__file__).resolve()):native._digest(module.__file__)
            for module in (steam,native,witness,ledger,steam.flow_source_contracts,steam.compiled_flow_input)} | {str(Path(__file__).resolve()):native._digest(__file__)}


def prepare_carrier_context(producer_directory, *, source_root, verified_journal=None):
    """Bind a closed original producer and separate running observer sources."""
    context=steam.prepare_steam_context(producer_directory,source_root=source_root,verified_journal=verified_journal)
    context['carrier_observer_sources']=_observer_sources()
    return context


def verify_carrier_context(context, *, full=True):
    if context['carrier_observer_sources']!=_observer_sources():native._failed('carrier_observer_source_drift')
    return steam.verify_steam_context(context,full=full)


def _owned_resolver():
    """Reuse the reviewed receipt verifier, stopping before any steam algebra.

    Its complete original AST is pinned, including all pre- and post-cut code.
    The detached prefix returns the exact already-validated proof/metadata.
    Original functions/globals are untouched; no steam audit/pass is invoked.
    """
    tree=ast.parse(Path(steam.__file__).read_text(encoding='utf8'))
    functions=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_actual_witness_segment']
    if len(functions)!=1:native._unknown('unsupported_owned_final_receipt_resolver')
    function=functions[0]
    if hashlib.sha256(ast.dump(function,include_attributes=False).encode()).hexdigest()!=_RESOLVER_AST_SHA:
        native._unknown('unsupported_owned_final_receipt_resolver')
    cuts=[i for i,n in enumerate(function.body) if isinstance(n,ast.Assign)
          and any(isinstance(t,ast.Name) and t.id=='namespace' for t in n.targets)]
    if len(cuts)!=1:native._unknown('unsupported_owned_final_receipt_resolver')
    function.name='_carrier_owned_final_receipt'
    function.body=function.body[:cuts[0]]+ast.parse('return proof, metadata, ci, original_ref').body
    detached=ast.fix_missing_locations(ast.Module(body=[function],type_ignores=[]))
    namespace=dict(steam.__dict__)
    exec(compile(detached,str(steam.__file__)+'::<owned-final-prefix>','exec'),namespace)
    return namespace[function.name]


def _parameters(context):
    constants=context['constants'];model=context['model_config']
    return dict(RT_TO_KW=constants['rt'],steam_kWh_per_lb=constants['steam'],exchanger_eff=constants['exchanger'],
        abs_steam_lb_per_rt=constants['abs_steam'],ec_electric_kW_per_rt=constants['ec_electric'],
        abs_aux_kW_per_rt=context['config']['historical_pnnl_performance']['existing_absorption_aux_kW_per_RT'],
        cold_pump_kW_per_kW=model['sci_engineering']['cold_pump_kW_per_kW'])


def _owned_carrier_rows(block,local):
    # _block_context has already verified the complete raw sidecar SHA, every
    # entry hash/count/location and its producer receipt. Retain only actual
    # owners' E/C rows here, not a second all-row history.
    wanted=set()
    for segment in block['segments']:
        receipt=segment.get(witness.FIELD)
        reference=receipt.get('rows_ref') if isinstance(receipt,dict) else None
        if isinstance(reference,dict):wanted.add(reference.get('record_id'))
    rows={}
    path=Path(block['solver_row_sidecar']['path'])
    with gzip.open(path,'rt',encoding='utf8') as stream:
        for line in stream:
            entry=json.loads(line)
            if entry['record_id'] in wanted:
                location=deepcopy(entry['location']);location[0]-=block['solver_call_local_start_index']
                rows[entry['record_id']]=dict(location=location,rows=[r for r in entry['rows']
                    if r.get('name') in ('balance.electric','balance.cooling')])
    return rows


def _check_mapping(action,expected,keys):
    for key in keys:
        if key not in action:native._unknown('missing_actual_carrier_port:'+key)
        if not native._same(action[key],expected[key]):native._failed('native_actual_carrier_port_mismatch:'+key)


def _check_row(rows,name,request,activity):
    selected=[row for row in rows if row.get('name')==name]
    if len(selected)!=1:native._unknown('missing_unique_original_carrier_row:'+name)
    row=selected[0]
    if row.get('unit')!='kW' or row.get('kind')!='physical':native._failed('wrong_original_carrier_row_kind_or_unit:'+name)
    for key,value in (('lower',request),('upper',request),('activity',activity)):
        if abs(native._number(row.get(key),name+'.'+key,nonnegative=False)-value)>ledger.POWER_TOL:
            native._failed('original_carrier_row_activity_or_bound_mismatch:'+name)
    violation=native._number(row.get('violation'),name+'.violation')
    if violation>1e-7 or abs(violation-max(0.,row['lower']-row['activity'],row['activity']-row['upper']))>native._MATCH_TOL:
        native._failed('original_carrier_row_violation_mismatch:'+name)
    return deepcopy(row)


def _empty(status,reason):
    return dict(status=status,reasons=[reason],ports_kW=None,ports_kWh=None,residual_kW=None,original_row=None)


def _flow(context,local,segment,x,served,expected,rows,carrier,*,compiled=None):
    constants=context['constants'];params=_parameters(context);action=segment['action']
    if x[43]!=0.:native._unknown('carrier_flow_requires_zero_recovery_context')
    if x[44] not in (0.,1.):native._unknown('carrier_flow_requires_exact_starter_binary')
    if any(x[i] not in (0.,1.) for i in (42,45)):native._unknown('carrier_flow_requires_exact_station_binaries')
    if carrier=='c':
        mapped=dict(expected,recovery_cooling_kW=x[43])
        _check_mapping(action,mapped,('absorption_cooling_kW','electric_chiller_cooling_kW',
            'cold_discharge_kW','cold_charge_kW','recovery_cooling_kW'))
        ports=dict(ABS=x[6]*constants['rt'],EC=sum(x[7:10])*constants['rt'],
            cold_discharge=x[40],cold_charge=-x[41],recovery=-x[43],building=-sum(r[2] for r in served))
        request=sum(r[2] for r in segment['connected_loads_kW'])
        activity=sum(ports.values())-ports['building']+sum(x[16+3*g] for g in range(4))
        row=_check_row(rows,'balance.cooling',request,activity)
    else:
        station=context['model_config']['model_closure_v22']['station']
        fixed=native._number(station['boiler_fixed_kW'],'boiler_fixed')
        coefficient=native._number(station['boiler_kW_per_kWsteam'],'boiler_coefficient')
        if compiled is not None:
            fixed=compiled['closure']['boiler_fixed_kW']
            coefficient=compiled['closure']['boiler_kW_per_kWsteam']
        restart=(fixed if compiled is None else compiled['closure']['boiler_restart_kW'])*x[45]
        boiler=fixed*x[42]+coefficient*constants['steam']*x[10]
        mapped=dict(expected,boiler_auxiliary_kW=boiler,boiler_restart_electric_kW=restart,
            boiler_station_running=bool(x[42]>.5),recovery_pump_electric_kW=0.,recovery_fan_electric_kW=0.)
        _check_mapping(action,mapped,('gt_electric_kW','grid_import_kW','diesel_kW','ups_discharge_kW','ups_charge_kW',
            'absorption_cooling_kW','electric_chiller_cooling_kW','cold_discharge_kW','cold_charge_kW',
            'cold_storage_pump_kW','boiler_auxiliary_kW','boiler_restart_electric_kW','boiler_station_running',
            'recovery_pump_electric_kW','recovery_fan_electric_kW'))
        if x[44]!=0. and compiled is None:native._unknown('GT_start_electric_coefficient_not_originally_exported')
        starter=0. if compiled is None else compiled['closure']['gt_start_kW']*x[44]
        mapped.update(gt_start_electric_kW=starter,station_auxiliary_kW=boiler+restart+starter)
        _check_mapping(action,mapped,('gt_start_electric_kW','station_auxiliary_kW'))
        ports=dict(GT=sum(expected['gt_electric_kW']),grid=x[11],DG=x[12],UPS_discharge=x[38],UPS_charge=-x[39],
            ABS=-x[6]*params['abs_aux_kW_per_rt'],EC=-sum(x[7:10])*params['ec_electric_kW_per_rt'],
            cold_pump=-params['cold_pump_kW_per_kW']*(x[40]+x[41]),boiler_auxiliary=-boiler,
            boiler_restart=-restart,GT_start=-starter,recovery_pump=0.,recovery_fan=0.,building=-sum(r[0] for r in served))
        request=sum(r[0] for r in segment['connected_loads_kW'])
        activity=sum(ports.values())-ports['building']+sum(x[14+3*g] for g in range(4))+sum(x[:3])-ports['GT']
        row=_check_row(rows,'balance.electric',request,activity)
    residual=sum(ports.values())
    if abs(residual)>ledger.POWER_TOL:native._failed('independent_actual_carrier_flow_failure:'+carrier)
    original=segment.get('energy_balance_residuals')
    if not isinstance(original,list) or len(original)!=3:native._unknown('missing_original_carrier_residuals')
    if abs(native._number(original[0 if carrier=='e' else 1],carrier+'.original_residual',nonnegative=False)-residual)>native._MATCH_TOL:
        native._failed('original_carrier_result_residual_mismatch:'+carrier)
    dt=(segment['end_minute']-segment['start_minute'])/60.
    return dict(status='passed',reasons=[],ports_kW=ports,ports_kWh={k:v*dt for k,v in ports.items()},
        residual_kW=residual,original_row=row,static_source_formula_validated=True,
        startup_eligibility_validated=False,port_sign_convention='sources positive, sinks negative; useful hub boundary')


def _segment(context,local,block,segment,index,resolver,owned):
    out=dict(segment_index=index,start_minute=segment['start_minute'],end_minute=segment['end_minute'],
        raw_flow={},binding=None,recorded_gt_start_electric_kW=segment.get('action',{}).get('gt_start_electric_kW'),
        compiled_context_status='absent_original_v1',**QUALIFICATION)
    try:
        if witness.FIELD not in segment:native._unknown('missing_final_actual_witness')
        lookup=dict(segment,_annual_original_total_unserved_kW=segment['unserved_kW'],
            unserved_kW=segment['service_request_ledger']['connected_unserved_kW'])
        proof,metadata,ci,ref=resolver(context,local,block,lookup,index)
        compiled=local.get('_validated_compiled_packets',{}).get(ci)
        if ((metadata.get('compiled_flow_input') is not None and compiled is None)
                or any(k in metadata for k in ('_compiled_flow_input','compiled_flow_context'))):
            native._unknown('unsupported_future_compiled_flow_context_schema')
        if compiled is not None:out['compiled_context_status']='validated_final_packet'
        elif metadata.get('compiled_flow_binding') is not None:out['compiled_context_status']='unavailable_final_packet'
        x=proof['x'];entry=owned.get(ref.get('record_id'))
        if entry is None:native._unknown('missing_owned_original_carrier_rows')
        location=[ci,'solver_backend_layers',0,'attempts',proof['attempt_index'],'validation']
        if entry['location']!=location:native._failed('wrong_carrier_row_owner')
        served,expected=steam._service_with_actual_cuts(local,lookup,x,action_service=steam._flow_native_service_prefix())
        # The source-pinned full native mapper rejects unknown/missing action
        # components after its startup guard. Retain that schema boundary even
        # when only this module's E/C ports are qualified, so a new unmodeled
        # carrier sink cannot be silently ignored.
        station_keys={'boiler_auxiliary_kW','gt_start_electric_kW','boiler_restart_electric_kW',
            'station_auxiliary_kW','recovery_cooling_kW','recovery_pump_electric_kW',
            'recovery_fan_electric_kW','boiler_station_running'}
        if set(segment['action'])!=set(expected)|station_keys:
            native._unknown('unsupported_actual_action_components')
        out['binding']=dict(proof_kind='logged_final_actual_witness',raw_x_sha256=witness.vector_sha(proof['attempt']['raw_x']),
            original_row_reference=deepcopy(ref),actual_solver_call_indices=[ci],
            actual_global_solver_call_indices=[block['solver_index_base']+ci],
            alternative_candidates_used_to_select_primal=False,full_primal_length=len(x),
            receipt_verifier_ast_sha256=_RESOLVER_AST_SHA)
        for carrier in ('e','c'):
            try:out['raw_flow'][carrier]=_flow(context,local,lookup,x,served,expected,entry['rows'],carrier,compiled=compiled)
            except native._Issue as exc:out['raw_flow'][carrier]=_empty(exc.status,exc.reason)
            except (KeyError,IndexError,TypeError,ValueError) as exc:
                out['raw_flow'][carrier]=_empty('unknown','missing_or_unsupported_carrier_evidence:'+str(exc))
    except native._Issue as exc:out['raw_flow']={k:_empty(exc.status,exc.reason) for k in ('e','c')}
    except (KeyError,IndexError,TypeError,ValueError) as exc:
        out['raw_flow']={k:_empty('unknown','missing_or_unsupported_owned_evidence:'+str(exc)) for k in ('e','c')}
    out['status']=_status([r['status'] for r in out['raw_flow'].values()])
    return out


def _status(statuses):return 'failed' if 'failed' in statuses else 'unknown' if not statuses or 'unknown' in statuses else 'passed'


def audit_carrier_block(context,block,*,block_record):
    """Stream one committed block; raw carrier status excludes stock/eligibility."""
    results=[];reasons=[];action_ledger=None;storage=None;diesel=None
    static_provenance=deepcopy(_STATIC_SOURCES)
    segments=block.get('segments',[]) if isinstance(block,dict) else []
    try:
        verify_carrier_context(context,full=False)
        # This is the running independent accounting observer, not a required
        # producer implementation file. Keep the two identities separate.
        if native._digest(ledger.__file__)!=_LEDGER_SOURCE_SHA:native._unknown('unsupported_recorded_action_ledger_source')
        for path,expected in _STATIC_SOURCES.items():
            if path=='tools/operating_response/physical_balance.py':
                steam._physical_formula_profile(context)
                static_provenance[path]=context['source_hashes'][path]
                continue
            if context['source_hashes'].get(path)!=expected:native._unknown('unsupported_carrier_static_source:'+path)
        record=context['records'].get(block_record.get('file'))
        if record is None or any(block_record.get(k)!=record[k] for k in ('file','sha256')):
            native._failed('block_record_not_in_verified_manifest')
        local=steam._block_context(context,block,record)
        # Consistently malformed receipt/attempt references can agree with one
        # another yet still be structurally invalid. Reject before the pinned
        # resolver or the separate recorded-action ledger dereferences them.
        for segment in segments:
            if not isinstance(segment,dict):native._failed('invalid_carrier_segment_type')
            if 'action' in segment and not isinstance(segment['action'],dict):
                native._failed('invalid_actual_carrier_action_type')
            if witness.FIELD in segment:
                receipt=segment[witness.FIELD]
                if not isinstance(receipt,dict):native._failed('invalid_final_actual_receipt_type')
                if 'rows_ref' in receipt and receipt['rows_ref'] is not None and not isinstance(receipt['rows_ref'],dict):
                    native._failed('invalid_final_actual_row_reference_type')
        owned=_owned_carrier_rows(block,local);resolver=_owned_resolver()
        durations=[(s['end_minute']-s['start_minute'])/60. for s in segments]
        balances,details=ledger._balances(segments,durations,_parameters(context))
        action_ledger=dict(qualification_scope='recorded_actual_action_arithmetic',balances=balances,segments=details)
        payload=dict(segments=segments,model_config=context['model_config'],terminal_snapshot=block['terminal'])
        storage=ledger._storage(payload,durations);diesel=ledger._diesel(segments,durations)
        results=[_segment(context,local,block,s,i,resolver,owned) for i,s in enumerate(local['payload']['segments'])]
        verify_carrier_context(context,full=False)
    except native._Issue as exc:reasons=[exc.reason];failure=exc.status
    except (OSError,KeyError,IndexError,TypeError,ValueError) as exc:
        reasons=['invalid_carrier_block_evidence:'+str(exc)];failure='failed'
    if reasons:
        results=[dict(segment_index=i,start_minute=s.get('start_minute'),end_minute=s.get('end_minute'),
            status=failure,raw_flow={k:_empty(failure,reasons[0]) for k in ('e','c')},binding=None,**QUALIFICATION)
            for i,s in enumerate(segments)]
    summary={}
    for carrier in ('e','c'):
        flows=[r['raw_flow'][carrier] for r in results];counts=Counter(f['status'] for f in flows)
        passed=[f for f in flows if f['status']=='passed'];status=failure if reasons else _status([f['status'] for f in flows])
        known={k:sum(f['ports_kWh'][k] for f in passed) for k in passed[0]['ports_kWh']} if passed else None
        summary[carrier]=dict(status=status,segment_count=len(flows),passed_segments=counts['passed'],
            unknown_segments=counts['unknown'],failed_segments=counts['failed'],
            total_ports_kWh=known if status=='passed' else None,known_passed_ports_kWh=known,
            maximum_absolute_residual_kW=max(abs(f['residual_kW']) for f in passed) if status=='passed' else None,
            known_passed_maximum_absolute_residual_kW=max(abs(f['residual_kW']) for f in passed) if passed else None,
            unknown_reason_counts=dict(Counter(reason for f in flows if f['status']=='unknown' for reason in f['reasons'])))
    return dict(schema=SCHEMA,status=_status([summary[k]['status'] for k in ('e','c')]),reasons=reasons,
        segments=results,summary=summary,action_ledger=action_ledger,storage=storage,diesel=diesel,
        block_raw_binding=deepcopy(block_record),producer_source_root=str(context['source_root']),
        producer_source_sha256=context['source_hashes'],observer_source_sha256=context['carrier_observer_sources'],
        observed_prefix_only=True,imputed_from_energy_remainder=False,legacy_action_matching_fallback=False,
        stock_dynamics_included_in_raw_flow_status=False,static_source_provenance=static_provenance,**QUALIFICATION)


def audit_carrier_segment(context,block,*,block_record,segment_index):
    """Select a segment only after validating its complete committed block."""
    if isinstance(segment_index,bool) or not isinstance(segment_index,int) or not 0<=segment_index<len(block['segments']):
        raise ValueError('Segment index outside committed block')
    return audit_carrier_block(context,block,block_record=block_record)['segments'][segment_index]


def audit_annual_block(producer_directory,*,source_root,block_index=0):
    context=prepare_carrier_context(producer_directory,source_root=source_root)
    if isinstance(block_index,bool) or not isinstance(block_index,int) or not 0<=block_index<len(context['raw_files_sha256']):
        raise ValueError('Block index outside committed journal')
    record=context['raw_files_sha256'][block_index]
    block=native._read(context['directory']/'evidence'/record['file'],zipped=True)
    report=audit_carrier_block(context,block,block_record=record)
    verify_carrier_context(context)
    return report
