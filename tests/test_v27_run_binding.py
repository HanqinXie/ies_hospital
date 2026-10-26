import pytest
from tools.model_review_v27.objective_runs import validate_resume_binding

def test_actual_solver_infinite_bounds_remain_explicit_and_do_not_break_evidence_export(tmp_path):
    import json,numpy as np
    from tools.model_review_v27.objective_runs import write
    path=tmp_path/'native_diagnostic.json'
    write(path,dict(bounds=[-float('inf'),float('inf')],unknown=np.array([float('nan')]),actual_energy_cost=None))
    result=json.loads(path.read_text())
    assert result['bounds']==[{'__nonfinite_float__':'-inf'},{'__nonfinite_float__':'+inf'}]
    assert result['unknown']==[{'__nonfinite_float__':'nan'}]
    assert result['actual_energy_cost'] is None

def test_a_resume_cannot_use_missing_or_changed_physical_bindings_even_when_both_are_empty():
    with pytest.raises(ValueError):validate_resume_binding({}, {})
    expected={k:'same' for k in ['source_sha256','input_sha256','scenario_sha256','model_config_sha256']}
    validate_resume_binding(dict(expected,case_sha256='new_policy'),dict(expected,case_sha256='old_policy'))
    for key in expected:
        changed=dict(expected);changed[key]='different'
        with pytest.raises(ValueError,match=key):validate_resume_binding(changed,expected)
