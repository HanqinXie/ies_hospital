"""Focused rejection of malformed current domains before any cached acceptance."""
import numpy as np
import pytest
from scipy.sparse import csr_matrix
from hospital_ies.engineering_numerics import engineering_numerics_scope,_family_key
from hospital_ies.solver_diagnostics import validate_vector

def test_nan_continuous_bound_cannot_be_hidden_by_zero_objective_bound():
    p=dict(c=np.array([1.,0.]),matrix=csr_matrix([[1.,1.]]),lower=np.array([1.]),upper=np.array([1.]),
        bounds_lower=np.array([0.,0.]),bounds_upper=np.array([2.,2.]),integrality=np.array([0,0]),
        rows=[dict(name='physical',kind='physical')],nonlinear=None)
    with engineering_numerics_scope() as cache:
        cache.candidates[_family_key(p)]=[np.array([0.,1.])]
        p['bounds_upper'][1]=np.nan
        with pytest.raises(ValueError,match='current engineering domain'):
            cache.candidate(p,p,validate_vector)
        assert cache.stats()['bound_skips']==0
