"""Final-primal E/C flow receipts: native validation/logging, no optimizer."""
from copy import deepcopy
import importlib
import json
from pathlib import Path

import numpy as np
import pytest

from test_annual_program_steam import annual, legacy_case, attach_actual_witness
from test_engineering_basis_steam_meter import jsha
from test_annual_program_witness import native_validate_vector


def api():
    path=Path(__file__).parents[1]/'tools/annual_program/carrier_flow.py'
    assert path.is_file(), 'independent final-primal carrier-flow observer not implemented'
    return importlib.import_module('tools.annual_program.carrier_flow')


@pytest.mark.parametrize('reference',[['foreign'],'foreign',7])
def test_consistently_malformed_receipt_and_attempt_row_ref_are_explicit_failure(carrier,reference):
    def corrupt(segment,calls,entries,field):
        receipt=segment[field]
        call=calls[receipt['solver_call_index']]
        call['solver_backend_layers'][0]['attempts'][receipt['accepted_attempt_index']]['validation']['rows_ref']=deepcopy(reference)
        receipt['rows_ref']=deepcopy(reference)
    old,_=carrier(corrupt=corrupt)
    report=api().audit_annual_block(old.producer,source_root=old.source)
    assert report['status']=='failed'
    assert all(report['summary'][key]['total_ports_kWh'] is None for key in ('e','c'))


@pytest.mark.parametrize('action',[None,['unexpected']])
def test_nonmapping_actual_action_is_explicit_failure(carrier,action):
    old,_=carrier(corrupt=lambda segment,calls,entries,field:segment.__setitem__('action',deepcopy(action)))
    report=api().audit_annual_block(old.producer,source_root=old.source)
    assert report['status']=='failed'
    assert all(report['summary'][key]['total_ports_kWh'] is None for key in ('e','c'))


@pytest.fixture
def carrier(annual):
    old,persist=annual
    def build(*,starter=0.,recovery=0.,updates=None,layout48=False,retained=False,active_cut=False,corrupt=None):
        def change(index,s,calls,entries):
            cfg=json.loads((old.source/'configs/plant_configs.json').read_text(encoding='utf8'))
            perf=cfg['historical_pnnl_performance'];rt=3.51685284
            x=calls[1]['solver_backend_layers'][-1]['attempts'][0]['raw_x']
            for k,v in (updates or {}).items():x[k]=v
            x[43]=recovery;x[44]=starter
            a=s['action'];a.update(grid_import_kW=x[11],diesel_kW=x[12],
                absorption_cooling_kW=x[6]*rt,electric_chiller_cooling_kW=[v*rt for v in x[7:10]],
                boiler_steam_lbph=x[10],ups_discharge_kW=x[38],ups_charge_kW=x[39],
                cold_discharge_kW=x[40],cold_charge_kW=x[41],cold_storage_pump_kW=.02*(x[40]+x[41]),
                boiler_auxiliary_kW=.5*x[42]+.012*.30*x[10],gt_start_electric_kW=10.*starter,
                boiler_restart_electric_kW=.5*x[45],recovery_cooling_kW=recovery,
                recovery_pump_electric_kW=.02*recovery,recovery_fan_electric_kW=0.,
                boiler_station_running=bool(x[42]>.5))
            a['station_auxiliary_kW']=a['boiler_auxiliary_kW']+a['gt_start_electric_kW']+a['boiler_restart_electric_kW']
            matrix=np.zeros((3,len(x)));matrix[0,[0,1,2,11,12,38]]=1.
            matrix[0,39]=-1.;matrix[0,6]=-perf['existing_absorption_aux_kW_per_RT']
            matrix[0,7:10]=-perf['existing_electric_chiller_all_in_kW_per_RT']
            matrix[0,40:42]=-.02;matrix[0,10]=-.012*.30;matrix[0,42]=-.5
            matrix[0,43]=-.02;matrix[0,44]=-10.;matrix[0,45]=-.5
            matrix[1,0]=a['hrsg_steam_lbph']/x[0];matrix[1,10]=1.;matrix[1,13]=-1.
            matrix[1,6]=-perf['existing_absorption_steam_lb_per_RT_hr']
            matrix[2,6:10]=rt;matrix[2,40]=1.;matrix[2,41]=-1.;matrix[2,43]=-1.
            net=matrix@np.asarray(x)
            for g in range(4):
                matrix[0,14+3*g]=1.;matrix[1,15+3*g]=1./(.30*.95);matrix[2,16+3*g]=1.
            connected_lost=[[max(0.,x[14+3*g+k]) for k in range(3)] for g in range(4)]
            served=[[0.]*3 for _ in range(4)];served[0]=[float(net[0]),float(net[1])*.30*.95,float(net[2])]
            connected=[[served[g][k]+connected_lost[g][k] for k in range(3)] for g in range(4)]
            cut=s['disconnected_loads_kW']
            total=[[connected_lost[g][k]+cut[g][k] for k in range(3)] for g in range(4)]
            original=[[connected[g][k]+cut[g][k] for k in range(3)] for g in range(4)]
            s.update(connected_loads_kW=connected,loads_kW=original,unserved_kW=total,
                energy_balance_residuals=[0.,0.,0.])
            s['service_request_ledger'].update(original_demand_kW=deepcopy(original),
                active_cut_kW=deepcopy(cut),connected_unserved_kW=connected_lost,delivered_kW=served)
            s['service_dependencies']['direct_electric_delivered_kW']=[served[g][0] for g in range(4)]
            rhs=np.asarray([sum(r[0] for r in connected),sum(r[1] for r in connected)/(.30*.95),sum(r[2] for r in connected)])
            integrality=np.zeros(len(x),dtype=int);integrality[[3,4,5,42,44,45]]=1
            rows=[dict(name=n,unit=u,kind='physical',source='real native validator; synthetic truthful flow fixture')
                  for n,u in [('balance.electric','kW'),('balance.steam','lb/h'),('balance.cooling','kW')]]
            checked=native_validate_vector()(matrix,rhs,rhs,np.zeros(len(x)),np.full(len(x),np.inf),integrality,np.asarray(x),rows)
            assert checked['max_original_violation']<1e-7
            checked['rows_ref']=calls[1]['solver_backend_layers'][-1]['attempts'][0]['validation']['rows_ref']
            calls[1]['solver_backend_layers'][-1]['attempts'][0]['validation']=checked
            entries[0].update(rows=deepcopy(checked['rows']),row_count=3,rows_sha256=jsha(checked['rows']))
            attach_actual_witness(old,index,s,calls,entries,guard=True,corrupt=corrupt)
        blocks=persist(layout48=layout48,retained=retained,active_cut=active_cut,change=change)
        return old,blocks
    return build


@pytest.mark.parametrize('layout48',[False,True])
@pytest.mark.parametrize('retained',[False,True])
def test_truthful_final_receipt_carries_E_C_scope_and_exact_global_owner(carrier,layout48,retained):
    module=api();old,blocks=carrier(layout48=layout48,retained=retained,active_cut=True)
    context=module.prepare_carrier_context(old.producer,source_root=old.source)
    before=deepcopy(blocks[1])
    report=module.audit_carrier_block(context,blocks[1],block_record=context['raw_files_sha256'][1])
    assert report==module.audit_annual_block(old.producer,source_root=old.source,block_index=1)
    assert blocks[1]==before
    assert report['status']=='passed',report['reasons']
    row=report['segments'][0]
    assert row['binding']['actual_global_solver_call_indices']==[7]
    for scope in (report,row):
        assert scope['qualification_scope']=='algebraic_electric_cold_flow_only'
        assert scope['full_startup_context_validated'] is scope['whole_system_validated'] is False
    for key in ('e','c'):
        assert row['raw_flow'][key]['status']=='passed'
        assert abs(row['raw_flow'][key]['residual_kW'])<1e-10
        assert report['action_ledger']['balances'][key]['check']=='pass'
    assert report['storage']['ups']['full_dynamics_check']=='unknown'
    assert row['binding']['alternative_candidates_used_to_select_primal'] is False
    assert report['imputed_from_energy_remainder'] is False


@pytest.mark.parametrize('updates',[
    {11:3.,12:2.},{38:2.},{39:2.},{40:2.},{41:2.,7:1.},
    {6:1.,7:1.,8:2.,9:1.},{10:2.,42:1.},{45:1.},
    {14:2.,16:3.,7:1.},
])
def test_native_port_formulas_include_converters_stores_and_station_once(carrier,updates):
    module=api();old,_=carrier(updates=updates,layout48=True)
    report=module.audit_annual_block(old.producer,source_root=old.source)
    assert report['status']=='passed'
    assert all(report['segments'][0]['raw_flow'][k]['status']=='passed' for k in ('e','c'))


def test_GT_starter_action_is_present_but_stronger_E_context_stays_unknown_C_passes(carrier):
    module=api();old,_=carrier(starter=1.,layout48=True)
    report=module.audit_annual_block(old.producer,source_root=old.source)
    row=report['segments'][0]
    assert report['status']=='unknown'
    assert row['raw_flow']['c']['status']=='passed'
    assert row['raw_flow']['e']['status']=='unknown'
    assert row['raw_flow']['e']['reasons']==['GT_start_electric_coefficient_not_originally_exported']
    assert row['recorded_gt_start_electric_kW']==10.
    assert report['action_ledger']['balances']['e']['check']=='pass'
    assert report['summary']['e']['total_ports_kWh'] is None
    assert report['summary']['c']['total_ports_kWh'] is not None


@pytest.mark.parametrize('recovery,starter',[(1.,0.),(0.,.5)])
def test_unsupported_recovery_or_invalid_starter_cannot_pass(carrier,recovery,starter):
    module=api()
    if starter==.5:
        # Corrupt after native integer validation; the source acceptance checks
        # must reject it rather than call a half starter a legitimate context.
        def corrupt(s,c,e,field):c[-1]['solver_backend_layers'][0]['attempts'][0]['raw_x'][44]=.5
        old,_=carrier(corrupt=corrupt)
    else:old,_=carrier(recovery=recovery,updates={7:1.})
    report=module.audit_annual_block(old.producer,source_root=old.source)
    assert report['status'] in ('unknown','failed')
    assert report['summary']['e']['total_ports_kWh'] is None


@pytest.mark.parametrize('change',['receipt','vector','owner','source','service','row_ref','row_activity','future_context'])
def test_missing_or_contradictory_binding_never_uses_matching_candidate(carrier,change):
    module=api()
    def corrupt(s,c,e,field):
        receipt=s[field];owner=receipt['solver_call_index'];call=c[owner]
        if change=='receipt':del s[field]
        elif change=='vector':receipt['raw_x_sha256']='0'*64
        elif change=='owner':receipt['solver_call_index']=0
        elif change=='source':call['annual_actual_witness']['original_native_source_binding']['supported']=False
        elif change=='service':s['service_request_ledger']['delivered_kW'][0][0]+=1.
        elif change=='row_ref':e[-1]['location'][0]=0
        elif change=='future_context':call['annual_actual_witness']['compiled_flow_input']={'schema':'undefined.future.v2','gt_start_kW':0.}
        elif change=='row_activity':
            entry=e[-1];entry['rows'][0]['activity']+=1.;entry['rows_sha256']=jsha(entry['rows'])
            ref=call['solver_backend_layers'][0]['attempts'][0]['validation']['rows_ref']
            ref['rows_sha256']=entry['rows_sha256'];receipt['rows_ref']=deepcopy(ref)
    old,_=carrier(corrupt=corrupt)
    report=module.audit_annual_block(old.producer,source_root=old.source)
    assert report['status']==('unknown' if change in ('receipt','future_context') else 'failed')
    assert report['summary']['e']['total_ports_kWh'] is None


@pytest.mark.parametrize('key,carrier_key',[('grid_import_kW','e'),('cold_discharge_kW','c'),
    ('cold_storage_pump_kW','e'),('station_auxiliary_kW','e'),('absorption_cooling_kW','c')])
def test_wrong_recorded_port_fails_its_mapping_before_any_integral(carrier,key,carrier_key):
    module=api()
    old,_=carrier(corrupt=lambda s,c,e,f:s['action'].__setitem__(key,s['action'][key]+1.))
    report=module.audit_annual_block(old.producer,source_root=old.source)
    assert report['segments'][0]['raw_flow'][carrier_key]['status']=='failed'
    assert report['summary'][carrier_key]['total_ports_kWh'] is None


def test_segment_API_requires_complete_owned_block_and_cannot_substitute_memory(carrier):
    module=api();old,blocks=carrier()
    context=module.prepare_carrier_context(old.producer,source_root=old.source)
    row=module.audit_carrier_segment(context,blocks[0],block_record=context['raw_files_sha256'][0],segment_index=0)
    assert row==module.audit_carrier_block(context,blocks[0],block_record=context['raw_files_sha256'][0])['segments'][0]
    changed=deepcopy(blocks[0]);changed['segments'][0]['action']['grid_import_kW']+=1e-10
    assert module.audit_carrier_block(context,changed,block_record=context['raw_files_sha256'][0])['status']=='failed'
    with pytest.raises(ValueError):module.audit_carrier_segment(context,blocks[0],block_record=context['raw_files_sha256'][0],segment_index=True)


@pytest.mark.parametrize('value',[None,[],False,'foreign'])
def test_malformed_receipt_reference_is_reported_without_crashing_or_fallback(carrier,value):
    module=api();old,_=carrier(corrupt=lambda s,c,e,f:s[f].__setitem__('rows_ref',value))
    report=module.audit_annual_block(old.producer,source_root=old.source)
    assert report['status']=='failed'
    assert report['summary']['c']['total_ports_kWh'] is None


@pytest.mark.parametrize('change',['missing_E','duplicate_C','unit_C','bound_E','activity_C'])
def test_original_E_C_rows_have_their_own_unique_owned_equations(carrier,change):
    module=api()
    def corrupt(s,c,e,field):
        receipt=s[field];call=c[receipt['solver_call_index']];entry=e[-1]
        rows=entry['rows']
        if change=='missing_E':rows[:]=[r for r in rows if r['name']!='balance.electric']
        elif change=='duplicate_C':rows.append(deepcopy(rows[2]))
        elif change=='unit_C':rows[2]['unit']='RT'
        elif change=='bound_E':rows[0]['lower']+=1.;rows[0]['upper']+=1.
        elif change=='activity_C':rows[2]['activity']+=1.
        entry.update(rows_sha256=jsha(rows),row_count=len(rows))
        reference=call['solver_backend_layers'][0]['attempts'][0]['validation']['rows_ref']
        reference.update(rows_sha256=entry['rows_sha256'],row_count=len(rows));receipt['rows_ref']=deepcopy(reference)
    old,_=carrier(corrupt=corrupt)
    row=module.audit_annual_block(old.producer,source_root=old.source)['segments'][0]
    affected='e' if change.endswith('_E') else 'c';other='c' if affected=='e' else 'e'
    assert row['raw_flow'][affected]['status']==('unknown' if change in ('missing_E','duplicate_C') else 'failed')
    assert row['raw_flow'][other]['status']=='passed'


def test_carrier_scope_does_not_relabel_a_failed_steam_action_as_full_physics(carrier):
    module=api()
    old,_=carrier(corrupt=lambda s,c,e,f:s['action'].__setitem__('hrsg_steam_lbph',0.))
    report=module.audit_annual_block(old.producer,source_root=old.source)
    assert report['status']=='passed'
    assert report['action_ledger']['balances']['steam']['source_sufficiency_check']=='fail'
    assert report['whole_system_validated'] is False


def test_source_drift_and_empty_substituted_block_are_failed(carrier):
    module=api();old,blocks=carrier()
    context=module.prepare_carrier_context(old.producer,source_root=old.source)
    altered=deepcopy(blocks[0]);altered['segments']=[]
    report=module.audit_carrier_block(context,altered,block_record=context['raw_files_sha256'][0])
    assert report['status']=='failed'
    assert report['summary']['e']['total_ports_kWh'] is None
    path=old.source/'tools/operating_response/physical_balance.py'
    path.write_bytes(path.read_bytes()+b'\n# source drift fixture\n')
    assert module.audit_carrier_block(context,blocks[0],block_record=context['raw_files_sha256'][0])['status']=='failed'


def test_unaccepted_final_call_does_not_borrow_matching_legal_candidate(carrier):
    module=api()
    old,_=carrier(corrupt=lambda s,c,e,f:c[s[f]['solver_call_index']]['solver_backend_layers'][0]['attempts'][0].__setitem__('accepted',False))
    report=module.audit_annual_block(old.producer,source_root=old.source)
    assert report['status']=='unknown'
    assert report['legacy_action_matching_fallback'] is False
    assert report['summary']['e']['total_ports_kWh'] is None


def test_residual_metadata_is_checked_without_inventing_an_energy_dump(carrier):
    module=api()
    old,_=carrier(corrupt=lambda s,c,e,f:s['energy_balance_residuals'].__setitem__(0,1.))
    report=module.audit_annual_block(old.producer,source_root=old.source)
    assert report['segments'][0]['raw_flow']['e']['status']=='failed'
    assert report['segments'][0]['raw_flow']['c']['status']=='passed'
    assert report['imputed_from_energy_remainder'] is False
    assert report['summary']['e']['total_ports_kWh'] is None


@pytest.mark.parametrize('change',['new_uninterpreted_port','missing_original_component'])
def test_unsupported_native_action_component_set_is_never_silently_ignored(carrier,change):
    module=api()
    def corrupt(s,c,e,f):
        if change=='new_uninterpreted_port':s['action']['uninterpreted_carrier_sink_kW']=1.
        else:del s['action']['hrsg_steam_lbph']
    old,_=carrier(corrupt=corrupt)
    report=module.audit_annual_block(old.producer,source_root=old.source)
    assert report['status']=='unknown'
    for carrier_key in ('e','c'):
        assert report['segments'][0]['raw_flow'][carrier_key]['reasons']==['unsupported_actual_action_components']
        assert report['summary'][carrier_key]['total_ports_kWh'] is None
