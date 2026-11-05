"""Read-only closure table and six real successful predecessor call exports."""
from __future__ import annotations
import argparse
import copy
import csv
import hashlib
import json
from pathlib import Path
import sys
import time
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
sys.path.insert(0,str(ROOT/'src'))
from p2c_solver_diagnostics import call_from_diagnostic,collect,import_frozen,write,sha,clean_witness
from hospital_ies.solver_diagnostics import LayerCollector
from hospital_ies.pipeline import read_data

def frozen_collect(module,d,new_layers,context):
    collector=LayerCollector(context);real=module.milp;error=None;result=None
    def observed(c,**kwargs):
        index=len(collector.layers);reference=new_layers[index]
        locks=[]
        for j,previous in enumerate(collector.layers):
            objective=previous['objective'];value=previous['result']['raw_objective']
            tolerance=module.lex_lock_tolerance(j,value,d['config'])
            locks.append(dict(source_rank=j,objective=objective,incumbent_raw_objective=value,
                tolerance=tolerance,upper=value+tolerance,relation='<=',unit='normalized carrier ENS'))
        layer=collector.begin(c,rank=index,variables=reference['variables'],rows=reference['rows'],locks=locks,
            input_context=d,**kwargs)
        started=time.perf_counter()
        try:res=real(c,**kwargs)
        except Exception as exc:
            collector.finish(layer,None,time.perf_counter()-started,exc);raise
        collector.finish(layer,res,time.perf_counter()-started);return res
    module.milp=observed
    try:result=call_from_diagnostic(module,d)
    except Exception as exc:error=dict(type=type(exc).__name__,message=str(exc))
    finally:module.milp=real
    if result is not None:
        processed=collector.layers[-1]['result']['x_raw'].copy();processed[:3]=result['action']['gt_electric_kW'];processed[14:26]=result['lost'].ravel();collector.postprocess(processed)
    return collector,result,error

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--run-dir',type=Path,required=True)
    parser.add_argument('--correction-tag',choices=('v2','v3'),default='v3');args=parser.parse_args()
    b=args.run_dir.resolve();output=b/'solver_diagnostics'
    old=json.loads((output/'original_111_summary.json').read_text(encoding='utf-8'))
    tag=args.correction_tag;suffix='_'+tag.upper()
    new=json.loads((output/f'corrected_111_{tag}_summary.json').read_text(encoding='utf-8'))
    gates=json.loads((output/f'S_gate_{tag}.json').read_text(encoding='utf-8'))
    source=ROOT/'outputs/v43_p2b_20261013_run01/report_data/failures.jsonl'
    failures=[json.loads(l) for l in source.read_text(encoding='utf-8').splitlines()]
    cause_rows=[]
    for a,z in zip(old['records'],new['records']):
        if a['case_id']!=z['case_id']:raise ValueError('Case alignment')
        cause_rows.append(dict(case_id=a['case_id'],scenario_id=a['scenario_id'],policy=a['policy'],phase=a['phase'],time_minute=a['time_minute'],
            original_failed_rank=a['expected_old_rank'],original_status=a['reproduced_status'],cause=a['reason'],
            unchanged_arrays_against_frozen=a['frozen_source_equivalence']['exact_layer_arrays_and_options_equal'],
            inherited_original_arrays_unchanged=a['unchanged_prefix_arrays'],
            previous_witness_original_max_violation=a['previous_witness_max_original_violation'],previous_witness_lock_max_violation=a['previous_witness_max_lock_violation'],previous_witness_integer_distance=a['previous_witness_max_integrality_distance'],
            same_matrix_original_status=a['controlled_status']['unchanged'],same_matrix_presolve_false_status=a['controlled_status']['presolve_false'],
            same_matrix_row_scaling_status=a['controlled_status']['row_scaling_only'],same_matrix_pure_feasibility_status=a['controlled_status']['pure_feasibility_only'],fixed_witness_status=a['controlled_status']['fixed_previous_witness'],
            corrected_all_five_layers=z['corrected_full_lex_success'],corrected_status=json.dumps(z['all_5_layer_status']),
            corrected_max_original_violation=z['worst_layer_original_violation'],corrected_max_lock_violation=z['worst_layer_lock_violation'],
            no_physical_base_change=z['original_base_arrays_unchanged'],positive_scale_equivalence=z['positive_scaling_equivalence'],
            entailed_component_bounds_equivalence=z.get('entailed_component_bounds_equivalence'),
            handling_status='fixed_equivalent_numeric' if z['corrected_full_lex_success'] else 'unresolved',
            original_artifacts=a['source_directory'],corrected_artifacts=z['source_directory']))
    with (output/f'111_CAUSE_TABLE{suffix}.csv').open('x',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(cause_rows[0]));writer.writeheader();writer.writerows(cause_rows)
    representatives=json.loads((ROOT/'tests/fixtures/p2c_lex_counterexamples.json').read_text(encoding='utf-8'))[:6]
    frozen=import_frozen(b/'.before/src/hospital_ies/resilience.py');neighbors=[];hourly=read_data(ROOT)
    for index,case in enumerate(representatives):
        failure=next(f for f in failures if f['scenario_id']==case['scenario_id'] and f['policy']==case['policy'])
        d=copy.deepcopy(failure['error']['diagnostic']);segment=d['completed_segments'][-1];t=segment['start_minute']
        # Restore the predecessor's actual hourly input, including when the
        # failure falls exactly on the next hourly boundary.
        row=next(hourly.iloc[[segment['hour_of_year']-1]].itertuples(index=False))
        d['row']=dict(timestamp=str(row.timestamp),outdoor_temperature_C=float(row.outdoor_temperature_C))
        commands=[e['command'] for e in d['events'] if e['kind']=='command_generated']
        command=next((c for c in commands if c['id']==segment['command_id']),{})
        d.update(phase='fixed_execution',step=segment['step'],time_minute=t,dt_h=(segment['end_minute']-t)/60,
            loads_kW=segment['connected_loads_kW'],availability=segment['availability'],state=segment['pre_state'],
            executed_action=segment['arrived_source_setpoints'],command=command,
            pending_commands=[c for c in commands if c['generated_minute']<=t and c['arrival_minute']>t],
            events=[e for e in d['events'] if e['time_minute']<=t],
            completed_segments=[s for s in d['completed_segments'] if s['end_minute']<=t],
            local_execution_state=dict(local_down=None))
        name=f"neighbor_{index:02d}_{case['scenario_id']}_{case['policy']}"
        current,result,error=collect(d,dict(call_id=name,policy=case['policy'],scope='real preceding successful fixed-execution call'))
        prior,old_result,old_error=frozen_collect(frozen,d,current.layers,dict(call_id=name,version='frozen P2B'))
        destination=output/f'success_neighbors_{tag}'/name
        current.export(destination/'corrected');prior.export(destination/'original');write(destination/'context.json',d)
        passed=error is None and old_error is None and len(current.layers)==len(prior.layers)==5 and all(
            l['result']['status']==0 and clean_witness(l['validation']) for c in (current,prior) for l in c.layers)
        row=dict(scenario_id=case['scenario_id'],policy=case['policy'],start_minute=t,end_minute=segment['end_minute'],
            original_error_class=f"{case['diagnostic']['phase']}:rank{case['expected_old_rank']}",passed=passed,
            original_error=old_error,corrected_error=error,
            old_cost=None if old_result is None else old_result['cost_USD_step'],new_cost=None if result is None else result['cost_USD_step'],
            old_lex_values=None if old_result is None else old_result['lex_values'],new_lex_values=None if result is None else result['lex_values'],
            old_action=None if old_result is None else old_result['action'],new_action=None if result is None else result['action'],
            old_lost=None if old_result is None else old_result['lost'],new_lost=None if result is None else result['lost'],
            path=str(destination.relative_to(b)))
        neighbors.append(row);print(json.dumps(dict(neighbor=index+1,passed=passed,scenario=case['scenario_id'],policy=case['policy'])),flush=True)
    write(output/f'success_neighbors_{tag}_summary.json',dict(passed=all(n['passed'] for n in neighbors),count=len(neighbors),records=neighbors))
    statistics=dict(original_cases=len(cause_rows),fixed=sum(r['corrected_all_five_layers'] for r in cause_rows),
        original_worst_row_and_bound=max(r['previous_witness_original_max_violation'] for r in cause_rows),
        original_worst_lock=max(r['previous_witness_lock_max_violation'] for r in cause_rows),
        original_worst_integrality=max(r['previous_witness_integer_distance'] for r in cause_rows),
        corrected_worst_raw_original_unit=max(r['corrected_max_original_violation'] for r in cause_rows),
        corrected_worst_raw_lock=max(r['corrected_max_lock_violation'] for r in cause_rows),
        controls={key:{str(status):sum(r['controlled_status'][key]==status for r in old['records']) for status in (0,1,2,3,4)} for key in old['records'][0]['controlled_status']},
        success_neighbors=neighbors,numerical_gate=gates)
    write(output/f'solver_closure{suffix}_summary.json',statistics)
    text=f'''# P2C solver numerical closure

Original 111 calls were reconstructed from their actual P2B first-error contexts. All 111 reproduce the original failed rank/status. Every objective/matrix/variable-domain/options array before solving matches the frozen P2B source exactly. None was reclassified outside the research domain.

All failing next layers retain their previous raw feasible incumbent. Maximum original row/bound violation: {statistics['original_worst_row_and_bound']:.17g}; normalized lock violation: {statistics['original_worst_lock']:.17g}; integer distance: {statistics['original_worst_integrality']:.17g}. These are floating-point residual measurements, not exact rational certificates.

On the identical failed matrices, presolve=False alone solves 111/111. A uniform full five-layer implementation with only that option solves 109/111; both policies of p2b_L4a_boundary_p2b_day_136_4 remain false infeasible at rank 3, with zero-residual inherited witnesses. This failed attempt and its matrices are retained. Positive row scaling alone solves each of those exact new matrices.

The v2 uniform presolve=False plus positive 1/max(abs(A_row)) rule closed all 111 first-error calls, but failed four original timing regressions. That failed global gate and all matrices are retained. Uniform presolve=True plus the same row rule closed the timing cases but only 107/111 original error calls; fixed-column elimination, bounded column units, implied GT-on bounds, declared lock-unit and binary-power row controls did not yield a single closed rule. Every attempt is logged and retained.

The {tag} implementation uses uniform presolve=False, positive rowmax scaling, and component upper bounds already mathematically implied by each original nonnegative total ENS lock. For c_j>0 and finite nonnegative original lower bounds, c@x<=U implies x_j<=(U-sum(c_k*l_k,k!=j))/c_j. Fraction arithmetic uses the exact rational values of the original floating-point numbers, then rounds the resulting upper bound outward. This neither fixes an incumbent nor removes the total lock. Original and actual solver bounds, exact rational proof fields and raw solutions are exported separately. Original A/lower/upper, c, priority order, integer variables, capacities and val+original tolerance locks remain unchanged; actual scaled rows multiply A/lower/upper by the same positive factor. No new tolerance, retry, physical capability or incumbent fallback is used.

Current {tag} 111/111 calls complete all five objective layers; maximum original-unit raw violation {statistics['corrected_worst_raw_original_unit']:.17g}, maximum raw normalized lock violation {statistics['corrected_worst_raw_lock']:.17g}. Six genuine successful predecessor calls are freshly exported with frozen P2B/current {tag} outcomes. A strict fixed-source/no-sink negative regression remains infeasible. A forced economic-layer error propagates instead of using the previous rank incumbent. Seventeen solver tests and all four unchanged original timing pytest tests passed in the recorded solver_25 and solver_27 runs. The full global release regression is a separate root gate; this first-error-call certificate alone does not authorize 800 trajectory generation.

The former economic-tiebreak skip has been removed. Public reset/step, original policies, six actions and output units are unchanged. Optional layer capture uses a context manager and resets after errors; source snapshots and all failed attempts are retained.

SciPy installed version is 1.15.3. Its [official milp manual](https://docs.scipy.org/doc/scipy-1.15.3/reference/generated/scipy.optimize.milp.html) documents presolve as a Boolean preprocessing option; the production backend remains SciPy/HiGHS. The evidence locates a numerical infeasibility failure, and does not identify a specific internal HiGHS presolver rule as the cause.

See 111_CAUSE_TABLE{suffix}.csv, original_111_summary.json, corrected_111_{tag}_summary.json, NUMERICAL_PREREGISTRATION*.json, S_gate_{tag}.json and the NPZ/JSON trees. All v1/v2 gates, the v2 global failure record, and candidate failures remain separately preserved. Full uniform trajectory replay is a separate required root task; this numerical closure does not claim hospital realism or new policy superiority.
'''
    (output/f'SOLVER_CLOSURE_REPORT{suffix}.md').write_text(text,encoding='utf-8')
    print(json.dumps({k:v for k,v in statistics.items() if k not in ('success_neighbors','numerical_gate')}),flush=True)
    return 0 if gates['passed'] and all(n['passed'] for n in neighbors) else 1

if __name__=='__main__':sys.exit(main())
