"""Opt-in, read-only snapshots of the exact lexicographic MILP layers.

The collector never changes solver arguments, and its context is restored even
on errors. Exported sparse arrays can be solved without the hospital runner.
Infinity in JSON is encoded as '+Infinity'/'-Infinity'; NaN is rejected.
"""
from __future__ import annotations
from contextlib import contextmanager
from contextvars import ContextVar
import copy
import hashlib
import json
from fractions import Fraction
from pathlib import Path
import time
import numpy as np
import scipy
from scipy import sparse
from scipy.optimize import Bounds, LinearConstraint, milp

_ACTIVE=ContextVar('hospital_ies_solver_layer_collector',default=None)

def active_collector():
    return _ACTIVE.get()

@contextmanager
def collect_layers(collector):
    token=_ACTIVE.set(collector)
    try:yield collector
    finally:_ACTIVE.reset(token)

def json_value(value):
    if isinstance(value,dict):return {str(k):json_value(v) for k,v in value.items()}
    if isinstance(value,(list,tuple,set)):return [json_value(v) for v in value]
    if isinstance(value,np.ndarray):return json_value(value.tolist())
    if isinstance(value,np.generic):return json_value(value.item())
    if isinstance(value,float):
        if np.isnan(value):raise ValueError('NaN is not a diagnostic value')
        if np.isposinf(value):return '+Infinity'
        if np.isneginf(value):return '-Infinity'
    return value

def flatten_constraints(constraints):
    arrays=[];lower=[];upper=[]
    for constraint in constraints:
        a=sparse.csr_matrix(np.atleast_2d(constraint.A) if not sparse.issparse(constraint.A) else constraint.A)
        arrays.append(a.copy())
        lower.append(np.broadcast_to(constraint.lb,(a.shape[0],)).copy())
        upper.append(np.broadcast_to(constraint.ub,(a.shape[0],)).copy())
    return sparse.vstack(arrays,format='csr'),np.concatenate(lower),np.concatenate(upper)

def positive_row_scale(constraints):
    matrix,lower,upper=flatten_constraints(constraints)
    maximum=np.asarray(abs(matrix).max(axis=1).toarray()).ravel()
    scales=np.divide(1.,maximum,out=np.ones_like(maximum),where=maximum>0.)
    return [LinearConstraint(sparse.diags(scales)@matrix,lower*scales,upper*scales)],scales

def entailed_ens_bounds(bounds,locks):
    """Expose component bounds already implied by nonnegative total ENS locks.

    With c >= 0 and finite l >= 0, c@x <= U entails
    x[j] <= (U - sum(c[k]*l[k], k != j))/c[j].  All arithmetic
    in this implication uses exact fractions of the original float values;
    the resulting upper bound is rounded outward, never inward.  The total
    lock remains present, and neither its tolerance nor its raw source value
    is modified.  No previous solution is used to fix a component.
    """
    lower=np.asarray(bounds.lb,dtype=float).copy()
    upper=np.asarray(bounds.ub,dtype=float).copy()
    proofs=[]
    for lock in locks:
        co=np.asarray(lock['objective'],dtype=float);indices=np.flatnonzero(co)
        if not len(indices):continue
        if np.any(co[indices]<=0.) or not np.all(np.isfinite(co[indices])) or np.any(lower[indices]<0.) or not np.all(np.isfinite(lower[indices])):
            raise ValueError('ENS component implication requires finite nonnegative coefficients and lower bounds')
        total_upper=Fraction.from_float(float(lock['upper']))
        for j in indices:
            minimum=sum((Fraction.from_float(float(co[k]))*Fraction.from_float(float(lower[k])) for k in indices if k!=j),Fraction())
            derived=(total_upper-minimum)/Fraction.from_float(float(co[j]))
            # An inconsistent lock is diagnosed by its unchanged original row.
            if derived<Fraction.from_float(float(lower[j])):continue
            if np.isfinite(upper[j]) and derived>=Fraction.from_float(float(upper[j])):continue
            try:outward=float(derived)
            except OverflowError:continue
            if Fraction.from_float(outward)<derived:outward=float(np.nextafter(outward,np.inf))
            if outward>=upper[j]:continue
            proofs.append(dict(source_rank=int(lock['source_rank']),index=int(j),original_upper=float(upper[j]),
                derived_upper=outward,coefficient=float(co[j]),other_lower_minimum=float(minimum),lock_upper=float(lock['upper']),
                exact_derived_numerator=str(derived.numerator),exact_derived_denominator=str(derived.denominator),
                relation='unchanged nonnegative total lock implies component upper; outward rounded'))
            upper[j]=outward
    return Bounds(lower,upper),proofs

def validate_vector(matrix,lower,upper,bounds_lower,bounds_upper,integrality,x,rows):
    if x is None:return None
    x=np.asarray(x,dtype=float)
    if not np.all(np.isfinite(x)):raise ValueError('Non-finite raw solution')
    activity=np.asarray(matrix@x).ravel()
    violation=np.maximum(0.,np.maximum(lower-activity,activity-upper))
    bound_violation=np.maximum(0.,np.maximum(bounds_lower-x,x-bounds_upper))
    integer_indices=np.flatnonzero(integrality)
    integer_distance=np.abs(x[integer_indices]-np.rint(x[integer_indices]))
    lock_mask=np.array([r.get('kind')=='priority_lock' for r in rows],dtype=bool)
    scale=np.maximum(1.,np.maximum(abs(activity),np.maximum(np.where(np.isfinite(lower),abs(lower),0.),np.where(np.isfinite(upper),abs(upper),0.))))
    physical_max=float(violation[~lock_mask].max(initial=0.))
    bound_max=float(bound_violation.max(initial=0.))
    lock_max=float(violation[lock_mask].max(initial=0.))
    detailed=[dict(index=i,**row,activity=float(activity[i]),lower=float(lower[i]),upper=float(upper[i]),
        lower_residual=float(lower[i]-activity[i]),upper_residual=float(activity[i]-upper[i]),
        violation=float(violation[i]),scaled_violation=float(violation[i]/scale[i])) for i,row in enumerate(rows)]
    order=np.argsort(-violation,kind='stable')[:min(10,len(rows))]
    return dict(max_original_violation=max(physical_max,bound_max),max_physical_row_violation=physical_max,
        max_bound_violation=bound_max,max_lock_violation=lock_max,
        max_integrality_distance=float(integer_distance.max(initial=0.)),
        max_scaled_row_violation=float((violation/scale).max(initial=0.)),
        bound_violation=bound_violation.tolist(),integer_indices=integer_indices.tolist(),
        integer_distance=integer_distance.tolist(),rows=detailed,
        worst_rows=[detailed[int(i)] for i in order])

class LayerCollector:
    def __init__(self,context=None):
        self.context=copy.deepcopy(context or {})
        self.layers=[]

    def begin(self,c,*,integrality,bounds,constraints,options,rank,variables,rows,locks,input_context,
              solver_constraints=None,row_scales=None,solver_bounds=None,entailed_bound_proofs=None):
        matrix,lower,upper=flatten_constraints(constraints)
        solver_matrix,solver_lower,solver_upper=flatten_constraints(solver_constraints or constraints)
        if not np.all(np.isfinite(matrix.data)) or not np.all(np.isfinite(c)):
            raise ValueError('Non-finite matrix/objective')
        if any(np.any(np.isnan(a)) for a in (lower,upper,bounds.lb,bounds.ub)):
            raise ValueError('NaN bound')
        if len(rows)!=matrix.shape[0]:raise ValueError('Row metadata mismatch')
        previous=self.layers[-1]['result']['x_raw'] if self.layers else None
        solver_bounds=bounds if solver_bounds is None else solver_bounds
        context=copy.deepcopy(input_context);context.update(copy.deepcopy(self.context))
        layer=dict(rank=int(rank),matrix=matrix,lower=lower.copy(),upper=upper.copy(),
            solver_matrix=solver_matrix,solver_lower=solver_lower,solver_upper=solver_upper,
            row_scales=np.ones(matrix.shape[0]) if row_scales is None else row_scales.copy(),
            bounds_lower=np.asarray(bounds.lb).copy(),bounds_upper=np.asarray(bounds.ub).copy(),
            solver_bounds_lower=np.asarray(solver_bounds.lb).copy(),solver_bounds_upper=np.asarray(solver_bounds.ub).copy(),
            entailed_bound_proofs=copy.deepcopy(entailed_bound_proofs or []),
            integrality=np.asarray(integrality).copy(),objective=np.asarray(c).copy(),
            objective_constant=0.,objective_scale=1.,variables=copy.deepcopy(variables),rows=copy.deepcopy(rows),
            locks=copy.deepcopy(locks),context=context,options=copy.deepcopy(options),
            previous_x=None if previous is None else np.asarray(previous).copy(),
            previous_validation=validate_vector(matrix,lower,upper,bounds.lb,bounds.ub,integrality,previous,rows),
            processed_x=None,solver=dict(backend='scipy.optimize.milp/HiGHS',scipy_version=scipy.__version__,
                presolve=bool(options.get('presolve',True))),result=None)
        self.layers.append(layer)
        return layer

    def finish(self,layer,result,elapsed,error=None):
        x=None if result is None or getattr(result,'x',None) is None else np.asarray(result.x).copy()
        layer['result']=dict(status=None if result is None else int(result.status),
            message=str(error) if error is not None else str(getattr(result,'message','')),
            error_type=None if error is None else type(error).__name__,elapsed_seconds=float(elapsed),
            x_raw=x,raw_objective=None if x is None else float(layer['objective']@x),
            res_fun=None if result is None or getattr(result,'fun',None) is None else float(result.fun),
            mip_dual_bound=None if result is None or getattr(result,'mip_dual_bound',None) is None else float(result.mip_dual_bound),
            mip_gap=None if result is None or getattr(result,'mip_gap',None) is None else float(result.mip_gap),
            mip_node_count=None if result is None or getattr(result,'mip_node_count',None) is None else int(result.mip_node_count),
            native_status=None if result is None else getattr(result,'native_status',None),
            attempts=[] if result is None else copy.deepcopy(getattr(result,'attempts',[])))
        if result is not None and getattr(result,'backend',None)=='gurobipy':
            from .solver_backend import descriptor
            layer['solver']=descriptor()
        layer['validation']=validate_vector(layer['matrix'],layer['lower'],layer['upper'],layer['bounds_lower'],
            layer['bounds_upper'],layer['integrality'],x,layer['rows'])
        layer['solver_validation']=validate_vector(layer['solver_matrix'],layer['solver_lower'],layer['solver_upper'],
            layer['solver_bounds_lower'],layer['solver_bounds_upper'],layer['integrality'],x,layer['rows'])

    def postprocess(self,x):
        if self.layers:self.layers[-1]['processed_x']=np.asarray(x).copy()

    def export(self,directory):
        directory=Path(directory)
        if directory.exists():raise FileExistsError(directory)
        directory.mkdir(parents=True)
        for layer in self.layers:
            base=directory/f"layer_{layer['rank']:02d}"
            np.savez_compressed(str(base)+'.npz',matrix_data=layer['matrix'].data,
                matrix_indices=layer['matrix'].indices,matrix_indptr=layer['matrix'].indptr,matrix_shape=layer['matrix'].shape,
                solver_matrix_data=layer['solver_matrix'].data,solver_matrix_indices=layer['solver_matrix'].indices,
                solver_matrix_indptr=layer['solver_matrix'].indptr,solver_matrix_shape=layer['solver_matrix'].shape,
                solver_lower=layer['solver_lower'],solver_upper=layer['solver_upper'],row_scales=layer['row_scales'],
                lower=layer['lower'],upper=layer['upper'],bounds_lower=layer['bounds_lower'],bounds_upper=layer['bounds_upper'],
                solver_bounds_lower=layer['solver_bounds_lower'],solver_bounds_upper=layer['solver_bounds_upper'],
                objective=layer['objective'],integrality=layer['integrality'],
                x_raw=np.empty(0) if layer['result']['x_raw'] is None else layer['result']['x_raw'],
                previous_x=np.empty(0) if layer['previous_x'] is None else layer['previous_x'],
                processed_x=np.empty(0) if layer['processed_x'] is None else layer['processed_x'])
            metadata={k:v for k,v in layer.items() if k not in ('matrix','solver_matrix')}
            metadata['matrix_shape']=list(layer['matrix'].shape)
            metadata['solver_matrix_shape']=list(layer['solver_matrix'].shape)
            metadata['npz_sha256']=hashlib.sha256(Path(str(base)+'.npz').read_bytes()).hexdigest()
            metadata['infinity_encoding']="JSON strings +Infinity/-Infinity; NPZ IEEE infinity, no NaN"
            Path(str(base)+'.json').write_text(json.dumps(json_value(metadata),ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
        return directory

def solve_layer(solver,c,*,integrality,bounds,constraints,options,rank,variables,rows,locks,input_context,row_scaling=False):
    from .solver_backend import original_problem
    solver_constraints,row_scales=positive_row_scale(constraints) if row_scaling else (constraints,None)
    solver_bounds,entailed_bound_proofs=entailed_ens_bounds(bounds,locks)
    matrix,lower,upper=flatten_constraints(constraints)
    collector=active_collector()
    if collector is None:
        with original_problem(matrix,lower,upper,bounds.lb,bounds.ub,integrality,rows):
            return solver(c,integrality=integrality,bounds=solver_bounds,constraints=solver_constraints,options=options)
    layer=collector.begin(c,integrality=integrality,bounds=bounds,constraints=constraints,options=options,
        rank=rank,variables=variables,rows=rows,locks=locks,input_context=input_context,
        solver_constraints=solver_constraints,row_scales=row_scales,solver_bounds=solver_bounds,entailed_bound_proofs=entailed_bound_proofs)
    started=time.perf_counter()
    try:
        with original_problem(matrix,lower,upper,bounds.lb,bounds.ub,integrality,rows):
            result=solver(c,integrality=integrality,bounds=solver_bounds,constraints=solver_constraints,options=options)
    except Exception as error:
        collector.finish(layer,None,time.perf_counter()-started,error)
        raise
    collector.finish(layer,result,time.perf_counter()-started)
    return result

def replay_layer(npz_path,json_path,*,presolve=None,pure_feasibility=False,row_scaling=None,fixed_witness=False):
    """Diagnostic variants act only on loaded copies; never a runtime fallback."""
    metadata=json.loads(Path(json_path).read_text(encoding='utf-8'))
    if hashlib.sha256(Path(npz_path).read_bytes()).hexdigest()!=metadata['npz_sha256']:
        raise ValueError('Snapshot NPZ hash mismatch')
    with np.load(npz_path,allow_pickle=False) as z:
        a=sparse.csr_matrix((z['matrix_data'],z['matrix_indices'],z['matrix_indptr']),shape=tuple(z['matrix_shape']))
        lower=z['lower'].copy();upper=z['upper'].copy();blo=z['bounds_lower'].copy();bhi=z['bounds_upper'].copy()
        c=z['objective'].copy();original_c=c.copy();integrality=z['integrality'].copy();previous=z['previous_x'].copy()
        saved_solver_matrix=sparse.csr_matrix((z['solver_matrix_data'],z['solver_matrix_indices'],z['solver_matrix_indptr']),shape=tuple(z['solver_matrix_shape'])) if 'solver_matrix_data' in z else a.copy()
        saved_solver_lower=z['solver_lower'].copy() if 'solver_lower' in z else lower.copy()
        saved_solver_upper=z['solver_upper'].copy() if 'solver_upper' in z else upper.copy()
        saved_solver_blo=z['solver_bounds_lower'].copy() if 'solver_bounds_lower' in z else blo.copy()
        saved_solver_bhi=z['solver_bounds_upper'].copy() if 'solver_bounds_upper' in z else bhi.copy()
    original=(a.copy(),lower.copy(),upper.copy(),blo.copy(),bhi.copy())
    options=dict(metadata['options'])
    if presolve is not None:options['presolve']=bool(presolve)
    if pure_feasibility:c[:]=0.
    if fixed_witness:
        if not previous.size:raise ValueError('No previous witness')
        blo=previous.copy();bhi=previous.copy()
    if row_scaling is None:
        a=saved_solver_matrix;lower=saved_solver_lower;upper=saved_solver_upper
        if not fixed_witness:blo=saved_solver_blo;bhi=saved_solver_bhi
    elif row_scaling:
        maximum=np.asarray(abs(a).max(axis=1).toarray()).ravel()
        scale=np.divide(1.,maximum,out=np.ones_like(maximum),where=maximum>0.)
        a=sparse.diags(scale)@a;lower=lower*scale;upper=upper*scale
    started=time.perf_counter()
    if metadata.get('solver',{}).get('backend')=='gurobipy':
        from .solver_backend import milp as native_milp,original_problem
        with original_problem(*original,integrality,metadata['rows']):
            res=native_milp(c,integrality=integrality,bounds=Bounds(blo,bhi),constraints=LinearConstraint(a,lower,upper),options=options)
    else:
        res=milp(c,integrality=integrality,bounds=Bounds(blo,bhi),constraints=LinearConstraint(a,lower,upper),options=options)
    elapsed=time.perf_counter()-started
    validation=validate_vector(*original,integrality,res.x,metadata['rows'])
    return dict(status=int(res.status),message=str(res.message),elapsed_seconds=elapsed,
        res_fun=None if getattr(res,'fun',None) is None else float(res.fun),
        original_objective=None if res.x is None else float(original_c@res.x),
        x_raw=None if res.x is None else res.x.tolist(),validation=validation,options=options,
        backend=getattr(res,'backend','scipy.optimize.milp/HiGHS'),attempts=getattr(res,'attempts',[]),
        variant=dict(presolve_override=presolve,pure_feasibility=pure_feasibility,row_scaling=row_scaling,fixed_witness=fixed_witness))
