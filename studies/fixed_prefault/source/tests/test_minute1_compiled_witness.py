"""Exact final-hook minute admission transport; synthetic primal, no model run.

Only the external source-authentication boundary is substituted. This file
does not create an accepted producer/review file or waive source admission for
the real runner. Source byte and review checks have separate rejection tests.
"""
from copy import deepcopy
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_annual_program_witness import actual,legacy_case
from test_annual_compiled_flow_input import fixture
from test_minute1_source_profile import descriptor
from tools.annual_program import witness
from tools.annual_program.compiled_flow_input import MINUTE1_SOURCE_SCHEMA,build_compiled_flow_input
from tools.annual_program.source_runtime import AnnualSourceMixin
from tools.minute1_20261103 import profile

ROOT=Path(__file__).resolve().parents[1]


def minute_capture(actual,monkeypatch,nvars=47):
    value=descriptor();files=value['source_files']
    value['recorder_sha256']=files['tools/annual_program/witness.py']=hashlib.sha256(Path(witness.__file__).read_bytes()).hexdigest()
    files['tools/annual_program/source_runtime.py']=hashlib.sha256((ROOT/'tools/annual_program/source_runtime.py').read_bytes()).hexdigest()
    reference=dict(path=str((actual.old.producer/'synthetic_not_a_real_admission.json').resolve()),sha256='d'*64)
    checks=[]
    def source_check(root,ref,*,source_hashes=None):
        assert Path(root).resolve()==ROOT and ref==reference
        assert source_hashes is None or source_hashes==files
        checks.append(deepcopy(ref));return deepcopy(value)
    monkeypatch.setattr(profile,'verify_source_admission',source_check)
    origin=dict(schema=MINUTE1_SOURCE_SCHEMA,source_root=str(ROOT),source_files=deepcopy(files),
        leaf_function='tools.operating_response.physical_balance.solve_physical',
        source_profile=witness.contracts.MINUTE1_CAPTURE_PROFILE,source_admission=deepcopy(reference))
    def compiled(root,sources,*,source_admission=None):
        assert Path(root).resolve()==ROOT and sources==files and source_admission==reference
        checks.append(('compiled',deepcopy(source_admission)));return deepcopy(origin)
    monkeypatch.setattr(witness,'compiled_origin',compiled)
    raw=actual.result['solver_backend_layers'][-1]['attempts'][0]['raw_x']
    if nvars==48:raw.append(.123)
    values=fixture(nvars);values['raw_x']=raw;values['source_origin']=origin
    actual.result['_compiled_flow_input']=build_compiled_flow_input(**values)
    class Base:
        def _after_actual_segment(self,segment,result):return 'physical commits retained'
    class Core(AnnualSourceMixin,Base):pass
    core=Core();core.__dict__.update(actual.core.__dict__)
    core.env=SimpleNamespace(root=ROOT,minute1_source_admission=deepcopy(reference),minute1_source_freeze=deepcopy(files))
    actual.core=core
    return actual,reference,checks


@pytest.mark.parametrize('nvars',[47,48])
def test_minute_exact_final_hook_consumes_runtime_and_packet_admission(actual,monkeypatch,nvars):
    current,reference,checks=minute_capture(actual,monkeypatch,nvars)
    assert current.core._after_actual_segment(current.segment,current.result)=='physical commits retained'
    receipt=current.segment[witness.FIELD]
    metadata=current.core.solver_calls[receipt['solver_call_index']]['annual_actual_witness']
    assert metadata['compiled_flow_binding']['source_admission']==reference
    assert metadata['original_native_source_binding']['source_admission']==reference
    assert metadata['original_native_source_binding']['source_profile']==witness.contracts.MINUTE1_CAPTURE_PROFILE
    assert metadata['compiled_flow_input']['source_origin']['source_admission']==reference
    assert ('compiled',reference) in checks and len(checks)>=4
    assert receipt['raw_x_length']==nvars and receipt['status']=='recorded'


@pytest.mark.parametrize('change',['packet_ref','historical_origin','foreign_hook','frozen_map','combined_context','missing_final_packet'])
def test_minute_admission_or_hook_mismatch_fails_before_success_sink(actual,monkeypatch,change):
    current,reference,checks=minute_capture(actual,monkeypatch)
    if change in ('packet_ref','historical_origin'):
        values=fixture();values['raw_x']=current.result['solver_backend_layers'][-1]['attempts'][0]['raw_x']
        origin=deepcopy(current.result['_compiled_flow_input']['source_origin'])
        if change=='packet_ref':origin['source_admission']['sha256']='0'*64
        else:
            origin['schema']=witness.SOURCE_SCHEMA;origin.pop('source_admission');origin.pop('source_profile')
        values['source_origin']=origin;current.result['_compiled_flow_input']=build_compiled_flow_input(**values)
    elif change=='frozen_map':current.core.env.minute1_source_freeze['tools/minute1_20261103/source.py']='0'*64
    elif change=='combined_context':current.core.env.annual_program_data_context={}
    elif change=='missing_final_packet':current.result.pop('_compiled_flow_input');current.result['_sf2_fuel_receipt']={'role':'actual_interval'}
    with pytest.raises((ValueError,AssertionError)):
        if change=='foreign_hook':witness.record_actual_witness(current.core,current.segment,current.result)
        elif change=='missing_final_packet':
            from tools.annual_program.compiled_capture import capture_scope
            origin=dict(schema=MINUTE1_SOURCE_SCHEMA,source_root=str(ROOT),source_files=current.core.env.minute1_source_freeze,
                leaf_function='tools.operating_response.physical_balance.solve_physical',source_profile=witness.contracts.MINUTE1_CAPTURE_PROFILE,
                source_admission=reference)
            with capture_scope(origin):current.core._after_actual_segment(current.segment,current.result)
        else:current.core._after_actual_segment(current.segment,current.result)
    assert witness.FIELD not in current.segment and current.core.solver_calls==[]
