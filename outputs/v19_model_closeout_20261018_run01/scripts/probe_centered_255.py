"""One affine presentation trial, preserving original integer coordinates."""
import sys,json,time
from pathlib import Path
import numpy as np
from scipy import sparse
ROOT=Path(__file__).resolve().parents[3];sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from hospital_ies.solver_backend import _gurobi_attempt,_COMMON
from hospital_ies.solver_diagnostics import validate_vector
b=ROOT/'outputs/v19_model_closeout_20261018_run01/review/c19_255_failure/replay01/layers/layer_20'
m=json.loads(b.with_suffix('.json').read_text(encoding='utf8'))
with np.load(b.with_suffix('.npz')) as z:d={k:z[k].copy() for k in z.files}
A=sparse.csr_matrix((d['matrix_data'],d['matrix_indices'],d['matrix_indptr']),shape=d['matrix_shape'])
shift=d['previous_x'].copy();shift[d['integrality']!=0]=np.rint(shift[d['integrality']!=0])
rows=[]
for factor in (1.,1000.):
    scales=np.array([factor if r.get('kind')=='priority_lock' else 1. for r in m['rows']])
    problem=dict(c=d['objective'],integrality=d['integrality'],matrix=sparse.diags(scales)@A,
        lower=(d['lower']-A@shift)*scales,upper=(d['upper']-A@shift)*scales,
        bounds_lower=d['bounds_lower']-shift,bounds_upper=d['bounds_upper']-shift)
    begin=time.perf_counter();parameters=dict(_COMMON,NumericFocus=3,Presolve=0,TimeLimit=6.)
    result=_gurobi_attempt(problem,parameters)
    candidate=None if result.x is None else result.x+shift
    validation=validate_vector(A,d['lower'],d['upper'],d['bounds_lower'],d['bounds_upper'],d['integrality'],candidate,m['rows'])
    record=dict(presentation='affine_current_feasible_witness',priority_row_factor=factor,status=int(result.status),
        native_status=result.native_status,parameters=parameters,elapsed_seconds=time.perf_counter()-begin,
        original_validation=validation,objective=None if candidate is None else float(d['objective']@candidate),
        mapped_dual_bound=None if result.mip_dual_bound is None else float(result.mip_dual_bound+d['objective']@shift),
        shift=shift.tolist(),candidate=None if candidate is None else candidate.tolist())
    rows.append(record);print(json.dumps({k:v for k,v in record.items() if k not in ('shift','candidate','original_validation')}),flush=True)
out=ROOT/'outputs/v19_model_closeout_20261018_run01/review/c19_255_failure/AFFINE_PROBES01.json'
with out.open('x',encoding='utf8') as f:json.dump(rows,f,indent=2)
