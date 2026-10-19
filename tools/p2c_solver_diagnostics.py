"""Recover P2B first-error calls, exporting every raw layer and controls."""
from __future__ import annotations
import argparse
from datetime import datetime,timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys
from types import SimpleNamespace
import numpy as np
import pandas as pd
import scipy

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from hospital_ies import resilience
from hospital_ies.model import Plant
from hospital_ies.solver_diagnostics import LayerCollector,collect_layers,flatten_constraints,json_value,replay_layer,entailed_ens_bounds
from scipy.optimize import Bounds

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def write(path,value):
    Path(path).write_text(json.dumps(json_value(value),ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')

def call_from_diagnostic(module,d):
    values=dict(d['state'])
    for key in ('gt_prev','overload_minutes','disconnected_loads'):values[key]=np.asarray(values[key],dtype=float)
    values['extra_faulted']=set(values['extra_faulted'])
    state=module.RuntimeState(**values)
    row=SimpleNamespace(timestamp=pd.Timestamp(d['row']['timestamp']),outdoor_temperature_C=d['row']['outdoor_temperature_C'])
    planning=d['phase']=='command_generation';cmd=d['command'] or {};request=cmd if planning else cmd.get('request',{})
    weight=request.get('security',{}).get('gt_loading_penalty_USD_per_kWh',0.) if planning else cmd.get('weight',0.)
    if d.get('local_execution_state',{}).get('local_down') is not None:
        raise ValueError('Local interval averaging needs exact restoration, not an executed-action shortcut')
    return module._solve_milp(row,Plant(**d['plant']),np.asarray(d['loads_kW']),d['availability'],state,d['dt_h'],
        d['scenario']['grid_limit_kW'],d['state']['diesel_status']=='CONNECTED' and d['availability']['DIESEL']>0,
        d['config']['gt_ramp_kW_per_hour'],d['config']['solver_time_limit_sec'],d['config'],
        tracking_targets=request.get('targets',{}),security_gt_penalty_USD_per_kWh=weight,
        execution_action=None if planning else d['executed_action'],execution_contract=d['execution_contract'])

def import_frozen(path):
    name='hospital_ies.p2b_frozen_diagnostics'
    spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec)
    sys.modules[name]=module;spec.loader.exec_module(module)
    return module

def same_raw_layer(layer,c,kwargs):
    matrix,lower,upper=flatten_constraints(kwargs['constraints'])
    return all((np.array_equal(matrix.toarray(),layer['matrix'].toarray()),
        np.array_equal(lower,layer['lower']),np.array_equal(upper,layer['upper']),
        np.array_equal(kwargs['bounds'].lb,layer['bounds_lower']),np.array_equal(kwargs['bounds'].ub,layer['bounds_upper']),
        np.array_equal(kwargs['integrality'],layer['integrality']),np.array_equal(c,layer['objective']),kwargs['options']==layer['options']))

def verify_against_frozen(module,d,layers):
    real=module.milp;comparisons=[]
    def observed(c,**kwargs):
        index=len(comparisons)
        comparisons.append(index<len(layers) and same_raw_layer(layers[index],c,kwargs))
        return real(c,**kwargs)
    module.milp=observed
    try:
        call_from_diagnostic(module,d)
    except Exception:pass
    finally:module.milp=real
    return dict(exact_layer_arrays_and_options_equal=bool(comparisons and all(comparisons) and len(comparisons)==len(layers)),
        layer_count=len(comparisons),comparisons=comparisons)

def prefix_same(layer,previous):
    n=previous['matrix'].shape[0]
    return all((np.array_equal(layer['matrix'][:n].toarray(),previous['matrix'].toarray()),
        np.array_equal(layer['lower'][:n],previous['lower']),np.array_equal(layer['upper'][:n],previous['upper']),
        np.array_equal(layer['bounds_lower'],previous['bounds_lower']),np.array_equal(layer['bounds_upper'],previous['bounds_upper']),
        np.array_equal(layer['integrality'],previous['integrality'])))

def clean_witness(validation):
    return validation is not None and validation['max_original_violation']<=1e-7 and validation['max_lock_violation']<=1e-10 and validation['max_integrality_distance']<=1e-9

def collect(d,context):
    collector=LayerCollector(context);error=None;result=None
    try:
        with collect_layers(collector):result=call_from_diagnostic(resilience,d)
    except Exception as exc:error=dict(type=type(exc).__name__,message=str(exc))
    return collector,result,error

def original(args,failures,output):
    frozen=import_frozen(args.frozen_source)
    write(output/'NUMERICAL_PREREGISTRATION.json',dict(registered_utc=datetime.now(timezone.utc).isoformat(),
        numerical_version='p2c.uniform_no_presolve.v1 (proposed; not yet production)',
        unchanged_original_priority_lock_abs_tol=[1e-7,1e-7,1e-7,1e-5],unchanged_original_priority_lock_rel_tol=[0.,0.,0.,1e-8],
        unchanged_mip_rel_gap=1e-6,unchanged_solver_time_limit='per original recorded config, 6 seconds',
        unchanged_runtime_balance_threshold_original_units=1e-4,
        diagnostic_witness_primary_row_and_bound_threshold_original_units=1e-7,
        diagnostic_witness_normalized_lock_threshold=1e-10,diagnostic_integer_distance_threshold=1e-9,
        thresholds_are_validation_not_domain_relaxation=True,
        uniform_candidate='Set presolve=False for every layer and case on existing scipy/HiGHS backend; no retry or incumbent fallback',
        current_scipy_version=scipy.__version__,frozen_source_sha256=sha(args.frozen_source),
        source_sha256={str(p.relative_to(ROOT)):sha(p) for p in (ROOT/'src/hospital_ies/resilience.py',ROOT/'src/hospital_ies/solver_diagnostics.py')},
        controls=['unchanged matrix and options','presolve=False only','positive row-max scaling only','zero-objective only','previous witness fixed and presolve=False'],
        exact_arithmetic_claim=False,notes='Raw floating-point violations are retained. A validated 1e-13 witness is not an exact rational feasibility certificate.'))
    records=[]
    for index,failure in enumerate(failures):
        d=failure['error']['diagnostic'];expected=int(re.search(r'rank=(\d+)',failure['error']['message']).group(1))
        case_id=f"{index:03d}_{failure['scenario_id']}_{failure['policy']}";directory=output/'original_111'/case_id
        collector,result,error=collect(d,dict(call_id=case_id,policy=failure['policy'],phase=d['phase'],
            time_minute=d['time_minute'],root_family=d['scenario']['root_family_id'],original_random_stream=d['scenario']['exogenous_stream_id']))
        collector.export(directory);write(directory/'original_context.json',failure)
        layer=collector.layers[-1];rank=layer['rank'];base=directory/f'layer_{rank:02d}'
        controls={name:replay_layer(str(base)+'.npz',str(base)+'.json',**kwargs) for name,kwargs in (
            ('unchanged',{}),('presolve_false',dict(presolve=False)),('row_scaling_only',dict(row_scaling=True)),
            ('pure_feasibility_only',dict(pure_feasibility=True)),('fixed_previous_witness',dict(presolve=False,fixed_witness=True)))}
        write(directory/'controlled_solves.json',controls)
        exact=verify_against_frozen(frozen,d,collector.layers)
        write(directory/'frozen_source_equivalence.json',exact)
        inherited=all(prefix_same(collector.layers[i],collector.layers[i-1]) for i in range(1,len(collector.layers)))
        witness=layer['previous_validation']
        reason='presolve_false_infeasibility_with_verified_inherited_witness' if (
            error is not None and rank==expected and layer['result']['status']==2 and exact['exact_layer_arrays_and_options_equal']
            and inherited and clean_witness(witness) and controls['unchanged']['status']==2
            and controls['presolve_false']['status']==0 and clean_witness(controls['presolve_false']['validation'])) else 'unresolved'
        record=dict(case_id=case_id,scenario_id=failure['scenario_id'],policy=failure['policy'],phase=d['phase'],time_minute=d['time_minute'],
            expected_old_rank=expected,reproduced_rank=rank,reproduced_status=layer['result']['status'],error=error,
            reason=reason,unchanged_prefix_arrays=inherited,frozen_source_equivalence=exact,
            previous_witness_max_original_violation=None if witness is None else witness['max_original_violation'],
            previous_witness_max_lock_violation=None if witness is None else witness['max_lock_violation'],
            previous_witness_max_integrality_distance=None if witness is None else witness['max_integrality_distance'],
            controlled_status={k:v['status'] for k,v in controls.items()},source_directory=str(directory.relative_to(args.run_dir)),
            original_priority_values=[l['result']['raw_objective'] for l in collector.layers if l['rank']<4 and l['result']['status']==0])
        records.append(record)
        print(json.dumps(dict(case=index+1,total=len(failures),scenario=record['scenario_id'],policy=record['policy'],reason=reason,controls=record['controlled_status']),ensure_ascii=False),flush=True)
    summary=dict(attempted_original_failures=len(failures),reproduced_original_failures=sum(r['reproduced_status']==2 and r['reproduced_rank']==r['expected_old_rank'] for r in records),
        verified_presolve_false_infeasibility=sum(r['reason']!='unresolved' for r in records),unresolved_ids=[r['case_id'] for r in records if r['reason']=='unresolved'],
        frozen_source_sha256=sha(args.frozen_source),records=records)
    write(output/'original_111_summary.json',summary)
    return 0 if not summary['unresolved_ids'] else 1

def corrected(args,failures,output):
    original_summary=json.loads((output/'original_111_summary.json').read_text(encoding='utf-8'))
    records=[];unresolved=[]
    for index,failure in enumerate(failures):
        previous=original_summary['records'][index];d=failure['error']['diagnostic'];case_id=previous['case_id']
        collector,result,error=collect(d,dict(call_id=case_id,policy=failure['policy'],phase=d['phase'],time_minute=d['time_minute']))
        corrected_directory='corrected_111' if args.correction_tag=='v1' else f'corrected_111_{args.correction_tag}'
        directory=output/corrected_directory/case_id;collector.export(directory)
        all_success=len(collector.layers)==5 and all(l['result']['status']==0 and clean_witness(l['validation']) for l in collector.layers)
        inherited=all(prefix_same(collector.layers[i],collector.layers[i-1]) and clean_witness(collector.layers[i]['previous_validation']) for i in range(1,len(collector.layers)))
        uniform=all(l['options'].get('presolve') is False for l in collector.layers)
        scaling_valid=all(np.all(np.isfinite(l['row_scales'])) and np.all(l['row_scales']>0.) and
            np.array_equal(l['solver_matrix'].toarray(),(np.diag(l['row_scales'])@l['matrix'].toarray())) and
            np.array_equal(l['solver_lower'],l['lower']*l['row_scales']) and np.array_equal(l['solver_upper'],l['upper']*l['row_scales'])
            for l in collector.layers)
        bounds_valid=True
        for layer in collector.layers:
            implied,proofs=entailed_ens_bounds(Bounds(layer['bounds_lower'],layer['bounds_upper']),layer['locks'])
            bounds_valid=bounds_valid and np.array_equal(layer['solver_bounds_lower'],implied.lb) and np.array_equal(layer['solver_bounds_upper'],implied.ub) and layer['entailed_bound_proofs']==proofs
        old0=output/'original_111'/case_id/'layer_00.npz'
        with np.load(old0,allow_pickle=False) as z:
            same_base=all(np.array_equal(z[name],collector.layers[0][key]) for name,key in (
                ('lower','lower'),('upper','upper'),('bounds_lower','bounds_lower'),('bounds_upper','bounds_upper'),('objective','objective'),('integrality','integrality')))
            same_base=same_base and np.array_equal(z['matrix_data'],collector.layers[0]['matrix'].data) and np.array_equal(z['matrix_indices'],collector.layers[0]['matrix'].indices) and np.array_equal(z['matrix_indptr'],collector.layers[0]['matrix'].indptr)
        success=all_success and inherited and uniform and same_base and scaling_valid and bounds_valid and error is None
        if not success:unresolved.append(case_id)
        values=[l['result']['raw_objective'] for l in collector.layers if l['rank']<4 and l['result']['status']==0]
        record=dict(case_id=case_id,scenario_id=failure['scenario_id'],policy=failure['policy'],phase=d['phase'],time_minute=d['time_minute'],
            corrected_full_lex_success=success,all_5_layer_status=[l['result']['status'] for l in collector.layers],
            inherited_witnesses_valid=inherited,uniform_presolve_false=uniform,positive_scaling_equivalence=scaling_valid,original_base_arrays_unchanged=same_base,error=error,
            entailed_component_bounds_equivalence=bounds_valid,
            original_priority_values=previous['original_priority_values'],corrected_priority_values=values,
            priority_delta_common_prefix=[a-b for a,b in zip(values,previous['original_priority_values'])],
            action=None if result is None else result['action'],lex_values=None if result is None else result['lex_values'],
            cost_USD_step=None if result is None else result['cost_USD_step'],
            worst_layer_original_violation=max((l['validation']['max_original_violation'] for l in collector.layers if l.get('validation')),default=None),
            worst_layer_lock_violation=max((l['validation']['max_lock_violation'] for l in collector.layers if l.get('validation')),default=None),
            source_directory=str(directory.relative_to(args.run_dir)))
        records.append(record);print(json.dumps(dict(case=index+1,total=len(failures),scenario=record['scenario_id'],policy=record['policy'],complete5=success),ensure_ascii=False),flush=True)
    summary_name='corrected_111_summary.json' if args.correction_tag=='v1' else f'corrected_111_{args.correction_tag}_summary.json'
    write(output/summary_name,dict(attempted_original_failures=len(failures),corrected_full_lex_success=sum(r['corrected_full_lex_success'] for r in records),unresolved_ids=unresolved,passed=not unresolved,records=records))
    sources={str(p.relative_to(ROOT)):sha(p) for p in (ROOT/'src/hospital_ies/resilience.py',ROOT/'src/hospital_ies/solver_diagnostics.py')}
    gate=dict(passed=not unresolved and not original_summary['unresolved_ids'] and len(failures)==111,
        attempted_original_failures=len(failures),reproduced_original_failures=original_summary['reproduced_original_failures'],
        corrected_full_lex_success=sum(r['corrected_full_lex_success'] for r in records),unresolved_ids=unresolved+original_summary['unresolved_ids'],
        source_sha256=sources,numerical_version={'v1':'p2c.uniform_no_presolve.v1','v2':'p2c.uniform_no_presolve_rowmax.v2','v3':'p2c.uniform_no_presolve_rowmax_entailed_ens_bounds.v3'}[args.correction_tag],tolerance_unchanged=True,
        production_backend_unchanged='scipy.optimize.milp/HiGHS',scipy_version=scipy.__version__,
        baseline_service_feasible_domain_unchanged=all(r['original_base_arrays_unchanged'] for r in records),
        no_incumbent_fallback=True,no_objective_layer_skipped=True,
        original_summary_path=str((output/'original_111_summary.json').relative_to(ROOT)),corrected_summary_path=str((output/summary_name).relative_to(ROOT)),
        original_summary_sha256=sha(output/'original_111_summary.json'),corrected_summary_sha256=sha(output/summary_name))
    manifest_name=f'case_files_{args.correction_tag}_manifest.json'
    snapshots={str(p.relative_to(output)):dict(sha256=sha(p),bytes=p.stat().st_size) for base in ('original_111',corrected_directory) for p in sorted((output/base).rglob('*')) if p.is_file()}
    write(output/manifest_name,dict(files=snapshots));gate.update(case_files_manifest_path=str((output/manifest_name).relative_to(ROOT)),case_files_manifest_sha256=sha(output/manifest_name))
    write(output/('S_gate.json' if args.correction_tag=='v1' else f'S_gate_{args.correction_tag}.json'),gate)
    print(json.dumps(gate,ensure_ascii=False),flush=True)
    return 0 if gate['passed'] else 1

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--run-dir',type=Path,required=True)
    parser.add_argument('--phase',choices=('original','corrected'),required=True)
    parser.add_argument('--correction-tag',choices=('v1','v2','v3'),default='v1')
    parser.add_argument('--failure-file',type=Path,default=ROOT/'outputs/v43_p2b_20261013_run01/report_data/failures.jsonl')
    parser.add_argument('--frozen-source',type=Path,default=ROOT/'outputs/v43_p2c_20261013_run01/.before/src/hospital_ies/resilience.py')
    args=parser.parse_args();args.run_dir=args.run_dir.resolve();output=args.run_dir/'solver_diagnostics';output.mkdir(exist_ok=True)
    failures=[json.loads(line) for line in args.failure_file.read_text(encoding='utf-8').splitlines()]
    if len(failures)!=111:raise ValueError('Expected exact original 111 failures')
    return original(args,failures,output) if args.phase=='original' else corrected(args,failures,output)

if __name__=='__main__':sys.exit(main())
