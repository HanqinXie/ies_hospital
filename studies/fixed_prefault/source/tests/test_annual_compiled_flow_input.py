"""Unused leaf-input DTO: exact types/bytes and no physical/context calls."""
from copy import deepcopy
import hashlib,importlib,json
from pathlib import Path

import numpy as np
import pytest


def api():
    path=Path(__file__).parents[1]/'tools/annual_program/compiled_flow_input.py'
    assert path.is_file(),'pure compiled leaf-flow input helper is not implemented'
    return importlib.import_module('tools.annual_program.compiled_flow_input')


def fixture(nvars=47):
    return dict(dt_h=.25,nvars=nvars,index_map=dict(gp=np.arange(3),go=np.arange(3,6),ia=6,ie=np.arange(7,10),
        ib=10,ig=11,idg=12,iw=13,iu=14,auxiliary_indices=np.arange(26,38).reshape(4,3),
        storage_indices=dict(ups_discharge_kW=38,ups_charge_kW=39,cold_discharge_kW=40,cold_charge_kW=41),
        boiler_on=42,recovery_cold=43,starter_on=44,boiler_restart_on=45,bridge_debt=46,
        cold_reserve_debt=47 if nvars==48 else None),
        closure=dict(station_ac_available=True,boiler_ready=False,boiler_fixed_kW=.5,
            boiler_kW_per_kWsteam=.012,gt_start_kW=10.,ups_energy_kWh=0.,ups_target_kWh=0.,
            ups_discharge_efficiency=.95,preserve_bridge=True,recovery_cooling_request_kW=0.,
            recovery_pump_kW_per_kW=.02,recovery_fan_kW_per_kW=0.,boiler_restart_kW=.5,
            instantaneous_diesel_current=True,research_stress_proxy=False,cold_energy_kWh=60.,
            cold_discharge_efficiency=.95,gt_starter_kW_per_unit=10.),
        storage_ports=dict(ups_discharge_kW=0.,ups_charge_kW=0.,cold_discharge_kW=0.,cold_charge_kW=0.,cold_pump_kW_per_kW=.02),
        port_coefficients=dict(RT_TO_KW=3.51685284,abs_electric_kW_per_rt=1.25,ec_electric_kW_per_rt=.75,
            abs_steam_lb_per_rt=8.,steam_kWh_per_lb=.30,exchanger_eff=.95,steam_per_kW=2.2,
            HRSG_availability=dict(HRSG_1=1.,HRSG_2=.5,HRSG_3=0.)),
        Aeq=np.eye(3,nvars),beq=np.array([100.,20.,30.]),raw_x=np.arange(nvars,dtype=float),
        domain_guard_inputs={'_m23_boiler_domain':'off','_m24_exact_service_guard':False,
            '_m24_charge_service_ceiling_kW':np.zeros((4,3))},
        source_origin=dict(schema='hospital_ies.annual_program.compiled_flow_source_origin.v1',
            source_root='synthetic_fixture_only',source_files={'fixture/compiler.py':'a'*64,'fixture/plant.json':'b'*64},
            leaf_function='fixture.solve_physical'))


def wire(value):return json.dumps(value,ensure_ascii=False,allow_nan=False,sort_keys=True,separators=(',',':')).encode('utf8')
def rehash(payload):
    payload['content_sha256']=hashlib.sha256(wire({k:v for k,v in payload.items() if k!='content_sha256'})).hexdigest()


@pytest.mark.parametrize('nvars',[47,48])
def test_explicit_numpy_leaf_locals_are_detached_and_not_actual(nvars):
    module=api();values=fixture(nvars);original_raw=values['raw_x'].copy()
    payload=module.build_compiled_flow_input(**values)
    assert module.validate_compiled_flow_input(payload,raw_x=original_raw)==payload
    assert payload['schema']==module.SCHEMA
    assert payload['scope']=='one_completed_leaf_physical_solve_input'
    assert payload['actual_execution_bound'] is payload['physics_validation_performed'] is payload['source_verification_performed'] is False
    assert payload['nvars']==payload['raw_x_length']==nvars
    expected=hashlib.sha256(json.dumps(original_raw.tolist(),ensure_ascii=False,allow_nan=False,separators=(',',':')).encode()).hexdigest()
    assert payload['raw_x_sha256']==expected
    assert payload['balance_rows']['names']==['balance.electric','balance.steam','balance.cooling']
    assert payload['balance_rows']['units']==['kW','lb/h','kW']
    assert payload['balance_rows']['Aeq']==values['Aeq'].tolist()
    snapshot=wire(payload)
    values['Aeq'][0,0]=99.;values['beq'][0]=99.;values['raw_x'][0]=99.
    values['index_map']['gp'][0]=99;values['closure']['gt_start_kW']=99.
    values['storage_ports']['cold_pump_kW_per_kW']=99.;values['source_origin']['source_files']['fixture/compiler.py']='c'*64
    values['domain_guard_inputs']['_m24_charge_service_ceiling_kW'][0,0]=99.
    assert wire(payload)==snapshot
    assert module.validate_compiled_flow_input(payload,raw_x=original_raw)==payload
    with pytest.raises(ValueError):module.validate_compiled_flow_input(payload,raw_x=values['raw_x'])


@pytest.mark.parametrize('closure,ports',[(None,None),(None,'present'),('present',None)])
def test_explicit_absence_is_preserved_without_zero_or_default_filling(closure,ports):
    module=api();values=fixture()
    if closure is None:values['closure']=None
    if ports is None:values['storage_ports']=None
    result=module.build_compiled_flow_input(**values)
    assert result['input_absence']==dict(closure=closure is None,storage_ports=ports is None)
    assert (result['closure'] is None)==(closure is None)
    assert (result['storage_ports'] is None)==(ports is None)
    assert result['physics_validation_performed'] is False


def test_equal_valued_distinct_contexts_are_fresh_objects_not_execution_identity():
    module=api();first=fixture();second=fixture()
    a=module.build_compiled_flow_input(**first);b=module.build_compiled_flow_input(**second)
    assert a==b and a is not b and a['closure'] is not b['closure']
    a['closure']['gt_start_kW']=50.
    assert b['closure']['gt_start_kW']==10.
    second['domain_guard_inputs'].pop('_m24_exact_service_guard')
    c=module.build_compiled_flow_input(**second)
    assert c['content_sha256']!=b['content_sha256']
    assert c['raw_x_sha256']==b['raw_x_sha256']
    assert c['actual_execution_bound'] is False


@pytest.mark.parametrize('value',[-0.0,0.0,0,1,1.0,5e-324,1.0000000000000002,np.float32(.1),np.float64(-0.),np.uint64(2**63+1)])
def test_numeric_values_and_types_are_not_rounded_or_boolean_coerced(value):
    module=api();values=fixture();values['raw_x']=values['raw_x'].tolist();values['raw_x'][0]=value
    values['Aeq']=values['Aeq'].tolist();values['Aeq'][0][0]=value
    payload=module.build_compiled_flow_input(**values)
    actual=payload['balance_rows']['Aeq'][0][0]
    expected=value.item() if isinstance(value,np.generic) else value
    assert type(actual) is type(expected)
    assert wire(actual)==wire(expected)
    assert module.validate_compiled_flow_input(payload,raw_x=values['raw_x'])==payload


def test_signed_zero_and_int_float_remain_distinct_in_vector_and_content_hashes():
    module=api();outputs=[]
    for value in (-0.0,0.0,0):
        values=fixture();values['raw_x']=values['raw_x'].tolist();values['raw_x'][0]=value
        outputs.append(module.build_compiled_flow_input(**values))
    assert len({p['raw_x_sha256'] for p in outputs})==3
    assert len({p['content_sha256'] for p in outputs})==3


@pytest.mark.parametrize('change',[
    'negative_dt','zero_dt','bool_dt','float_nvars','bool_nvars','wrong_nvars','raw_shape','matrix_shape','rhs_shape',
    'raw_bool','matrix_bool','port_bool','availability_bool','index_float','index_bool','index_shift',
    'closure_missing','closure_extra','closure_boolean_number','storage_missing','storage_extra',
    'coefficient_missing','coefficient_extra','availability_extra','domain_extra','guard_number','guard_shape',
    'source_schema','source_extra','source_missing','source_sha','source_path','source_empty',
])
def test_missing_or_wrong_typed_leaf_inputs_are_rejected(change):
    module=api();v=fixture()
    if change=='negative_dt':v['dt_h']=-1.
    elif change=='zero_dt':v['dt_h']=-0.
    elif change=='bool_dt':v['dt_h']=True
    elif change=='float_nvars':v['nvars']=47.
    elif change=='bool_nvars':v['nvars']=True
    elif change=='wrong_nvars':v['nvars']=46
    elif change=='raw_shape':v['raw_x']=np.zeros((1,47))
    elif change=='matrix_shape':v['Aeq']=np.zeros((3,48))
    elif change=='rhs_shape':v['beq']=np.zeros((3,1))
    elif change=='raw_bool':v['raw_x']=[True]+[0.]*46
    elif change=='matrix_bool':v['Aeq']=[[False]*47 for _ in range(3)]
    elif change=='port_bool':v['port_coefficients']['steam_per_kW']=True
    elif change=='availability_bool':v['port_coefficients']['HRSG_availability']['HRSG_1']=True
    elif change=='index_float':v['index_map']['ia']=6.
    elif change=='index_bool':v['index_map']['gp']=[False,1,2]
    elif change=='index_shift':v['index_map']['cold_reserve_debt']=47
    elif change=='closure_missing':del v['closure']['gt_start_kW']
    elif change=='closure_extra':v['closure']['invented_pressure']=1.
    elif change=='closure_boolean_number':v['closure']['station_ac_available']=1
    elif change=='storage_missing':del v['storage_ports']['cold_pump_kW_per_kW']
    elif change=='storage_extra':v['storage_ports']['future_port']=1.
    elif change=='coefficient_missing':del v['port_coefficients']['abs_electric_kW_per_rt']
    elif change=='coefficient_extra':v['port_coefficients']['invented_OEM_efficiency']=.99
    elif change=='availability_extra':v['port_coefficients']['HRSG_availability']['HRSG_4']=1.
    elif change=='domain_extra':v['domain_guard_inputs']['_future_context']=1.
    elif change=='guard_number':v['domain_guard_inputs']['_m24_exact_service_guard']=1
    elif change=='guard_shape':v['domain_guard_inputs']['_m24_charge_service_ceiling_kW']=[[0.]*3]*3
    elif change=='source_schema':v['source_origin']['schema']='future.v2'
    elif change=='source_extra':v['source_origin']['backend_selected_location']=[0]
    elif change=='source_missing':del v['source_origin']['source_files']
    elif change=='source_sha':v['source_origin']['source_files']['fixture/compiler.py']='not-a-SHA'
    elif change=='source_path':v['source_origin']['source_files']['../outside']='c'*64
    elif change=='source_empty':v['source_origin']['source_files']={}
    with pytest.raises(ValueError):module.build_compiled_flow_input(**v)


@pytest.mark.parametrize('target',['dt','raw','matrix','rhs','closure','storage','coefficient','availability','guard'])
@pytest.mark.parametrize('bad',[float('nan'),float('inf'),float('-inf')])
def test_nonfinite_values_are_never_normalized_to_null(target,bad):
    module=api();v=fixture()
    if target=='dt':v['dt_h']=bad
    elif target=='raw':v['raw_x'][0]=bad
    elif target=='matrix':v['Aeq'][0,0]=bad
    elif target=='rhs':v['beq'][0]=bad
    elif target=='closure':v['closure']['gt_start_kW']=bad
    elif target=='storage':v['storage_ports']['cold_pump_kW_per_kW']=bad
    elif target=='coefficient':v['port_coefficients']['steam_per_kW']=bad
    elif target=='availability':v['port_coefficients']['HRSG_availability']['HRSG_1']=bad
    elif target=='guard':v['domain_guard_inputs']['_m24_charge_service_ceiling_kW'][0,0]=bad
    with pytest.raises(ValueError):module.build_compiled_flow_input(**v)


@pytest.mark.parametrize('change',['content_sha','raw_sha','raw_length','schema','scope','extra','actual_flag','physics_flag',
    'source_flag','rows_order','units','typed_dt','typed_nvars','index','closure_key'])
def test_payload_validation_is_strict_even_if_corruptor_rehashes(change):
    module=api();v=fixture();payload=module.build_compiled_flow_input(**v)
    if change=='content_sha':payload['content_sha256']='0'*64
    elif change=='raw_sha':payload['raw_x_sha256']='0'*64
    elif change=='raw_length':payload['raw_x_length']=47.
    elif change=='schema':payload['schema']='future.v2'
    elif change=='scope':payload['scope']='final_actual'
    elif change=='extra':payload['selected_by_action']=True
    elif change=='actual_flag':payload['actual_execution_bound']=True
    elif change=='physics_flag':payload['physics_validation_performed']=True
    elif change=='source_flag':payload['source_verification_performed']=True
    elif change=='rows_order':payload['balance_rows']['names'].reverse()
    elif change=='units':payload['balance_rows']['units'][1]='kW'
    elif change=='typed_dt':payload['dt_h']=True
    elif change=='typed_nvars':payload['nvars']=47.
    elif change=='index':payload['index_map']['gp'][0]=True
    elif change=='closure_key':payload['closure']['pressure']=1.
    if change!='content_sha':rehash(payload)
    with pytest.raises(ValueError):module.validate_compiled_flow_input(payload,raw_x=v['raw_x'])


def test_arbitrary_tolist_or_array_protocol_is_not_executed():
    module=api();v=fixture()
    class Bomb:
        def tolist(self):raise AssertionError('unknown tolist executed')
        def __array__(self,*args,**kwargs):raise AssertionError('unknown array protocol executed')
    object_array=np.empty(47,dtype=object)
    object_array.fill(Bomb())  # Build the adversary without NumPy coercing it first.
    for value in (Bomb(),object_array,np.zeros(47,dtype=complex)):
        v['raw_x']=value
        with pytest.raises(ValueError):module.build_compiled_flow_input(**v)


def test_no_module_imports_or_calls_to_native_state_or_model_constructors():
    module=api();path=Path(module.__file__);source=path.read_text(encoding='utf8')
    import ast
    tree=ast.parse(source)
    forbidden={'current_context','_current_context','snapshot','data_at','solve_physical','milp','HospitalIESEnv'}
    assert not {n.func.attr if isinstance(n.func,ast.Attribute) else n.func.id if isinstance(n.func,ast.Name) else ''
        for n in ast.walk(tree) if isinstance(n,ast.Call)}&forbidden
    assert not any(isinstance(n,ast.ImportFrom) and n.module and
        (n.module.startswith('hospital_ies') or n.module.startswith('tools.')) for n in ast.walk(tree))


def test_unknown_numpy_scalar_subclasses_do_not_execute_properties():
    module=api();v=fixture()
    class ScalarBomb(np.float64):
        @property
        def dtype(self):raise AssertionError('unknown numpy subclass property executed')
    v['raw_x']=[ScalarBomb(1.)]+[0.]*46
    with pytest.raises(ValueError):module.build_compiled_flow_input(**v)


def test_validator_return_is_detached_and_missing_guard_differs_from_explicit_false():
    module=api();v=fixture();payload=module.build_compiled_flow_input(**v)
    validated=module.validate_compiled_flow_input(payload,raw_x=v['raw_x'])
    assert validated==payload and validated is not payload
    validated['balance_rows']['Aeq'][0][0]=55.
    validated['source_origin']['source_files']['fixture/compiler.py']='c'*64
    assert payload['balance_rows']['Aeq'][0][0]==1.
    assert payload['source_origin']['source_files']['fixture/compiler.py']=='a'*64
    v['domain_guard_inputs']={}
    absent=module.build_compiled_flow_input(**v)
    v['domain_guard_inputs']={'_m24_exact_service_guard':False,'_m24_charge_service_ceiling_kW':None}
    explicit=module.build_compiled_flow_input(**v)
    assert absent['content_sha256']!=explicit['content_sha256']
    assert absent['domain_guard_inputs']=={}
    assert explicit['domain_guard_inputs']['_m24_charge_service_ceiling_kW'] is None


@pytest.mark.parametrize('change',['negative_port','zero_conversion','availability_range','efficiency_range','domain_value','cycle'])
def test_original_normalized_ranges_and_cycles_are_rejected(change):
    module=api();v=fixture()
    if change=='negative_port':v['storage_ports']['cold_charge_kW']=-1.
    elif change=='zero_conversion':v['port_coefficients']['steam_kWh_per_lb']=0.
    elif change=='availability_range':v['port_coefficients']['HRSG_availability']['HRSG_1']=1.1
    elif change=='efficiency_range':v['closure']['ups_discharge_efficiency']=0.
    elif change=='domain_value':v['domain_guard_inputs']['_m23_boiler_domain']='chosen_by_action'
    else:
        cycle=[];cycle.append(cycle);v['raw_x']=cycle
    with pytest.raises(ValueError):module.build_compiled_flow_input(**v)
