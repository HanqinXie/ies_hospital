"""Small transactional boiler domains; no positive steam minimum is invented."""
from __future__ import annotations
import copy
import numpy as np
from hospital_ies.execution import ModelConstraintInfeasible, SolverExecutionError
from .evidence import successful_backend_evidence


def select_candidate(results):
    """Use the solver's original objectives and lock widths, without rounding.

    Candidate-local locks never cross solves. The final-value comparison
    retains each original physical objective in its original order, then
    uses actual operating cost for deterministic cost-equivalent ties.
    """
    order=(0,1,'start','bridge',2,3,'ups_current_service_only','charge','recovery','cost')
    pool=list(results)
    for name in order:
        values=[]
        for result in pool:
            item=next((x for x in result['_m23_objective_hierarchy'] if x['name']==name),None)
            values.append((result,float(item['value']) if item else 0.,float(item['lock_tolerance']) if item else 0.))
        best=min(values,key=lambda x:x[1]);limit=best[1]+best[2]
        pool=[result for result,value,_ in values if value<=limit]
        if len(pool)==1:return pool[0]
    return min(pool,key=lambda item:(item['cost_USD_step'],int(item['action'].get('boiler_station_running',False))))


def solve_execution(arguments, solver):
    """Re-solve off and genuinely running domains from one unmodified snapshot.

    A strict positive domain is open because no OEM minimum is registered.
    A run solution at exactly zero steam is rejected, never repaired by an
    epsilon sink. Exhaustion is scoped to these registered domains.
    """
    records=[];valid=[];errors=[]
    for domain in ('off','run'):
        trial=dict(arguments);trial['config']=copy.deepcopy(arguments.get('config') or {})
        trial['config']['_m23_boiler_domain']=domain
        try:
            result=solver(**trial)
        except ModelConstraintInfeasible as exc:
            records.append(dict(domain=domain,status='checked_domain_infeasible',message=str(exc),solver_backend_layers=getattr(exc,'solver_backend_attempts',[])))
            continue
        except OSError:
            raise
        except Exception as exc:
            records.append(dict(domain=domain,status='compiler_or_solver_error',exception_type=type(exc).__name__,message=str(exc),solver_backend_layers=getattr(exc,'solver_backend_attempts',[])));errors.append(exc)
            continue
        if domain=='run' and (not result['action'].get('boiler_station_running') or result['action']['boiler_steam_lbph']<=0.):
            records.append(dict(domain=domain,status='candidate_rejected',reason='run_domain_has_no_actual_positive_steam',solver_backend_layers=result.get('solver_backend_layers',[])))
            continue
        # Evidence failure is a run failure, never a reason to discard a
        # lawful OFF candidate and execute another (possibly dearer) domain.
        evidence=successful_backend_evidence(result.get('solver_backend_layers',[]))
        records.append(dict(domain=domain,status='legal',solver_backend_layers=evidence))
        valid.append(result)
    diagnostic=dict(status='legal_domain_selected' if valid else 'registered_domains_exhausted',
        domains=records,inventory_committed=False,global_physical_infeasibility_claimed=False,
        current_arrived_permission_only=True,positive_minimum_invented=False)
    if not valid:
        cls=SolverExecutionError if errors else ModelConstraintInfeasible
        error=cls('M23 boiler registered off/run domains exhausted; no global physical infeasibility claim')
        error.category='boiler_domain_compiler_gap' if errors else 'registered_boiler_domains_exhausted'
        error.command_compilation_diagnostic=diagnostic
        error.solver_backend_attempts=[dict(layer,domain=record['domain'],domain_status=record['status']) for record in records for layer in record.get('solver_backend_layers',[])]
        raise error from (errors[0] if errors else None)
    result=select_candidate(valid)
    result['m23_boiler_domains']=diagnostic
    return result


def solve_command(arguments, solver):
    """Close only actually encountered off/run coordinates, fully reoptimized.

    Each discarded station-on/zero-steam candidate is retained. Fresh solves
    share the input snapshot and no lexicographic locks cross a candidate.
    """
    domains={};records=[];last=None
    for attempt in range(48):
        trial=dict(arguments);trial['_m23_boiler_domains']=dict(domains)
        try:result=solver(**trial)
        except OSError:
            raise
        except Exception as exc:
            error=SolverExecutionError('M23 checked boiler command domains unavailable; no global physical infeasibility claim')
            error.category='command_compilation_domain_exhausted' if isinstance(exc,ModelConstraintInfeasible) else 'command_compilation_implementation_gap'
            error.command_compilation_diagnostic=dict(status=error.category,checked_domains=records,
                global_physical_infeasibility_claimed=False,positive_command_domain_searched=True,
                full_priority_hierarchy_reoptimized=True,prior_candidate_locks_reused=False)
            error.solver_backend_attempts=getattr(exc,'solver_backend_attempts',[])
            raise error from exc
        truth=result['joint_diagnostic']['m22_boiler_command_truth'];stages=truth['stages']
        invalid=[name for name,value in stages.items() if value['station_on_value']>.5 and value['actual_steam_lbph']<=0.]
        invalid+=truth.get('zero_permission_phantom_stages',[])
        invalid=sorted(set(invalid))
        records.append(dict(domains=dict(domains),status='candidate_rejected' if invalid else 'legal',
            rejected_stages=invalid,station_truth=copy.deepcopy(truth),solver_backend_layers=(copy.deepcopy(result.get('solver_backend_layers',[])) if invalid else successful_backend_evidence(result.get('solver_backend_layers',[])))))
        if not invalid:
            result['joint_diagnostic']['m23_boiler_domains']=dict(status='legal_domain_selected',checked_domains=records,
                positive_command_domain_searched=True,full_priority_hierarchy_reoptimized=True,
                prior_candidate_locks_reused=False,inventory_committed=False,global_physical_infeasibility_claimed=False)
            return result
        # A zero-output run is a rejected boundary of the strict positive
        # domain, not an excuse to count .5 kW standby as real consumption.
        # All positive solutions in the unconstrained candidate were open;
        # close only the rejected coordinates and rerun the full hierarchy.
        for name in invalid:domains[name]='off'
        last=result
    error=SolverExecutionError('M23 boiler command candidate closure exceeded its registered finite bound')
    error.category='command_compilation_implementation_gap'
    error.command_compilation_diagnostic=dict(checked_domains=records,global_physical_infeasibility_claimed=False)
    error.solver_backend_attempts=last.get('solver_backend_layers',[]) if last else []
    raise error
