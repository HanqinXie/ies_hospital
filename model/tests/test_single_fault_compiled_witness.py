"""Current SF2 capture/hook protocol; synthetic primal, no physical execution."""
from copy import deepcopy
from pathlib import Path

import pytest

from test_annual_program_witness import actual,legacy_case
from test_annual_compiled_flow_input import fixture as leaf_values
from test_sf2_source_profile import current_freeze,PROFILE,EXTRA_FILES
from tools.annual_program import witness
from tools.annual_program.compiled_flow_input import build_compiled_flow_input
from tools.annual_program.source_runtime import AnnualSourceMixin


ROOT=Path(__file__).resolve().parents[1]


def capture(actual):
    assert witness.native_source_binding(ROOT)['supported'] is True
    pins=current_freeze();origin=witness.compiled_origin(ROOT,pins)
    values=leaf_values();values['source_origin']=origin
    values['raw_x']=actual.result['solver_backend_layers'][-1]['attempts'][0]['raw_x']
    actual.result['_compiled_flow_input']=build_compiled_flow_input(**values)
    class Base:
        def _after_actual_segment(self,segment,result):return 'original hook completed'
    class Core(AnnualSourceMixin,Base):pass
    core=Core();core.__dict__.update(actual.core.__dict__)
    core.env.root=ROOT
    # A source freeze in a synthetic protocol fixture grants no data endpoint.
    core.env.annual_program_data_context={'runtime_freeze_receipt':{'source_sha256':pins},
        'annual_numeric_grant':False}
    actual.core=core
    return actual


def test_scientific_final_argument_is_owned_by_original_hook_and_sink(actual):
    c=capture(actual);before=deepcopy(c.result)
    assert c.core._after_actual_segment(c.segment,c.result)=='original hook completed'
    receipt=c.segment[witness.FIELD]
    metadata=c.core.solver_calls[receipt['solver_call_index']]['annual_actual_witness']
    binding=metadata['compiled_flow_binding'];payload=metadata['compiled_flow_input']
    assert c.result==before and payload==before['_compiled_flow_input']
    assert binding['source_profile']==PROFILE
    assert binding['status']=='recorded' and binding['exact_final_result_argument'] is True
    assert binding['source_files']==current_freeze()==payload['source_origin']['source_files']
    assert set(binding['source_files'])>=EXTRA_FILES
    assert binding['payload_content_sha256']==receipt['compiled_flow_input_sha256']==payload['content_sha256']
    logged=c.core.solver_calls[receipt['solver_call_index']]['solver_backend_layers'][0]['attempts'][0]
    assert logged['validation']['rows_ref']==receipt['rows_ref']
    assert logged['raw_x']==before['solver_backend_layers'][-1]['attempts'][0]['raw_x']
    assert payload['actual_execution_bound'] is payload['source_verification_performed'] is False
    assert payload['physics_validation_performed'] is False
    assert c.core.env.annual_program_data_context['annual_numeric_grant'] is False


def test_scientific_packet_cannot_be_recorded_outside_original_actual_hook(actual):
    c=capture(actual)
    with pytest.raises(ValueError,match='exact final actual-result hook'):
        witness.record_actual_witness(c.core,c.segment,c.result)
    assert witness.FIELD not in c.segment and c.core.solver_calls==[]


@pytest.mark.parametrize('relative',sorted(EXTRA_FILES))
def test_scientific_actual_freeze_must_bind_every_extra_before_sink_append(actual,relative):
    c=capture(actual)
    c.core.env.annual_program_data_context['runtime_freeze_receipt']['source_sha256'][relative]='0'*64
    with pytest.raises(ValueError):c.core._after_actual_segment(c.segment,c.result)
    assert witness.FIELD not in c.segment and c.core.solver_calls==[]


def test_equal_probe_packet_is_never_substituted_for_missing_final_argument(actual):
    c=capture(actual);packet=c.result.pop('_compiled_flow_input')
    c.result['m22_mode_selection']['candidates'][0]['_compiled_flow_input']=packet
    c.core._after_actual_segment(c.segment,c.result)
    metadata=c.core.solver_calls[c.segment[witness.FIELD]['solver_call_index']]['annual_actual_witness']
    assert metadata['compiled_flow_input'] is None
    assert metadata['compiled_flow_binding']['status']=='unknown'
