"""Bounded same-snapshot storage modes with an explicit recharge safeguard."""
from __future__ import annotations
import copy
import numpy as np
from hospital_ies.execution import ModelConstraintInfeasible,SolverExecutionError
from tools.model_correctness_v23.domains import select_candidate
from tools.model_correctness_v23.evidence import successful_backend_evidence,retained_backend_evidence


def search(core,solve,baseline,loads,availability,dt_h,*,baseline_failure=None):
    """Compare the original hierarchy; charging cannot borrow service slack.

    The twelve service ceilings are a conservative *execution* safeguard
    for the additional charge domain, not twelve replacement objectives.
    The no-charge winner and the witness retain the original normalized
    E/H/C objectives in P0--P3 order. All trials read the same snapshot.
    """
    records=[];noncharging=[]
    ups_modes=[False,True] if core.ups.energy_kWh<core.ups.config['capacity_kWh']-1e-9 and core.ups.config['capacity_kWh']>0. else [False]
    cold_modes=['idle']+(['discharge'] if core.cold_store.energy_kWh>1e-9 else [])
    def retain(mode,ups_charge,status,layers,*,guard_probe=False):
        # Candidate proofs use the existing ordered log and its CAS/sidecar.
        # Actual segments carry only these small summaries and log indices.
        index=len(core.solver_calls)
        evidence=successful_backend_evidence(layers) if status=='legal' else retained_backend_evidence(layers)
        core.solver_calls.append(dict(time_minute=core.time,dt_h=dt_h,phase='m24_cold_mode_candidate',
            cold_mode=mode,ups_charge=ups_charge,guard_probe=guard_probe,status='success' if status=='legal' else status,
            current_only=True,inventory_committed=False,solver_backend_layers=evidence))
        summary=[]
        for layer in layers:
            item={key:copy.deepcopy(layer[key]) for key in ('rank','layer','backend','status','numerical_version') if key in layer}
            item['attempt_statuses']=[{key:copy.deepcopy(a[key]) for key in ('attempt','status','native_status','accepted','error_type','error') if key in a} for a in layer.get('attempts',[])]
            summary.append(item)
        return dict(solver_call_index=index,backend_layer_summary=summary)
    def attempt(mode,ups_charge,extra=None,reuse=False,label=None):
        ports=core._base_ports(ups_charge=ups_charge,cold_mode=mode)
        recorded_mode=label or mode
        try:
            if reuse and ports==core._base_ports() and baseline_failure is not None:raise baseline_failure
            result=baseline if reuse and ports==core._base_ports() else solve(ports,extra)
        except (ModelConstraintInfeasible,SolverExecutionError) as exc:
            status='candidate_infeasible' if isinstance(exc,ModelConstraintInfeasible) else 'candidate_solver_error'
            records.append(dict(cold_mode=recorded_mode,ups_charge=ups_charge,status=status,message=str(exc),
                guard_probe=bool(extra),**retain(recorded_mode,ups_charge,status,getattr(exc,'solver_backend_attempts',[]),guard_probe=bool(extra))))
            return None
        records.append(dict(cold_mode=recorded_mode,ups_charge=ups_charge,status='legal',guard_probe=bool(extra),
            action=copy.deepcopy(result['action']),unserved_kW=np.asarray(result['lost']).tolist(),
            objective_hierarchy=copy.deepcopy(result['_m23_objective_hierarchy']),cost_USD_step=result['cost_USD_step'],
            **retain(recorded_mode,ups_charge,'legal',result.get('solver_backend_layers',[]),guard_probe=bool(extra))))
        return result,ports
    for mode in cold_modes:
        for ups_charge in ups_modes:
            item=attempt(mode,ups_charge,reuse=True)
            if item is not None:noncharging.append(item)
    winner=select_candidate([r for r,_ in noncharging]) if noncharging else None
    selected=next((item for item in noncharging if item[0] is winner),None)
    no_charge_status='legal' if noncharging else 'registered_domains_unavailable' if any(x['status']=='candidate_solver_error' for x in records) else 'registered_domains_infeasible'
    candidates=list(noncharging);guard=None;guard_by_result={}
    allowed=core.cold_recharge.allowed(core.cold_store.energy_kWh,core.cold_store.config['capacity_kWh'])
    necessary_sink=not noncharging and core._base_ports(cold_mode='charge')['cold_charge_kW']>0.
    deadband_bypassed=bool(necessary_sink and not allowed)
    if allowed and noncharging:
        # This extra pure witness removes service-lock slack before recording
        # service ceilings. A source-limited minimum therefore cannot fund a
        # microcharge through the final cost layer's tolerated service loss.
        try:reference=solve(selected[1],{'_m24_exact_service_guard':True})
        except (ModelConstraintInfeasible,SolverExecutionError) as exc:
            status='candidate_infeasible' if isinstance(exc,ModelConstraintInfeasible) else 'candidate_solver_error'
            records.append(dict(cold_mode='guard_witness',status=status,message=str(exc),
                **retain('guard_witness',None,status,getattr(exc,'solver_backend_attempts',[]),guard_probe=True)))
        else:
            records.append(dict(cold_mode='guard_witness',status='legal',guard_probe=True,
                action=copy.deepcopy(reference['action']),unserved_kW=np.asarray(reference['lost']).tolist(),
                objective_hierarchy=copy.deepcopy(reference['_m23_objective_hierarchy']),
                **retain('guard_witness',None,'legal',reference.get('solver_backend_layers',[]),guard_probe=True)))
            guard=np.minimum(np.asarray(reference['lost']),np.asarray(winner['lost']))
            for ups_charge in ups_modes:
                item=attempt('charge',ups_charge,{'_m24_charge_service_ceiling_kW':guard.tolist(),'_m24_exact_service_guard':True})
                if item is not None:candidates.append(item)
    elif not noncharging and (allowed or necessary_sink):
        # A fixed source can need charging as its lawful no-export sink.
        # There is no invented idle service vector. Each charge domain first
        # establishes its own full-priority, exact-service witness, which is
        # itself a legal executable alternative if a later probe fails.
        # Maintenance hysteresis is not a physical capacity limit. When
        # no noncharging domain is legal, real remaining capacity may be
        # used as the required source sink; all physical checks still apply.
        for ups_charge in ups_modes:
            witness=attempt('charge',ups_charge,{'_m24_exact_service_guard':True},label='charge_witness')
            if witness is None:continue
            guard=np.asarray(witness[0]['lost']);guard_by_result[id(witness[0])]=guard
            candidates.append(witness)
            item=attempt('charge',ups_charge,{'_m24_charge_service_ceiling_kW':guard.tolist(),'_m24_exact_service_guard':True})
            if item is not None:
                candidates.append(item);guard_by_result[id(item[0])]=guard
    if not candidates:
        errors=any(x['status']=='candidate_solver_error' for x in records)
        error=(SolverExecutionError if errors else ModelConstraintInfeasible)('M24 registered storage modes exhausted; no global physical infeasibility claim')
        error.category='registered_storage_domains_unavailable' if errors else 'registered_storage_domains_exhausted'
        error.command_compilation_diagnostic=dict(status='registered_storage_domains_exhausted',candidates=records,
            no_charge_domain_status=no_charge_status,inventory_committed=False,global_physical_infeasibility_claimed=False,
            maintenance_recharge_deadband_bypassed=deadband_bypassed)
        error.solver_backend_attempts=[dict(layer,cold_mode=record['cold_mode']) for record in records
            for layer in core.solver_calls[record['solver_call_index']].get('solver_backend_layers',[])]
        raise error
    result=select_candidate([r for r,_ in candidates])
    ports=next(p for r,p in candidates if r is result)
    mode='charge' if result['action'].get('cold_charge_kW',0.)>0. else 'discharge' if result['action'].get('cold_discharge_kW',0.)>0. else 'idle'
    guard=guard_by_result.get(id(result),guard)
    diagnostic=dict(cold_mode=mode,no_store_critical_cold_deficit_kW=None if baseline is None else float(np.asarray(baseline['lost'])[:2,2].sum()),
        candidate_count=len(candidates),candidates=records,inventory_committed=False,current_arrived_permission_only=True,
        service_objectives='original_per_priority_normalized_E_H_C_sum',
        charge_guard=('no_degradation_of_any_service_from_no_charge_winner_and_exact_service_witness' if noncharging else 'preserve_legal_charge_domain_exact_service_witness'),
        charge_guard_unserved_ceiling_kW=None if guard is None else guard.tolist(),
        no_charge_domain_status=no_charge_status,charge_guard_reference_domain='no_charge' if noncharging else 'charge',
        idle_baseline_status='legal' if baseline is not None else 'solver_error' if isinstance(baseline_failure,SolverExecutionError) else 'candidate_infeasible',
        storage_event_fix_enabled=core.env.correctness_config['storage_fix'],
        maintenance_recharge_deadband_bypassed=deadband_bypassed,
        maintenance_deadband_bypass_reason=('no_legal_registered_no_charge_domain_with_real_charge_headroom' if deadband_bypassed else None),
        original_service_lock_tolerances_unchanged=True,service_lock_slack_used_to_charge=False)
    diagnostic['candidate_solver_errors']=sum(x['status']=='candidate_solver_error' for x in records)
    diagnostic['safe_no_charge_retained_after_probe_failure']=bool(diagnostic['candidate_solver_errors'] and any(r is result for r,_ in noncharging))
    return result,ports,diagnostic
