"""M23-only equivalent presentation of the optional recovery-service lock.

The unchanged native feasibility tolerance is larger than the unchanged
original-unit lock acceptance tolerance. A binary-exact positive row scale
exposes that finer precision to the native optimizer. Original-domain
validation, objective, bounds and raw 1e-7 recovery-lock width are retained.
"""
from __future__ import annotations
import math
import numpy as np
from scipy import sparse
from scipy.optimize import LinearConstraint
from hospital_ies.solver_backend import descriptor
from hospital_ies.solver_diagnostics import solve_layer as original_solve_layer,positive_row_scale,flatten_constraints
from tools.engineering_model.environment import isolated

RECOVERY_LOCK='m22.actual_recovery_after_all_service'

def solve_layer(solver,c,**arguments):
    indices=[i for i,row in enumerate(arguments['rows']) if row.get('name')==RECOVERY_LOCK]
    if not indices:
        return original_solve_layer(solver,c,**arguments)
    if not arguments.get('row_scaling'):
        raise ValueError('M23 recovery lock presentation requires recorded positive row scaling')
    contract=descriptor()
    ratio=contract['parameters']['FeasibilityTol']/contract['raw_validation']['normalized_locks']
    factor=2.**max(0,math.ceil(math.log2(ratio)))
    def scale(constraints):
        scaled,scales=positive_row_scale(constraints)
        matrix,lower,upper=flatten_constraints(scaled)
        extra=np.ones(matrix.shape[0]);extra[indices]=factor
        return [LinearConstraint(sparse.diags(extra)@matrix,lower*extra,upper*extra)],scales*extra
    execute=isolated(original_solve_layer,dict(original_solve_layer.__globals__,positive_row_scale=scale))
    return execute(solver,c,**arguments)

solve_layer.original_solve_layer=original_solve_layer
