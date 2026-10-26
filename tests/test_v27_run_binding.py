import pytest
from tools.model_review_v27.objective_runs import validate_resume_binding

def test_a_resume_cannot_use_missing_or_changed_physical_bindings_even_when_both_are_empty():
    with pytest.raises(ValueError):validate_resume_binding({}, {})
    expected={k:'same' for k in ['source_sha256','input_sha256','scenario_sha256','model_config_sha256']}
    validate_resume_binding(dict(expected,case_sha256='new_policy'),dict(expected,case_sha256='old_policy'))
    for key in expected:
        changed=dict(expected);changed[key]='different'
        with pytest.raises(ValueError,match=key):validate_resume_binding(changed,expected)
