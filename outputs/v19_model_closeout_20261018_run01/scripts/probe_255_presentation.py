"""Finite positive presentations of the saved original P3 objective/domain."""
import sys,json
from pathlib import Path
import numpy as np
from scipy import sparse
from scipy.optimize import Bounds,LinearConstraint
ROOT=Path(__file__).resolve().parents[3];sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from hospital_ies.solver_diagnostics import solve_layer,validate_vector
from hospital_ies.solver_backend import milp
b=ROOT/'outputs/v19_model_closeout_20261018_run01/review/c19_255_failure/replay01/layers/layer_20'
m=json.loads(b.with_suffix('.json').read_text(encoding='utf8'))
with np.load(b.with_suffix('.npz')) as z:d={k:z[k].copy() for k in z.files}
A=sparse.csr_matrix((d['matrix_data'],d['matrix_indices'],d['matrix_indptr']),shape=d['matrix_shape'])
rows=[]
for label in ('priority_x1000','all_row_normalization'):
    scales=(np.array([1000. if r.get('kind')=='priority_lock' else 1. for r in m['rows']])
        if label=='priority_x1000' else 1./np.maximum(1.,np.asarray(abs(A).max(axis=1).toarray()).ravel()))
    def presented(c,*,integrality,bounds,constraints,options):
        return milp(c,integrality=integrality,bounds=bounds,
            constraints=[LinearConstraint(sparse.diags(scales)@A,d['lower']*scales,d['upper']*scales)],options=options)
    result=solve_layer(presented,d['objective'],integrality=d['integrality'],bounds=Bounds(d['bounds_lower'],d['bounds_upper']),
        constraints=[LinearConstraint(A,d['lower'],d['upper'])],options=dict(time_limit=6.,mip_rel_gap=1e-6,presolve=False),
        rank=20,variables=m['variables'],rows=m['rows'],locks=[],input_context={'saved':'255','presentation':label},row_scaling=False)
    validation=validate_vector(A,d['lower'],d['upper'],d['bounds_lower'],d['bounds_upper'],d['integrality'],result.x,m['rows'])
    record=dict(presentation=label,status=int(result.status),message=str(result.message),
        validation=validation,attempts=result.attempts)
    rows.append(record)
    print(json.dumps({k:v for k,v in record.items() if k not in ('attempts','validation')}),flush=True)
out=ROOT/'outputs/v19_model_closeout_20261018_run01/review/c19_255_failure/PRESENTATION_PROBES01.json'
with out.open('x',encoding='utf8') as f:json.dump(rows,f,indent=2)
