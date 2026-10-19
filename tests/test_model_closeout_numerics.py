"""A strict feasible prior vector disproves the native false infeasibility."""
import importlib
import json
from pathlib import Path
import sys
import numpy as np
from scipy import sparse
from scipy.optimize import Bounds,LinearConstraint
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]

def test_saved_255_layer_optimizes_same_domain_with_feasible_witness():
    from hospital_ies.solver_backend import milp
    from hospital_ies.solver_diagnostics import validate_vector
    try:module=importlib.import_module('tools.model_closeout.numerics')
    except ModuleNotFoundError:module=importlib.import_module('tools.execution_recovery.numerics')
    base=ROOT/'outputs/v19_model_closeout_20261018_run01/review/c19_255_failure/replay01/layers/layer_20'
    metadata=json.loads(base.with_suffix('.json').read_text(encoding='utf8'))
    with np.load(base.with_suffix('.npz')) as z:d={k:z[k].copy() for k in z.files}
    matrix=sparse.csr_matrix((d['matrix_data'],d['matrix_indices'],d['matrix_indptr']),shape=d['matrix_shape'])
    before=validate_vector(matrix,d['lower'],d['upper'],d['bounds_lower'],d['bounds_upper'],
        d['integrality'],d['previous_x'],metadata['rows'])
    assert before['max_original_violation']<=1e-7
    assert before['max_lock_violation']<=1e-10
    result=module.solve_layer_with_lock_presentation(milp,d['objective'],integrality=d['integrality'],
        bounds=Bounds(d['bounds_lower'],d['bounds_upper']),constraints=[LinearConstraint(matrix,d['lower'],d['upper'])],
        options=dict(time_limit=6.,mip_rel_gap=1e-6,presolve=False),rank=20,variables=metadata['variables'],
        rows=metadata['rows'],locks=[],input_context={'saved_case':'C19_255_arrived_current_P3'},
        row_scaling=False,previous_candidate=d['previous_x'])
    assert result.status==0
    validation=validate_vector(matrix,d['lower'],d['upper'],d['bounds_lower'],d['bounds_upper'],
        d['integrality'],result.x,metadata['rows'])
    assert validation['max_original_violation']<=1e-7
    assert validation['max_lock_violation']<=1e-10
    assert validation['max_integrality_distance']<=1e-9
    assert result.same_domain_presentation_recovery['recovery_accepted']
    assert result.same_domain_presentation_recovery['original_objective_bounds_and_locks_unchanged']
