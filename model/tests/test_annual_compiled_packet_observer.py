"""Historical ABI reader + native vector validation, synthetic protocol fixture.

No environment, reset or optimizer is created. These are protocol/row-algebra
tests, not a physical producer. Frozen source bytes are only hashed/parsed;
current actual recorder/hook coverage is separate in the SF1 witness tests.
Public old journal coverage is separate.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src'),str(ROOT/'tests')]
from test_annual_compiled_witness import legacy_captured
from test_annual_program_witness import actual,legacy_case,native_validate_vector
from test_annual_compiled_flow_input import fixture as leaf_values,rehash
from tools.annual_program import steam,carrier_flow,witness,flow_source_contracts as contracts
from tools.annual_program.compiled_flow_input import build_compiled_flow_input
from tools.sci_engineering.solver_log import iter_sidecar_entries


@pytest.fixture(params=[47,48])
def compiled_case(legacy_captured,request):
    captured=legacy_captured
    source=Path(captured.core.env.root)
    nvars=request.param;s=captured.segment;r=captured.result
    x=r['solver_backend_layers'][-1]['attempts'][0]['raw_x']
    if nvars==48:x.append(.123)
    x[44]=1.;x[14]=10.
    s['unserved_kW'][0][0]=10.;r['lost'][0][0]=10.;r['served'][0][0]=90.
    s['service_dependencies']['direct_electric_delivered_kW'][0]=90.
    s['action'].update(gt_start_electric_kW=10.,station_auxiliary_kW=10.)
    r['action']=deepcopy(s['action'])
    cfg=json.loads((ROOT/'configs/plant_configs.json').read_text(encoding='utf8'))
    perf=cfg['historical_pnnl_performance'];rt=3.51685284;energy=.30;eta=.95
    ratio=s['action']['hrsg_steam_lbph']/100.
    matrix=np.zeros((3,nvars));matrix[0,[0,1,2,11,12,38]]=1.;matrix[0,39]=-1.
    matrix[0,6]=-perf['existing_absorption_aux_kW_per_RT'];matrix[0,7:10]=-perf['existing_electric_chiller_all_in_kW_per_RT']
    matrix[0,10]=-.012*energy;matrix[0,40:42]=-.02;matrix[0,42]=-.5;matrix[0,43]=-.02;matrix[0,44]=-10.;matrix[0,45]=-.5
    matrix[1,0:3]=ratio;matrix[1,6]=-perf['existing_absorption_steam_lb_per_RT_hr'];matrix[1,10]=1.;matrix[1,13]=-1.
    matrix[2,6:10]=rt;matrix[2,40]=1.;matrix[2,41]=-1.;matrix[2,43]=-1.
    for g in range(4):matrix[0,14+3*g]=1.;matrix[1,15+3*g]=1./(energy*eta);matrix[2,16+3*g]=1.
    rhs=np.array([sum(row[0] for row in s['connected_loads_kW']),sum(row[1] for row in s['connected_loads_kW'])/(energy*eta),sum(row[2] for row in s['connected_loads_kW'])])
    integral=np.zeros(nvars,dtype=int);integral[[3,4,5,42,44,45]]=1
    row_names=[dict(name=name,unit=unit,kind='physical',source='pure synthetic truthful native row fixture') for name,unit in
               [('balance.electric','kW'),('balance.steam','lb/h'),('balance.cooling','kW')]]
    checked=native_validate_vector()(matrix,rhs,rhs,np.zeros(nvars),np.full(nvars,np.inf),integral,np.asarray(x),row_names)
    assert checked['max_original_violation']<=1e-7
    checked['integer_indices']=[3,4,5,42,44,45]
    r['solver_backend_layers'][-1]['attempts'][0]['validation']=checked
    values=leaf_values(nvars);values.update(raw_x=x,Aeq=matrix,beq=rhs,source_origin=deepcopy(r['_compiled_flow_input']['source_origin']))
    values['port_coefficients']=dict(RT_TO_KW=rt,abs_electric_kW_per_rt=perf['existing_absorption_aux_kW_per_RT'],
        ec_electric_kW_per_rt=perf['existing_electric_chiller_all_in_kW_per_RT'],abs_steam_lb_per_rt=perf['existing_absorption_steam_lb_per_RT_hr'],
        steam_kWh_per_lb=energy,exchanger_eff=eta,steam_per_kW=ratio,HRSG_availability=dict(s['availability']))
    r['_compiled_flow_input']=build_compiled_flow_input(**values)
    captured.core._after_actual_segment(s,r)
    captured.writer.close()
    s['service_request_ledger']=dict(original_demand_kW=deepcopy(s['loads_kW']),active_cut_kW=deepcopy(s['disconnected_loads_kW']),
        connected_unserved_kW=deepcopy(r['lost']),delivered_kW=deepcopy(r['served']))
    s['energy_balance_residuals']=[0.,0.,0.]
    if nvars==48:s['m25_reserve']=dict(control_rule=dict(name='sustained_critical'))
    calls=list(captured.core.solver_calls);ref=s[witness.FIELD]['rows_ref'];entry=next(iter_sidecar_entries(captured.writer.path))
    assert entry['record_id']==ref['record_id'] and s[witness.FIELD]['compiled_flow_input_sha256']
    metadata=calls[0]['annual_actual_witness'];pins=dict(values['source_origin']['source_files'])
    for name in (*steam.native.SOURCE_FILES,'tools/annual_program/witness.py','tools/annual_program/source_runtime.py'):
        pins[name]=hashlib.sha256((source/name).read_bytes()).hexdigest()
    native={name:pins[name] for name in contracts.LEGACY_NATIVE_SOURCE_CONTRACT}
    additional=contracts.capture_source_extra_contract(native)
    assert additional is not None
    pins.update({name:hashlib.sha256((source/name).read_bytes()).hexdigest() for name in additional})
    constants=dict(rt=rt,steam=energy,exchanger=eta,abs_steam=perf['existing_absorption_steam_lb_per_RT_hr'],ec_electric=perf['existing_electric_chiller_all_in_kW_per_RT'])
    context=dict(source_root=source,source_hashes=pins,config=cfg,constants=constants,model_config=deepcopy(captured.old.payload['model_config']))
    local=dict(directory=captured.old.producer,config=cfg,constants=constants,allow_m25_48=nvars==48,
        # New-profile block contexts retain all three balance rows only.
        entries={entry['record_id']:dict(entry,rows=deepcopy(entry['rows']))},
        sidecar_basename=captured.writer.path.name,cas_cache={},payload=dict(segments=[s],solver_calls=calls,
            decisions=[dict(decision_time_min=0.,features=dict(outdoor_temperature_C=20.))],model_config=context['model_config']))
    block=dict(segments=[s],solver_calls=calls,solver_index_base=0,solver_call_local_start_index=0)
    owned={entry['record_id']:dict(location=deepcopy(entry['location']),rows=[row for row in entry['rows'] if row['name'] in ('balance.electric','balance.cooling')])}
    return dict(context=context,local=local,block=block,segment=s,owned=owned,metadata=metadata)


def audit(case):
    return carrier_flow._segment(case['context'],case['local'],case['block'],case['segment'],0,carrier_flow._owned_resolver(),case['owned'])


def test_genuine_final_packet_allows_electric_starter_algebra_only(compiled_case):
    row=audit(compiled_case)
    assert row['raw_flow']['e']['status']==row['raw_flow']['c']['status']=='passed',row
    assert row['raw_flow']['e']['ports_kW']['GT_start']==-10.
    assert row['raw_flow']['e']['ports_kW']['building']==-90.
    assert row['compiled_context_status']=='validated_final_packet'
    assert row['full_startup_context_validated'] is row['whole_system_validated'] is False


@pytest.mark.parametrize('change',['Aeq_active','Aeq_inactive','closure_active','closure_inactive','beq','dt','origin_root','origin_source','hook_sha','receipt_hash','row_owner'])
def test_contradictory_registered_packet_cannot_qualify_even_with_rehashed_DTO(compiled_case,change):
    c=compiled_case;payload=c['metadata']['compiled_flow_input'];binding=c['metadata']['compiled_flow_binding']
    if change=='Aeq_active':payload['balance_rows']['Aeq'][0][44]=-9.
    elif change=='Aeq_inactive':payload['balance_rows']['Aeq'][0][12]=2.
    elif change=='closure_active':payload['closure']['gt_start_kW']=11.
    elif change=='closure_inactive':payload['closure']['boiler_fixed_kW']=.6
    elif change=='beq':payload['balance_rows']['beq'][0]+=1.
    elif change=='dt':payload['dt_h']=.1
    elif change=='origin_root':payload['source_origin']['source_root']='foreign_root';binding['source_root']='foreign_root'
    elif change=='origin_source':
        payload['source_origin']['source_files']['tools/annual_program/compiled_capture.py']='0'*64
        binding['source_files']=deepcopy(payload['source_origin']['source_files'])
    elif change=='hook_sha':binding['actual_hook']['sha256']='0'*64
    elif change=='receipt_hash':c['segment'][witness.FIELD]['compiled_flow_input_sha256']='0'*64
    elif change=='row_owner':next(iter(c['owned'].values()))['location'][0]=1
    if change not in ('receipt_hash','row_owner'):
        rehash(payload);binding['payload_content_sha256']=payload['content_sha256']
        c['segment'][witness.FIELD]['compiled_flow_input_sha256']=payload['content_sha256']
    row=audit(c)
    assert row['raw_flow']['e']['status']=='failed',row
    assert row['raw_flow']['e']['ports_kWh'] is None


def test_new_layout_requires_approved_erasure_source_profile(compiled_case):
    assert steam._physical_formula_profile(compiled_case['context'])==contracts.CAPTURE_PROFILE


@pytest.mark.parametrize('start',[107.,525500.])
def test_observer_compiled_clock_roundoff_keeps_packet_unchanged(compiled_case,start):
    c=compiled_case;s=c['segment'];receipt=s[witness.FIELD];call=c['block']['solver_calls'][0]
    solved_minutes=.8292027169925059
    s.update(start_minute=start,end_minute=start+solved_minutes)
    receipt.update(start_minute=start,end_minute=s['end_minute'])
    call.update(time_minute=start,dt_h=(s['end_minute']-start)/60.)
    payload=c['metadata']['compiled_flow_input'];payload['dt_h']=solved_minutes/60.
    rehash(payload);c['metadata']['compiled_flow_binding']['payload_content_sha256']=payload['content_sha256']
    receipt['compiled_flow_input_sha256']=payload['content_sha256'];before=deepcopy(payload)
    assert payload['dt_h']!=(s['end_minute']-start)/60.
    row=audit(c)
    assert row['raw_flow']['e']['status']==row['raw_flow']['c']['status']=='passed',row
    assert payload==before


@pytest.mark.parametrize('dt_h',[1/60.,True,False,float('nan'),float('inf'),-1.,0.])
def test_observer_invalid_interval_duration_remains_failed(compiled_case,dt_h):
    c=compiled_case;s=c['segment'];receipt=s[witness.FIELD];call=c['block']['solver_calls'][0]
    s.update(start_minute=107.,end_minute=107.82920271699251)
    receipt.update(start_minute=107.,end_minute=s['end_minute'])
    call.update(time_minute=107.,dt_h=(s['end_minute']-107.)/60.)
    payload=c['metadata']['compiled_flow_input'];payload['dt_h']=dt_h
    if dt_h==dt_h and dt_h not in (float('inf'),float('-inf')):
        rehash(payload);c['metadata']['compiled_flow_binding']['payload_content_sha256']=payload['content_sha256']
        receipt['compiled_flow_input_sha256']=payload['content_sha256']
    row=audit(c)
    assert row['raw_flow']['e']['status']==row['raw_flow']['c']['status']=='failed',row
