"""Explicit lossy row-table copies for clean, unexecuted internal probes.

No model/state access, solver call, disk write, precision change or primal
replacement occurs here. None means retain the existing full evidence path.
"""
from __future__ import annotations

from copy import deepcopy
import math

ROWS_SUMMARY_SCHEMA='hospital_ies.annual_program.validation_rows_summary.v1'
SAFE_INTERNAL_PHASES=frozenset((
    'm24_cold_mode_candidate','m24_current_port_selection',
    'm22_current_port_selection','m25_selected_no_storage_witness',
    'm22_non_DG_release_witness','m23_sustained_non_DG_release_witness'))
_LIMITS={'max_original_violation':1e-7,'max_lock_violation':1e-10,
         'max_integrality_distance':1e-9}
_STATUSES=frozenset(('success','legal','solved','candidate_rejected'))


def _number(value):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):
        raise ValueError('Unknown/nonfinite engineering summary number')
    return value


def _validation(value):
    if not isinstance(value,dict) or 'rows_ref' in value:
        raise ValueError('Summary requires original inline validation')
    for key,limit in _LIMITS.items():
        if not 0.<=_number(value[key])<=limit:raise ValueError('Original validation failed')
    if 'rows_summary' in value:
        summary=value['rows_summary']
        if ('rows' in value or 'worst_rows' in value or not isinstance(summary,dict)
            or summary.get('schema')!=ROWS_SUMMARY_SCHEMA
            or summary.get('rows_retained') is not False
            or summary.get('raw_rows_recoverable') is not False):
            raise ValueError('Unknown or mixed summary structure')
        for key in ('row_count','worst_row_count'):
            if type(summary[key]) is not int or summary[key]<0:raise ValueError('Invalid row count')
        if _number(summary['max_row_violation'])<0.:raise ValueError('Invalid row violation')
        for key in _LIMITS:
            if summary[key]!=value[key]:raise ValueError('Summary validation mismatch')
        return _copy(value)
    rows=value['rows'];worst=value.get('worst_rows',[])
    if not isinstance(rows,list) or not isinstance(worst,list):raise ValueError('Unknown row table')
    violations=[]
    for row in rows+worst:
        if not isinstance(row,dict) or _number(row['violation'])<0.:raise ValueError('Unknown row')
        violations.append(row['violation'])
    copied=_copy({key:item for key,item in value.items() if key not in ('rows','worst_rows')})
    copied['rows_summary']=dict(schema=ROWS_SUMMARY_SCHEMA,row_count=len(rows),worst_row_count=len(worst),
        max_row_violation=max(violations,default=0.),rows_retained=False,raw_rows_recoverable=False,
        **{key:value[key] for key in _LIMITS})
    return copied


def _attempt(value):
    if (not isinstance(value,dict) or type(value.get('status')) is not int or value['status']!=0
        or value.get('accepted') is not True or type(value.get('attempt')) is not int or value['attempt']!=1
        or value.get('objective_consistent',True) is not True
        or value.get('native_status',2)!=2):raise ValueError('Not a single normal accepted attempt')
    raw=value['raw_x']
    if not isinstance(raw,list) or not raw:raise ValueError('Unknown original primal')
    for number in raw:_number(number)
    _validation(value['validation'])


def _layers(value):
    if not isinstance(value,list) or not value:raise ValueError('Missing backend layers')
    for layer in value:
        if (not isinstance(layer,dict) or type(layer.get('status')) is not int or layer['status']!=0
            or layer.get('backend')!='gurobipy' or not isinstance(layer.get('attempts'),list)
            or len(layer['attempts'])!=1):raise ValueError('Unknown/failing/recovered backend layer')
        _attempt(layer['attempts'][0])


def _copy(value,*,validation=False):
    if isinstance(value,list):return [_copy(item,validation=validation) for item in value]
    if not isinstance(value,dict):return deepcopy(value)
    if validation and any(key in value for key in ('rows','rows_ref','rows_summary','worst_rows')):
        return _validation(value)
    if 'solver_backend_layers' in value:_layers(value['solver_backend_layers'])
    if 'attempts' in value:
        if not isinstance(value['attempts'],list) or len(value['attempts'])!=1:
            raise ValueError('Unknown/recovered attempt chain')
        _attempt(value['attempts'][0])
    for key,item in value.items():
        if not isinstance(key,str):raise ValueError('Unknown logging key')
        if key=='status' or key.endswith('_status'):
            if not ((type(item) is int and item==(2 if key=='native_status' else 0))
                    or (isinstance(item,str) and item in _STATUSES)):
                raise ValueError('Failure or unknown status')
        if any(word in key.lower() for word in ('recover','fallback','project','error','exception','refinement')):
            if item is not None and item is not False and item!='' and item!=[] and item!={}:
                raise ValueError('Failure/recovery/projection evidence stays full')
    return {key:_copy(item,validation=key=='validation' or key.endswith('_validation'))
            for key,item in value.items()}


def summarize_backend_layers(layers):
    """Return a safe diagnostic copy, or None for full/unknown evidence."""
    try:
        _layers(layers)
        return _copy(layers)
    except OSError:raise
    except Exception:return None


def summarize_solver_call(call):
    """Keep exact log ordinal/non-row data; actual and unknown phases stay full."""
    try:
        if (not isinstance(call,dict) or call.get('phase') not in SAFE_INTERNAL_PHASES
            or call.get('status')!='success' or call.get('inventory_committed') is not False):return None
        _layers(call['solver_backend_layers'])
        return _copy(call)
    except OSError:raise
    except Exception:return None
