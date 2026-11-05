"""Current SF2 actual-meter capture failures, synthetic protocol only."""
import pytest
from test_annual_program_witness import actual,legacy_case
from test_single_fault_compiled_witness import capture
from tools.annual_program import witness


def test_actual_sf2_meter_cannot_commit_without_final_compiled_dto(actual):
    from tools.annual_program.compiled_capture import capture_scope
    c=capture(actual);origin=c.result.pop('_compiled_flow_input')['source_origin']
    c.result['_sf2_fuel_receipt']={'schema':'single_fault.sf2.running_fuel_receipt.v1','role':'actual_interval'}
    with capture_scope(origin),pytest.raises(ValueError,match='actual.*compiled|compiled.*actual'):
        c.core._after_actual_segment(c.segment,c.result)
    assert witness.FIELD not in c.segment and c.core.solver_calls==[]


@pytest.mark.parametrize('role',['point_probe','free_reference_probe'])
@pytest.mark.parametrize('tracking',[False,True])
def test_free_layout_has_explicit_nonactual_role_and_never_an_actual_dto(monkeypatch,role,tracking):
    import numpy as np
    from scipy.optimize import OptimizeResult
    from test_annual_compiler_capture import inputs
    from hospital_ies.solver_backend import active_original_problem
    from tools.single_fault_fuel_20261102.fuel import FuelCurve
    from tools.operating_response import physical_balance as leaf
    from tools.annual_program.compiled_capture import capture_scope
    from tools.annual_program.compiled_flow_input import SOURCE_SCHEMA
    from pathlib import Path
    root=Path(__file__).resolve().parents[1];values=inputs();values['execution_action']=None
    values['sf2_fuel_role']=role
    values['config']['_sf2_fuel_curve']=FuelCurve.from_source(root,values['plant']).as_dict()
    if tracking:values['tracking_targets']={'grid_import':20.}
    sizes=[]
    def injected(c,**kwargs):
        problem=active_original_problem();assert problem is not None
        sizes.append(len(c));x=np.zeros(len(c));x[11]=20.;x[45 if tracking else 44]=1.
        return OptimizeResult(status=0,x=x,fun=float(np.asarray(c)@x),message='explicit nonactual shape fixture',backend='injected')
    monkeypatch.setattr(leaf,'milp',injected)
    origin=dict(schema=SOURCE_SCHEMA,source_root='synthetic_shape_not_qualified',source_files={'fixture.py':'a'*64},leaf_function='fixture.solve')
    with capture_scope(origin):result=leaf.solve_physical(**values)
    assert sizes and set(sizes)=={51 if tracking else 50}
    assert result['sf2_nonactual_layout']['role']==role
    assert result['sf2_nonactual_layout']['actual_DTO_emitted'] is False
    assert result['sf2_nonactual_layout']['native_vector_dimension']==sizes[-1]
    assert '_compiled_flow_input' not in result
    assert result['_sf2_fuel_receipt']['role']==role
    assert result['_sf2_fuel_receipt']['accounting_committed'] is False


def test_no_capture_request_can_only_copy_basic_rows_with_unknown_compiled_binding(actual):
    c=capture(actual);c.result.pop('_compiled_flow_input')
    c.result['_sf2_fuel_receipt']={'schema':'single_fault.sf2.running_fuel_receipt.v1','role':'actual_interval'}
    c.core._after_actual_segment(c.segment,c.result)
    receipt=c.segment[witness.FIELD]
    metadata=c.core.solver_calls[receipt['solver_call_index']]['annual_actual_witness']
    assert metadata['compiled_flow_input'] is None
    assert metadata['compiled_flow_binding']['status']=='unknown'
    assert metadata['compiled_flow_binding']['reasons']==['missing_final_compiled_flow_input']
    assert receipt['compiled_flow_input_sha256'] is None
