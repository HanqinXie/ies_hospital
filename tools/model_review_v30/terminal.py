"""Read actual terminal data without invoking any physical/observation method."""
from __future__ import annotations
import hashlib
import json
import math
import numpy as np

CORE_FIELDS = ('time','executed','pending_commands','pending_contact_restore',
    'pending_shed','gt_start_pending','gt_clocks','grid_bus_state',
    'grid_return_started_minute','grid_open_started_minute','active_command',
    'station','_m22_gt_progress','_m23_restart','ups','cold_store','diesel_rotor',
    'disconnected_fraction','automatic_fraction','arrived_dispatch','sequence',
    'local_down','interlock_latched','bridge_wait_started_minute','bridge_wait_fallback_reason')

def _plain(value):
    if value is None or isinstance(value,(str,bool,int)):return value
    if isinstance(value,float):return value if math.isfinite(value) else {'unknown_nonfinite':str(value)}
    if isinstance(value,np.ndarray):return _plain(value.tolist())
    if isinstance(value,np.generic):return _plain(value.item())
    if isinstance(value,dict):return {str(k):_plain(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)):return [_plain(v) for v in value]
    if isinstance(value,(set,frozenset)):return sorted((_plain(v) for v in value),key=str)
    if hasattr(value,'__dict__'):return _plain(vars(value))
    raise TypeError('Unsupported terminal state data: '+type(value).__name__)

def _capture(env):
    core=env.core
    return dict(state=_plain(vars(env.state)),
        core={key:_plain(vars(core)[key]) for key in CORE_FIELDS if key in vars(core)},
        solver_call_count=len(core.solver_calls))

def _sha(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,allow_nan=False,separators=(',',':')).encode()).hexdigest()

def terminal_snapshot(env,observed_until_minute):
    if getattr(env,'core',None) is None:
        return dict(schema='hospital_ies.actual_terminal.v30.v1',completeness='unknown_no_live_core',
            snapshot_time_minute=None,observed_until_minute=observed_until_minute,inventories=None,
            source='no live core; no zero-imputation')
    before=_capture(env);core=before['core'];state=before['state']
    stocks={}
    for key,carrier in (('ups','e'),('cold_store','c')):
        stocks[key]=dict(stock_kWh=core.get(key,{}).get('energy_kWh'),carrier=carrier,
            unit='kWh_'+carrier,source='live core.'+key+'.energy_kWh')
    value=dict(schema='hospital_ies.actual_terminal.v30.v1',source='actual live state after integration; no observation or solve',
        snapshot_time_minute=core.get('time'),observed_until_minute=observed_until_minute,
        completeness='actual_live_core',GT=dict(actual_electric_kW=state.get('gt_prev'),
            arrived_permission_kW=core.get('executed',{}).get('gt_electric_kW'),
            clocks=core.get('gt_clocks'),start_pending=core.get('gt_start_pending'),
            actual_start_progress=core.get('_m22_gt_progress')),
        DG=dict(status=state.get('diesel_status'),start_minute=state.get('diesel_start_minute'),
            actual_electric_kW=state.get('last_diesel_kW'),arrived_ceiling_kW=core.get('executed',{}).get('diesel_kW'),
            remaining_shaft_budget_kWh=state.get('diesel_energy_remaining_kWh'),
            chemical_fuel_kWh=None,rotor=core.get('diesel_rotor')),
        station=core.get('station'),restart=core.get('_m23_restart'),
        grid_transfer={k:core.get(k) for k in ('grid_bus_state','grid_return_started_minute','grid_open_started_minute')},
        pending_commands=core.get('pending_commands'),pending_contact_restore=core.get('pending_contact_restore'),
        pending_shed=core.get('pending_shed'),inventories=stocks,actual_state=state,
        execution_state=core,solver_call_count=before['solver_call_count'])
    after=_capture(env)
    unchanged=before==after
    value['side_effect_check']=dict(unchanged=unchanged,before_sha256=_sha(before),after_sha256=_sha(after),
        checks=['clock','physical_state','inventories','command_and_recovery_queues','solver_call_count'],
        observe_called=False,decision_snapshot_called=False,additional_solve_called=False)
    if not unchanged:raise RuntimeError('Terminal export mutated actual execution state')
    return value
