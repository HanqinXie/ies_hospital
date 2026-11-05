"""Pure capture/validation of explicit completed-leaf flow inputs.

No native module, state query, filesystem lookup, solver or model constructor.
Callers supply the already-used locals and source assertions. This DTO proves
neither source authenticity, physical feasibility nor actual execution. Its
content SHA is not a solve/context identity; a later exact-final hook must bind
the selected result. None remains explicit absence and no coefficient is filled.
"""
from __future__ import annotations

import hashlib
import json
import math
import re

import numpy as np

SCHEMA='hospital_ies.annual_program.compiled_flow_input.v1'
SOURCE_SCHEMA='hospital_ies.annual_program.compiled_flow_source_origin.v1'
MINUTE1_SOURCE_SCHEMA='hospital_ies.minute1.compiled_flow_source_origin.v1'
MINUTE_THERMAL_SOURCE_SCHEMA='hospital_ies.minute1.thermal_compiled_flow_source_origin.v2'
SCOPE='one_completed_leaf_physical_solve_input'
_BOOL_CLOSURE=frozenset(('station_ac_available','boiler_ready','preserve_bridge',
    'instantaneous_diesel_current','research_stress_proxy'))
_NUMBER_CLOSURE=frozenset(('boiler_fixed_kW','boiler_kW_per_kWsteam','gt_start_kW',
    'ups_energy_kWh','ups_target_kWh','ups_discharge_efficiency','recovery_cooling_request_kW',
    'recovery_pump_kW_per_kW','recovery_fan_kW_per_kW','boiler_restart_kW',
    'cold_energy_kWh','cold_discharge_efficiency','gt_starter_kW_per_unit'))
_STORES=('ups_discharge_kW','ups_charge_kW','cold_discharge_kW','cold_charge_kW')
_COEFFICIENTS=frozenset(('RT_TO_KW','abs_electric_kW_per_rt','ec_electric_kW_per_rt',
    'abs_steam_lb_per_rt','steam_kWh_per_lb','exchanger_eff','steam_per_kW','HRSG_availability'))
_GUARDS=frozenset(('_m23_boiler_domain','_m24_exact_service_guard','_m24_charge_service_ceiling_kW'))
_BODY_KEYS=frozenset(('schema','scope','actual_execution_bound','physics_validation_performed',
    'source_verification_performed','dt_h','nvars','index_map','closure','storage_ports',
    'port_coefficients','balance_rows','raw_x_sha256','raw_x_length','domain_guard_inputs',
    'source_origin','input_absence'))
_NUMPY_SCALARS=frozenset((np.bool_,np.int8,np.int16,np.int32,np.int64,
    np.uint8,np.uint16,np.uint32,np.uint64,np.int_,np.uint,np.intc,np.uintc,
    np.intp,np.uintp,np.longlong,np.ulonglong,np.float16,np.float32,np.float64,np.longdouble))


def _plain(value,active=None):
    """Exact builtin/NumPy numeric conversion; no arbitrary object protocols."""
    kind=type(value)
    if value is None or kind in (str,bool,int):return value
    if kind is float:
        if not math.isfinite(value):raise ValueError('Nonfinite leaf input')
        return value
    if kind is np.ndarray:
        if value.dtype.kind not in 'biuf' or value.dtype.kind=='f' and value.dtype.itemsize>8:
            raise ValueError('Unsupported or precision-losing NumPy array dtype')
        return _plain(np.ndarray.tolist(value),active)
    if kind in _NUMPY_SCALARS:
        if value.dtype.kind=='f':
            if value.dtype.itemsize>8:raise ValueError('Extended NumPy float cannot be silently rounded')
            return _plain(np.float64(value).item(),active)
        if value.dtype.kind in 'biu':return _plain(np.generic.item(value),active)
        raise ValueError('Unsupported NumPy scalar dtype')
    if kind not in (dict,list,tuple):raise ValueError('Unsupported leaf input type: '+kind.__name__)
    active=set() if active is None else active
    if id(value) in active:raise ValueError('Cyclic leaf input')
    active.add(id(value))
    try:
        if kind is dict:
            if any(type(key) is not str for key in value):raise ValueError('String metadata keys required')
            return {key:_plain(item,active) for key,item in value.items()}
        return [_plain(item,active) for item in value]
    finally:active.remove(id(value))


def _wire(value):
    return json.dumps(value,ensure_ascii=False,allow_nan=False,sort_keys=True,separators=(',',':')).encode('utf8')


def _sha(value):return hashlib.sha256(_wire(value)).hexdigest()


def _keys(value,expected,name):
    if type(value) is not dict or set(value)!=set(expected):raise ValueError('Missing/unknown keys: '+name)


def _number(value,name,*,nonnegative=False,positive=False):
    if type(value) not in (int,float) or type(value) is float and not math.isfinite(value):
        raise ValueError('Finite numeric value required: '+name)
    if nonnegative and value<0 or positive and value<=0:raise ValueError('Invalid numeric range: '+name)


def _integer(value,name):
    if type(value) is not int:raise ValueError('Exact integer required: '+name)


def _matrix(value,rows,columns,name,*,nonnegative=False):
    if type(value) is not list or len(value)!=rows:raise ValueError('Wrong matrix row shape: '+name)
    for row in value:
        if type(row) is not list or len(row)!=columns:raise ValueError('Wrong matrix column shape: '+name)
        for item in row:_number(item,name,nonnegative=nonnegative)


def _same_typed(value,expected):
    if type(value) is not type(expected):return False
    if type(value) is dict:return set(value)==set(expected) and all(_same_typed(value[k],expected[k]) for k in value)
    if type(value) is list:return len(value)==len(expected) and all(_same_typed(a,b) for a,b in zip(value,expected))
    return value==expected


def _index_map(value,nvars):
    # Validation of explicitly supplied actual indices, not default generation.
    # Native idx(g,k)=iu+3*g+k; tracking-free 47/48 M22/M25 layout only.
    expected=dict(gp=[0,1,2],go=[3,4,5],ia=6,ie=[7,8,9],ib=10,ig=11,idg=12,iw=13,iu=14,
        auxiliary_indices=[[26+3*g+k for k in range(3)] for g in range(4)],
        storage_indices=dict(zip(_STORES,range(38,42))),boiler_on=42,recovery_cold=43,
        starter_on=44,boiler_restart_on=45,bridge_debt=46,cold_reserve_debt=47 if nvars==48 else None)
    if not _same_typed(value,expected):raise ValueError('Unsupported/wrong typed actual 47/48 index map')


def _closure(value):
    if value is None:return
    _keys(value,_BOOL_CLOSURE|_NUMBER_CLOSURE,'normalized closure')
    for key in _BOOL_CLOSURE:
        if type(value[key]) is not bool:raise ValueError('Explicit normalized closure boolean required: '+key)
    for key in _NUMBER_CLOSURE:_number(value[key],key,nonnegative=True)
    for key in ('ups_discharge_efficiency','cold_discharge_efficiency'):
        if not 0<value[key]<=1:raise ValueError('Invalid original normalized discharge efficiency')
    if value['recovery_cooling_request_kW']>0 and value['recovery_fan_kW_per_kW']<=0:
        raise ValueError('Original normalized active recovery requires fan electricity')


def _storage(value):
    if value is None:return
    _keys(value,set(_STORES)|{'cold_pump_kW_per_kW'},'storage ports')
    for key,item in value.items():_number(item,key,nonnegative=True)


def _coefficients(value):
    _keys(value,_COEFFICIENTS,'actual port coefficients')
    for key in _COEFFICIENTS-{'HRSG_availability'}:
        _number(value[key],key,nonnegative=True,positive=key in ('RT_TO_KW','steam_kWh_per_lb','exchanger_eff'))
    _keys(value['HRSG_availability'],{'HRSG_1','HRSG_2','HRSG_3'},'actual HRSG availability')
    for key,item in value['HRSG_availability'].items():
        _number(item,key,nonnegative=True)
        if item>1:raise ValueError('HRSG availability exceeds original fraction range')


def _sha_string(value,name):
    if type(value) is not str or re.fullmatch('[0-9a-f]{64}',value) is None:raise ValueError('Invalid SHA256: '+name)


def _source(value):
    if type(value) is not dict:raise ValueError('Explicit source origin mapping required')
    thermal=value.get('schema')==MINUTE_THERMAL_SOURCE_SCHEMA
    minute=value.get('schema') in (MINUTE1_SOURCE_SCHEMA,MINUTE_THERMAL_SOURCE_SCHEMA)
    keys={'schema','source_root','source_files','leaf_function'}
    if minute:keys|={'source_profile','source_admission'}
    if thermal:keys.add('native_revision_contract')
    _keys(value,keys,'explicit source origin')
    if value['schema'] not in (SOURCE_SCHEMA,MINUTE1_SOURCE_SCHEMA,MINUTE_THERMAL_SOURCE_SCHEMA):raise ValueError('Unsupported source-origin schema')
    if minute:
        profile='single_fault_minute1_thermal_pnnl_exact_fuel_capture_v2' if thermal else 'single_fault_minute1_pnnl_exact_fuel_capture_v1'
        if value['source_profile']!=profile:raise ValueError('Unsupported minute source profile')
        reference=value['source_admission'];_keys(reference,{'path','sha256'},'explicit minute source admission')
        if type(reference['path']) is not str or not reference['path'].strip():raise ValueError('Explicit minute source admission path required')
        _sha_string(reference['sha256'],'minute source admission')
    if thermal:
        revision=value['native_revision_contract'];_keys(revision,{'schema','revision_id','source_files'},'explicit thermal native revision')
        if (revision['schema']!='hospital_ies.minute1.thermal_native_revision.v2'
                or revision['revision_id']!='essential_actual_native_minute_thermal_reference_v2'):
            raise ValueError('Unsupported explicit thermal native revision')
        _keys(revision['source_files'],{'tools/essential_fixes/execution.py'},'single Essential native revision')
        _sha_string(revision['source_files']['tools/essential_fixes/execution.py'],'Essential native revision')
    for key in ('source_root','leaf_function'):
        if type(value[key]) is not str or not value[key].strip():raise ValueError('Explicit source assertion required: '+key)
    files=value['source_files']
    if type(files) is not dict or not files:raise ValueError('Explicit source file SHA mapping required')
    for path,sha in files.items():
        if not path or any(part in ('','.','..') for part in path.split('/')) or '\\' in path or ':' in path:
            raise ValueError('Canonical relative source-file path required')
        _sha_string(sha,path)


def _guards(value):
    if type(value) is not dict or set(value)-_GUARDS:raise ValueError('Unknown domain/guard input keys')
    if '_m23_boiler_domain' in value and value['_m23_boiler_domain'] not in ('off','run'):
        raise ValueError('Unsupported original compiled boiler domain')
    if '_m24_exact_service_guard' in value and type(value['_m24_exact_service_guard']) is not bool:
        raise ValueError('Exact original service-guard boolean required')
    ceiling=value.get('_m24_charge_service_ceiling_kW')
    if ceiling is not None:_matrix(ceiling,4,3,'original charge service ceiling',nonnegative=True)


def _vector(raw_x,nvars):
    if type(raw_x) is not list or len(raw_x)!=nvars:raise ValueError('Raw vector shape differs from nvars')
    for value in raw_x:_number(value,'raw_x')


def _validate_body(body,raw_x):
    _keys(body,_BODY_KEYS,'compiled flow payload body')
    if body['schema']!=SCHEMA or body['scope']!=SCOPE:raise ValueError('Unsupported compiled flow schema/scope')
    for key in ('actual_execution_bound','physics_validation_performed','source_verification_performed'):
        if type(body[key]) is not bool or body[key] is not False:raise ValueError('Leaf input cannot assert qualification: '+key)
    _number(body['dt_h'],'dt_h',positive=True);_integer(body['nvars'],'nvars')
    nvars=body['nvars']
    if nvars not in (47,48):raise ValueError('Only reviewed 47/48 leaf layouts are supported')
    _index_map(body['index_map'],nvars);_closure(body['closure']);_storage(body['storage_ports'])
    _coefficients(body['port_coefficients']);_guards(body['domain_guard_inputs']);_source(body['source_origin'])
    expected_absence=dict(closure=body['closure'] is None,storage_ports=body['storage_ports'] is None)
    if not _same_typed(body['input_absence'],expected_absence):raise ValueError('Input absence markers contradict explicit values')
    rows=body['balance_rows'];_keys(rows,{'names','units','Aeq','beq'},'original equality rows')
    if not _same_typed(rows['names'],['balance.electric','balance.steam','balance.cooling']) or not _same_typed(rows['units'],['kW','lb/h','kW']):
        raise ValueError('Original E/steam/C row order/units required')
    _matrix(rows['Aeq'],3,nvars,'Aeq')
    if type(rows['beq']) is not list or len(rows['beq'])!=3:raise ValueError('Original beq vector shape required')
    for value in rows['beq']:_number(value,'beq')
    _integer(body['raw_x_length'],'raw_x_length')
    if body['raw_x_length']!=nvars:raise ValueError('Raw length differs from nvars')
    _sha_string(body['raw_x_sha256'],'raw_x_sha256');_vector(raw_x,nvars)
    if _sha(raw_x)!=body['raw_x_sha256']:raise ValueError('Explicit original raw vector SHA mismatch')


def build_compiled_flow_input(*,dt_h,nvars,index_map,closure,storage_ports,port_coefficients,
        Aeq,beq,raw_x,domain_guard_inputs,source_origin):
    """Copy explicit already-used leaf locals; no defaults, IO or recomputation.

    None closure/ports remain absence. Non-None closure must have the complete
    native normalized key set; ports/coefficient dictionaries must be complete.
    Arrays/NumPy scalars convert exactly to JSON primitives. Extended floats,
    arbitrary tolist/__array__ objects and nonfinite values are rejected.
    """
    raw=_plain(raw_x)
    body=_plain(dict(schema=SCHEMA,scope=SCOPE,actual_execution_bound=False,
        physics_validation_performed=False,source_verification_performed=False,
        dt_h=dt_h,nvars=nvars,index_map=index_map,closure=closure,storage_ports=storage_ports,
        port_coefficients=port_coefficients,balance_rows=dict(names=['balance.electric','balance.steam','balance.cooling'],
            units=['kW','lb/h','kW'],Aeq=Aeq,beq=beq),raw_x_sha256=_sha(raw),raw_x_length=len(raw) if type(raw) is list else -1,
        domain_guard_inputs=domain_guard_inputs,source_origin=source_origin,
        input_absence=dict(closure=closure is None,storage_ports=storage_ports is None)))
    _validate_body(body,raw)
    return dict(body,content_sha256=_sha(body))


def validate_compiled_flow_input(payload,*,raw_x):
    """Validate with the caller's exact vector; return a fresh detached copy.

    SHA identifies typed content, not a unique solve/candidate/actual interval.
    This does not test Aeq*x, reconstruct coefficients or authenticate source.
    """
    value=_plain(payload);_keys(value,_BODY_KEYS|{'content_sha256'},'compiled flow payload')
    _sha_string(value['content_sha256'],'content_sha256')
    body={key:item for key,item in value.items() if key!='content_sha256'}
    _validate_body(body,_plain(raw_x))
    if _sha(body)!=value['content_sha256']:raise ValueError('Compiled flow content SHA mismatch')
    return value
