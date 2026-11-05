"""Offline static command inspection from one bound snapshot; never step/reset.

This is a preview on a disposable fork. It is neither an actual execution nor
a replacement for the paired ordinary-command continuations.
"""
from __future__ import annotations
import copy,traceback
import numpy as np
from hospital_ies.model import KWH_PER_MMBTU,illustrative_tariff
from tools.integrated_learning.snapshot import digest,rng_state,restore_rng
from tools.sci_engineering.policy import validate_action
from .objective_snapshot import _encode,validate_legal_history,PHYSICAL_CLASSES

# Command queues, sequence and solver/diagnostic caches are deliberately not
# asserted invariant. These are the held execution/state clocks and all typed
# physical objects; the full before/after core fingerprints are also reported.
ACTUAL_CORE_FIELDS=frozenset((
    'time','executed','active_command','failed','segments','gt_start_pending',
    'last_availability','grid_outage_start','last_shock_active',
    'automatic_disconnected','ramp_time','ramp_anchor','ramp_budget',
    'pending_shed','local_down','auto_shed_latched','connection_basis_loads',
    'disconnected_fraction','automatic_fraction','grid_bus_state',
    'bridge_wait_started_minute','_m22_gt_progress','_m22_last_dg_start'))


def _physical_projection(env):
    core=env.core
    fields={k:v for k,v in vars(core).items() if k in ACTUAL_CORE_FIELDS or
        type(v).__module__+'.'+type(v).__qualname__ in PHYSICAL_CLASSES}
    return dict(state=env.state,actual_core=fields)


def _core_fields(env):
    return {k:_encode(v) for k,v in vars(env.core).items() if k!='env'}


def _planned_cost_parts(env,command):
    joint=command.get('joint_dispatch',{});plan=joint.get('planned_current_action')
    if plan is None:raise ValueError('Static objective comparison requires the joint planner current-action record')
    _,row,_,_=env.core.data_at(float(env.core.time));plant=env.plant;dt=float(env.dt_h)
    parts=dict(GT_gas_USD_step=float(plan['gt_fuel_MMBtu_h'])*plant.gas_price_usd_per_mmbtu*dt,
        boiler_gas_USD_step=float(plan['boiler_steam_lbph'])*plant.steam_kWh_per_lb/plant.boiler_eff/KWH_PER_MMBTU*plant.gas_price_usd_per_mmbtu*dt,
        grid_purchase_USD_step=float(plan['grid_import_kW'])*illustrative_tariff(row.timestamp,plant)*dt,
        DG_planning_proxy_USD_step=float(plan['diesel_kW'])*.30*dt)
    if not all(np.isfinite(v) for v in parts.values()):raise ValueError('Nonfinite planned cost part')
    total=sum(parts.values());engineering=joint['diagnostic']['engineering']
    value=engineering.get('final_cost_USD_step')
    parts.update(energy_terms_under_existing_planning_prices_USD_step=total,
        recorded_final_optimization_objective_USD_step=value,
        unallocated_optimization_preferences_and_roundoff_USD_step=None if value is None else float(value)-total,
        actual_energy_cost_not_computed=True,
        semantics='Static planned-current energy terms under unchanged planner prices, including DG0.30 proxy; remainder is not an actual energy fee or OEM startup cost.')
    return parts


def inspect_snapshot(snapshot,controller,*,io_resources=None):
    """Generate one command on a fresh fork and return an auditable preview.

    Caller owns fresh per-branch I/O resources and must close/hash them. The
    controller is copied and sees only copied legal history/current records.
    The mother document, held physical objects and caller RNG must survive.
    No preview command is reused by the real continuation.
    """
    mother_content=snapshot.content_sha256;mother_physical=snapshot.physical_state_sha256
    saved_rng=rng_state();env=None;before=None;core_before=None;start=None;calls_before=None
    result=dict(schema='hospital_ies.v27.static_objective_inspection.v1',status='failed_static_preview',
        snapshot_content_sha256_before=mother_content,mother_physical_state_sha256_before=mother_physical,
        snapshot_binding=copy.deepcopy(snapshot.binding),snapshot_minute=float(snapshot.minute),
        policy_id=getattr(controller,'policy_id',None),controller_request=None,validated_request=None,
        controller_diagnostic=None,command=None,layers=None,planned_cost_components=None,
        actual_energy_cost_USD_step=None,startup_request=None,error=None,
        semantics='Static same-state conditional objective preview; command generation is not execution, startup completion or realized cost.')
    try:
        history=copy.deepcopy(snapshot.legal_history);current=copy.deepcopy(snapshot.current_observation)
        validate_legal_history(history,current,float(snapshot.minute))
        env=snapshot.fork(io_resources=io_resources)
        start=float(env.core.time)
        if start!=float(snapshot.minute):raise ValueError('Fork clock differs from captured decision boundary')
        before=_encode(_physical_projection(env));core_before=_core_fields(env)
        calls_before=len(env.core.solver_calls)
        result.update(clone_physical_invariants_sha256_before=digest(before),
            clone_full_core_sha256_before=digest(core_before),
            physical_invariant_core_fields=sorted(_physical_projection(env)['actual_core']),
            actual_held_dispatch_before_preview=copy.deepcopy(getattr(env.core,'executed',None)),
            current_executable_limits=copy.deepcopy(current.get('execution_limits')))
        local_controller=copy.deepcopy(controller)
        request,diagnostic=local_controller.decide(history+[copy.deepcopy(current)])
        result['controller_request']=copy.deepcopy(request)
        result['controller_diagnostic']=copy.deepcopy(diagnostic)
        validated=validate_action(request);result['validated_request']=copy.deepcopy(validated)
        command=env.core.generate_command(validated);result['command']=copy.deepcopy(command)
        if command.get('request')!=validated:raise RuntimeError('Generated command differs from validated current request')
        joint=command.get('joint_dispatch',{});d=joint.get('diagnostic',{})
        result.update(layers=copy.deepcopy(d.get('layers')),objective_order=copy.deepcopy(d.get('objective_order')),
            planner_diagnostic=copy.deepcopy(d),readiness_context=copy.deepcopy(joint.get('readiness_context')),
            cost_optimization_completed=d.get('engineering',{}).get('cost_optimization_completed'),
            planning_preference_fallbacks=copy.deepcopy(d.get('late_preference_fallbacks',[])),
            planned_current_action=copy.deepcopy(joint.get('planned_current_action')),
            new_solver_call_records=copy.deepcopy(list(env.core.solver_calls)[calls_before:]))
        if float(env.core.time)!=start or _encode(_physical_projection(env))!=before:
            raise RuntimeError('Static command preview advanced or mutated held physical execution state')
        result['planned_cost_components']=_planned_cost_parts(env,command)
        readiness=joint.get('readiness_context',{});actual=np.asarray(readiness.get('actual_gt_kW',[]),float)
        commanded=np.asarray(d.get('command_GT_on',[]),bool)
        pending=readiness.get('pending_starts')
        start_info_known=(actual.shape==(3,) and commanded.shape==(3,) and np.isfinite(actual).all()
            and isinstance(pending,list) and len(pending)==3)
        new=(np.flatnonzero(commanded & (actual<=1e-9) & np.asarray([p is None or p is False for p in pending])).tolist()
            if start_info_known else None)
        per_unit=getattr(env,'closure_config',{}).get('station',{}).get('gt_starter_kW_per_unit')
        plan=joint.get('planned_current_action',{})
        result['startup_request']=dict(new_GT_command_indices=new,
            registered_starter_kW_per_unit=per_unit,
            nominal_requested_starter_kW=None if new is None or per_unit is None else len(new)*float(per_unit),
            planned_current_GT_start_electric_kW=plan.get('gt_start_electric_kW'),
            planned_current_boiler_restart_electric_kW=plan.get('boiler_restart_electric_kW'),
            current_pending_starts=copy.deepcopy(readiness.get('pending_starts')),
            earliest_legal_start_minute=copy.deepcopy(readiness.get('earliest_start_minute')),
            actual_startup_energy_delivered_kWh=None,
            semantics='Requested/planned startup power only; actual supplied work and completed readiness require ordinary execution.')
        result['status']='static_command_generated'
    except Exception as exc:
        result['error']=dict(type=type(exc).__name__,message=str(exc),traceback=traceback.format_exc(),
            category=getattr(exc,'category','static_preview_failure'))
        for key in ('solver_backend_attempts','joint_diagnostic','command_compilation_diagnostic'):
            if hasattr(exc,key):result['error'][key]=copy.deepcopy(getattr(exc,key))
    finally:
        restore_rng(saved_rng)
        result.update(snapshot_content_sha256_after=snapshot.content_sha256,
            mother_physical_state_sha256_after=snapshot.physical_state_sha256,
            mother_unchanged=(snapshot.content_sha256==mother_content and snapshot.physical_state_sha256==mother_physical),
            caller_rng_restored=digest(_encode(rng_state()))==digest(_encode(saved_rng)))
        if env is not None and start is not None:
            after=_encode(_physical_projection(env));core_after=_core_fields(env)
            if calls_before is not None:result['new_solver_call_records']=copy.deepcopy(list(env.core.solver_calls)[calls_before:])
            result.update(physical_time_advanced_minutes=float(env.core.time)-start,
                clone_physical_invariants_sha256_after=digest(after),clone_physical_invariants_unchanged=after==before,
                clone_full_core_sha256_after=digest(core_after),
                clone_core_changed_fields=sorted(k for k in set(core_before or {})|set(core_after) if (core_before or {}).get(k)!=core_after.get(k)))
        else:result.update(physical_time_advanced_minutes=None,clone_physical_invariants_unchanged=None)
        if not result['mother_unchanged'] or not result['caller_rng_restored']:
            result['status']='failed_static_preview'
            result['error']=dict(type='RuntimeError',message='Mother snapshot or caller RNG was mutated')
    return result
