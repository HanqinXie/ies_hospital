"""New thermal native revision reaches the unchanged exact final actual hook.

Only the external authentication boundary is substituted for synthetic DTO/raw
vectors. No real admission, producer, solver or model trajectory is created.
"""
from copy import deepcopy
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_annual_program_witness import actual,legacy_case
from test_annual_compiled_flow_input import fixture
from test_minute_thermal_source_profile import descriptor
from tools.annual_program import witness
from tools.annual_program.compiled_flow_input import build_compiled_flow_input
from tools.annual_program.source_runtime import AnnualSourceMixin
from tools.minute1_20261103 import profile

ROOT=Path(__file__).resolve().parents[1]


def thermal_capture(actual,monkeypatch,nvars):
    value=descriptor();files=value['source_files'];value['recorder_sha256']=files['tools/annual_program/witness.py']=hashlib.sha256(Path(witness.__file__).read_bytes()).hexdigest()
    files['tools/annual_program/source_runtime.py']=hashlib.sha256((ROOT/'tools/annual_program/source_runtime.py').read_bytes()).hexdigest()
    reference={'path':str((actual.old.producer/'synthetic_no_real_admission.json').resolve()),'sha256':'d'*64};calls=[]
    def authentication(root,ref,*,source_hashes=None):
        assert Path(root).resolve()==ROOT and ref==reference
        assert source_hashes is None or source_hashes==files
        calls.append('independent_authentication_boundary');return deepcopy(value)
    monkeypatch.setattr(profile,'verify_source_admission',authentication)
    origin=dict(schema=witness.MINUTE_THERMAL_SOURCE_SCHEMA,source_root=str(ROOT),source_files=deepcopy(files),
        leaf_function='tools.operating_response.physical_balance.solve_physical',source_profile=witness.contracts.MINUTE_THERMAL_CAPTURE_PROFILE,
        source_admission=deepcopy(reference),native_revision_contract=deepcopy(value['native_revision_contract']))
    def compiled(root,sources,*,source_admission=None):
        assert Path(root).resolve()==ROOT and sources==files and source_admission==reference
        calls.append('compiled_origin_consumed');return deepcopy(origin)
    monkeypatch.setattr(witness,'compiled_origin',compiled)
    raw=actual.result['solver_backend_layers'][-1]['attempts'][0]['raw_x']
    if nvars==48:raw.append(.123)
    values=fixture(nvars);values.update(raw_x=raw,source_origin=deepcopy(origin));actual.result['_compiled_flow_input']=build_compiled_flow_input(**values)
    class Base:
        def _after_actual_segment(self,segment,result):return 'unchanged physical commits'
    class Core(AnnualSourceMixin,Base):pass
    core=Core();core.__dict__.update(actual.core.__dict__);core.env=SimpleNamespace(root=ROOT,minute1_source_admission=deepcopy(reference),minute1_source_freeze=deepcopy(files))
    actual.core=core
    return actual,value,reference,calls


@pytest.mark.parametrize('nvars',[47,48])
def test_thermal_revision_is_bound_in_native_DTO_and_actual_recorded_metadata(actual,monkeypatch,nvars):
    current,value,reference,calls=thermal_capture(actual,monkeypatch,nvars)
    assert current.core._after_actual_segment(current.segment,current.result)=='unchanged physical commits'
    receipt=current.segment[witness.FIELD];metadata=current.core.solver_calls[receipt['solver_call_index']]['annual_actual_witness']
    for obj in (metadata['original_native_source_binding'],metadata['compiled_flow_binding'],metadata['compiled_flow_input']['source_origin']):
        assert obj['source_profile']==witness.contracts.MINUTE_THERMAL_CAPTURE_PROFILE
        assert obj['source_admission']==reference and obj['native_revision_contract']==value['native_revision_contract']
    assert receipt['status']=='recorded' and receipt['raw_x_length']==nvars
    assert 'compiled_origin_consumed' in calls and len(calls)>=4


@pytest.mark.parametrize('change',['revision_pin','admission_pin','foreign_hook','missing_packet','old_minute_schema'])
def test_mixed_thermal_revision_fails_before_any_success_sink(actual,monkeypatch,change):
    current,value,reference,calls=thermal_capture(actual,monkeypatch,47)
    if change in ('revision_pin','admission_pin','old_minute_schema'):
        origin=deepcopy(current.result['_compiled_flow_input']['source_origin'])
        if change=='revision_pin':origin['native_revision_contract']['source_files']['tools/essential_fixes/execution.py']='0'*64
        elif change=='admission_pin':origin['source_admission']['sha256']='0'*64
        else:
            origin['schema']=witness.MINUTE1_SOURCE_SCHEMA;origin['source_profile']=witness.contracts.MINUTE1_CAPTURE_PROFILE;origin.pop('native_revision_contract')
        values=fixture();values.update(raw_x=current.result['solver_backend_layers'][-1]['attempts'][0]['raw_x'],source_origin=origin)
        current.result['_compiled_flow_input']=build_compiled_flow_input(**values)
    elif change=='missing_packet':current.result.pop('_compiled_flow_input');current.result['_sf2_fuel_receipt']={'role':'actual_interval'}
    with pytest.raises((ValueError,AssertionError)):
        if change=='foreign_hook':witness.record_actual_witness(current.core,current.segment,current.result)
        elif change=='missing_packet':
            from tools.annual_program.compiled_capture import capture_scope
            with capture_scope({'protocol_test_only':True}):current.core._after_actual_segment(current.segment,current.result)
        else:current.core._after_actual_segment(current.segment,current.result)
    assert witness.FIELD not in current.segment and current.core.solver_calls==[]
