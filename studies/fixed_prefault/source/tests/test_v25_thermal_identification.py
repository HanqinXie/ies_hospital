import importlib
import importlib.util
import json
import pytest


def module():
    name='tools.model_correction_v25.thermal_identification'
    assert importlib.util.find_spec(name) is not None, 'V25 bounded fit gate is missing'
    return importlib.import_module(name)


@pytest.mark.parametrize('passed,maximum,runs',[(False,1e-15,6),(True,1e-3,6),(True,1e-15,5)])
def test_fit_requires_completed_actual_mapping_gate(tmp_path,passed,maximum,runs):
    (tmp_path/'OUTPUT_MAPPING_GATE.json').write_text(json.dumps(dict(mapping_gate_passed=passed,mapping_max_abs_C=maximum,actual_EnergyPlus_runs=runs)))
    with pytest.raises(ValueError):
        module().require_mapping_gate(tmp_path)


def test_valid_mapping_gate_allows_bounded_fit_stage(tmp_path):
    (tmp_path/'OUTPUT_MAPPING_GATE.json').write_text(json.dumps(dict(mapping_gate_passed=True,mapping_max_abs_C=7e-15,actual_EnergyPlus_runs=6)))
    assert module().require_mapping_gate(tmp_path)['actual_EnergyPlus_runs']==6
