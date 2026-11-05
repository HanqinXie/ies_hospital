"""Current capture/hook tests and parse-only historical protocol fixtures."""
from copy import deepcopy
import hashlib
from pathlib import Path
import pytest

from test_annual_program_witness import actual, legacy_case, api
from test_annual_compiled_flow_input import fixture as leaf_values
from reviewed_source_fixtures import reviewed_source_bytes


@pytest.fixture
def captured(actual,monkeypatch):
    root=Path(__file__).parents[1]
    monkeypatch.syspath_prepend(str(root/'src'))
    from tools.annual_program import flow_source_contracts as c
    from tools.annual_program.compiled_flow_input import build_compiled_flow_input
    from tools.annual_program.source_runtime import AnnualSourceMixin
    pins={name:hashlib.sha256((root/name).read_bytes()).hexdigest()
          for name in (*c.LEGACY_NATIVE_SOURCE_CONTRACT,
                       'tools/annual_program/compiled_capture.py','tools/annual_program/compiled_flow_input.py',
                       'tools/annual_program/source_runtime.py')}
    native={name:pins[name] for name in c.LEGACY_NATIVE_SOURCE_CONTRACT}
    # Current science has its own profile and complete source closure. It must
    # never be relabelled as the historical output-only AST migration.
    additional=c.sci_fuel_capture_source_extra_contract(native)
    assert additional is not None
    pins.update({name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in additional})
    origin=api().compiled_origin(root,pins)
    values=leaf_values();values['source_origin']=origin
    values['raw_x']=actual.result['solver_backend_layers'][-1]['attempts'][0]['raw_x']
    actual.result['_compiled_flow_input']=build_compiled_flow_input(**values)
    class Base:
        def _after_actual_segment(self,segment,result):return 'original hook completed'
    class Core(AnnualSourceMixin,Base):pass
    core=Core();core.__dict__.update(actual.core.__dict__)
    core.env.root=root
    core.env.annual_program_data_context={'runtime_freeze_receipt':{'source_sha256':pins}}
    actual.core=core
    return actual


@pytest.fixture
def legacy_captured(actual):
    """Construct an explicit old ABI protocol fixture without executing it.

    Historical source bytes are hashed/parsed only. A current basic witness
    externalizes the truthful synthetic rows; the fixture then explicitly
    assembles the old source-bound packet metadata for legacy reader tests.
    This is not evidence of an actual old physical/hook execution. Current
    real-hook protocol coverage lives in captured and the SF1 witness tests.
    """
    from tools.annual_program import flow_source_contracts as c
    from tools.annual_program.compiled_flow_input import build_compiled_flow_input,validate_compiled_flow_input,SOURCE_SCHEMA
    root=actual.old.source
    names=(*c.LEGACY_NATIVE_SOURCE_CONTRACT,*c.CAPTURE_AUXILIARY_SOURCE_CONTRACT,
        'src/hospital_ies/solver_result_cache.py','tools/annual_program/witness.py',
        'tools/annual_program/source_runtime.py')
    for name in names:
        path=root/name;path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(reviewed_source_bytes(name))
    pins={name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in names}
    native={name:pins[name] for name in c.LEGACY_NATIVE_SOURCE_CONTRACT}
    assert c.recognized_capture_native_source_contract(native)==native
    origin=dict(schema=SOURCE_SCHEMA,source_root=str(root.resolve()),
        source_files={name:pins[name] for name in (*native,*c.CAPTURE_AUXILIARY_SOURCE_CONTRACT)},
        leaf_function='tools.operating_response.physical_balance.solve_physical')
    values=leaf_values();values['source_origin']=origin
    values['raw_x']=actual.result['solver_backend_layers'][-1]['attempts'][0]['raw_x']
    actual.result['_compiled_flow_input']=build_compiled_flow_input(**values)

    class HistoricalProtocolCore:
        def _after_actual_segment(self,segment,result):
            # Explicit synthetic old-format envelope, never a production hook.
            packet=validate_compiled_flow_input(result['_compiled_flow_input'],
                raw_x=result['solver_backend_layers'][-1]['attempts'][0]['raw_x'])
            plain={key:value for key,value in result.items() if key!='_compiled_flow_input'}
            receipt=api().record_actual_witness(self,segment,plain)
            assert receipt['status']=='recorded'
            metadata=self.solver_calls[receipt['solver_call_index']]['annual_actual_witness']
            metadata.update(recorder_source_sha256=c.CAPTURE_RECORDER_SHA256,
                compiled_flow_input=deepcopy(packet),compiled_flow_binding=dict(
                    schema='hospital_ies.annual_program.final_compiled_flow_binding.v1',status='recorded',reasons=[],
                    exact_final_result_argument=True,payload_content_sha256=packet['content_sha256'],
                    source_profile=c.CAPTURE_PROFILE,source_root=origin['source_root'],
                    source_files=deepcopy(origin['source_files']),leaf_function=origin['leaf_function'],
                    actual_hook=dict(file='tools/annual_program/source_runtime.py',
                        function='AnnualSourceMixin._after_actual_segment',sha256=pins['tools/annual_program/source_runtime.py'])))
            receipt.update(recorder_source_sha256=c.CAPTURE_RECORDER_SHA256,
                compiled_flow_input_sha256=packet['content_sha256'])
            segment[api().FIELD]=deepcopy(receipt)
            return 'synthetic historical protocol assembled'

    core=HistoricalProtocolCore();core.__dict__.update(actual.core.__dict__)
    actual.core=core
    return actual


def test_historical_capture_origin_retains_exact_sixteen_source_files(legacy_captured):
    c=legacy_captured;c.core._after_actual_segment(c.segment,c.result)
    receipt=c.segment[api().FIELD]
    binding=c.core.solver_calls[receipt['solver_call_index']]['annual_actual_witness']['compiled_flow_binding']
    assert len(binding['source_files'])==16
    assert binding['source_profile']=='completed_leaf_capture_v1'


def test_new_recorder_keeps_legacy_witness_but_explicitly_marks_missing_compiled_inputs(actual):
    m=api();receipt=m.record_actual_witness(actual.core,actual.segment,actual.result)
    assert receipt['status']=='recorded'
    metadata=actual.core.solver_calls[receipt['solver_call_index']]['annual_actual_witness']
    assert metadata['compiled_flow_input'] is None
    binding=metadata['compiled_flow_binding']
    assert binding['status']=='unknown'
    assert binding['reasons']==['missing_final_compiled_flow_input']
    assert receipt['compiled_flow_input_sha256'] is None


def test_legacy_leaf_cannot_supply_a_self_asserted_compiled_packet(actual):
    actual.result['_compiled_flow_input']={'source_origin':{'source_root':'unverified'}}
    before=deepcopy(actual.result)
    with pytest.raises(ValueError,match='unsupported physical leaf profile'):
        api().record_actual_witness(actual.core,actual.segment,actual.result)
    assert actual.result==before and actual.core.solver_calls==[]
    assert api().FIELD not in actual.segment


def test_native_source_binding_names_the_exact_legacy_profile(actual):
    binding=api().native_source_binding(actual.old.source)
    assert binding['supported'] is True
    assert binding['source_profile']=='native_return_v1'


def test_no_compiled_input_from_an_alternative_probe_is_substituted(actual):
    actual.result['m22_mode_selection']['candidates'][0]['_compiled_flow_input']={'foreign':True}
    receipt=api().record_actual_witness(actual.core,actual.segment,actual.result)
    metadata=actual.core.solver_calls[receipt['solver_call_index']]['annual_actual_witness']
    assert metadata['compiled_flow_input'] is None
    assert metadata['compiled_flow_binding']['status']=='unknown'


def test_capture_is_bound_through_the_original_actual_hook_and_final_argument(sci1_captured):
    captured=sci1_captured
    m=api();before=deepcopy(captured.result)
    assert captured.core._after_actual_segment(captured.segment,captured.result)=='original hook completed'
    receipt=captured.segment[m.FIELD]
    metadata=captured.core.solver_calls[receipt['solver_call_index']]['annual_actual_witness']
    payload=metadata['compiled_flow_input'];binding=metadata['compiled_flow_binding']
    assert captured.result==before
    assert payload==before['_compiled_flow_input']
    assert binding['status']=='recorded' and binding['exact_final_result_argument'] is True
    assert binding['payload_content_sha256']==receipt['compiled_flow_input_sha256']==payload['content_sha256']
    assert binding['source_files']==payload['source_origin']['source_files']
    from tools.annual_program import flow_source_contracts as c
    assert len(binding['source_files'])==14+2+len(c.SCIENTIFIC_CAPTURE_EXTRA_SOURCE_CONTRACT)
    assert binding['source_profile']==c.SCIENTIFIC_CAPTURE_PROFILE
    assert binding['actual_hook']['file']=='tools/annual_program/source_runtime.py'
    assert payload['actual_execution_bound'] is payload['source_verification_performed'] is False
    assert payload['physics_validation_performed'] is False


def test_direct_recorder_call_cannot_qualify_a_packet_outside_the_actual_hook(captured):
    with pytest.raises(ValueError,match='exact final actual-result hook'):
        api().record_actual_witness(captured.core,captured.segment,captured.result)
    assert api().FIELD not in captured.segment and captured.core.solver_calls==[]


@pytest.mark.parametrize('change',['raw_x','dt','root','source','hook_freeze','helper_freeze'])
def test_capture_identity_mismatches_fail_before_append_or_recorded_flag(captured,change):
    from tools.annual_program.compiled_flow_input import build_compiled_flow_input
    payload=captured.result['_compiled_flow_input']
    if change=='raw_x':captured.result['solver_backend_layers'][-1]['attempts'][0]['raw_x'][46]=.2
    elif change in ('dt','root','source'):
        values=leaf_values();values['source_origin']=deepcopy(payload['source_origin'])
        values['raw_x']=captured.result['solver_backend_layers'][-1]['attempts'][0]['raw_x']
        if change=='dt':values['dt_h']=.1
        elif change=='root':values['source_origin']['source_root']='foreign_root'
        else:values['source_origin']['source_files']['tools/annual_program/compiled_capture.py']='a'*64
        captured.result['_compiled_flow_input']=build_compiled_flow_input(**values)
    else:
        key='tools/annual_program/source_runtime.py' if change=='hook_freeze' else 'tools/annual_program/compiled_capture.py'
        captured.core.env.annual_program_data_context['runtime_freeze_receipt']['source_sha256'][key]='a'*64
    with pytest.raises(ValueError):captured.core._after_actual_segment(captured.segment,captured.result)
    assert api().FIELD not in captured.segment and captured.core.solver_calls==[]


@pytest.mark.parametrize('start',[107.,525500.])
def test_compiled_clock_roundoff_retains_original_solve_duration(captured,start):
    from test_annual_compiled_flow_input import rehash
    solved_minutes=.8292027169925059
    captured.segment.update(start_minute=start,end_minute=start+solved_minutes)
    captured.core.time=captured.segment['end_minute']
    packet=captured.result['_compiled_flow_input'];packet['dt_h']=solved_minutes/60.
    rehash(packet);before=deepcopy(packet)
    assert packet['dt_h']!=(captured.segment['end_minute']-start)/60.
    captured.core._after_actual_segment(captured.segment,captured.result)
    receipt=captured.segment[api().FIELD]
    logged=captured.core.solver_calls[receipt['solver_call_index']]['annual_actual_witness']
    assert logged['compiled_flow_input']==before
    assert captured.result['_compiled_flow_input']==before
    assert logged['compiled_flow_binding']['status']=='recorded'


@pytest.mark.parametrize('dt_h',[1/60.,True,False,float('nan'),float('inf'),-1.,0.])
def test_invalid_interval_duration_does_not_publish_a_compiled_witness(captured,dt_h):
    from test_annual_compiled_flow_input import rehash
    captured.segment.update(start_minute=107.,end_minute=107.82920271699251)
    captured.core.time=captured.segment['end_minute']
    packet=captured.result['_compiled_flow_input'];packet['dt_h']=dt_h
    if dt_h==dt_h and dt_h not in (float('inf'),float('-inf')):rehash(packet)
    with pytest.raises(ValueError):captured.core._after_actual_segment(captured.segment,captured.result)
    assert api().FIELD not in captured.segment and captured.core.solver_calls==[]


@pytest.fixture
def sci1_captured(actual):
    """Construct an explicit old ABI protocol fixture without executing it.

    Historical source bytes are hashed/parsed only. A current basic witness
    externalizes the truthful synthetic rows; the fixture then explicitly
    assembles the old source-bound packet metadata for legacy reader tests.
    The hook return is a synthetic ABI literal, not evidence of old physical/hook execution. Current
    real-hook protocol coverage lives in captured and the SF1 witness tests.
    """
    from tools.annual_program import flow_source_contracts as c
    from reviewed_sci1_source_fixtures import reviewed_source_bytes
    from tools.annual_program.compiled_flow_input import build_compiled_flow_input,validate_compiled_flow_input,SOURCE_SCHEMA
    root=actual.old.source
    names=(*c.LEGACY_NATIVE_SOURCE_CONTRACT,*c.CAPTURE_AUXILIARY_SOURCE_CONTRACT,*c.SCIENTIFIC_CAPTURE_EXTRA_SOURCE_CONTRACT,
        'src/hospital_ies/solver_result_cache.py','tools/annual_program/witness.py',
        'tools/annual_program/source_runtime.py')
    for name in names:
        path=root/name;path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(reviewed_source_bytes(name))
    pins={name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in names}
    native={name:pins[name] for name in c.LEGACY_NATIVE_SOURCE_CONTRACT}
    assert c.recognized_scientific_native_source_contract(native)==native
    origin=dict(schema=SOURCE_SCHEMA,source_root=str(root.resolve()),
        source_files={name:pins[name] for name in (*native,*c.CAPTURE_AUXILIARY_SOURCE_CONTRACT,*c.SCIENTIFIC_CAPTURE_EXTRA_SOURCE_CONTRACT)},
        leaf_function='tools.operating_response.physical_balance.solve_physical')
    values=leaf_values();values['source_origin']=origin
    values['raw_x']=actual.result['solver_backend_layers'][-1]['attempts'][0]['raw_x']
    actual.result['_compiled_flow_input']=build_compiled_flow_input(**values)

    class HistoricalProtocolCore:
        def _after_actual_segment(self,segment,result):
            # Explicit synthetic old-format envelope, never a production hook.
            packet=validate_compiled_flow_input(result['_compiled_flow_input'],
                raw_x=result['solver_backend_layers'][-1]['attempts'][0]['raw_x'])
            plain={key:value for key,value in result.items() if key!='_compiled_flow_input'}
            receipt=api().record_actual_witness(self,segment,plain)
            assert receipt['status']=='recorded'
            metadata=self.solver_calls[receipt['solver_call_index']]['annual_actual_witness']
            metadata.update(recorder_source_sha256=c.SCIENTIFIC_CAPTURE_RECORDER_SHA256,
                compiled_flow_input=deepcopy(packet),compiled_flow_binding=dict(
                    schema='hospital_ies.annual_program.final_compiled_flow_binding.v1',status='recorded',reasons=[],
                    exact_final_result_argument=True,payload_content_sha256=packet['content_sha256'],
                    source_profile=c.SCIENTIFIC_CAPTURE_PROFILE,source_root=origin['source_root'],
                    source_files=deepcopy(origin['source_files']),leaf_function=origin['leaf_function'],
                    actual_hook=dict(file='tools/annual_program/source_runtime.py',
                        function='AnnualSourceMixin._after_actual_segment',sha256=pins['tools/annual_program/source_runtime.py'])))
            receipt.update(recorder_source_sha256=c.SCIENTIFIC_CAPTURE_RECORDER_SHA256,
                compiled_flow_input_sha256=packet['content_sha256'])
            segment[api().FIELD]=deepcopy(receipt)
            return 'original hook completed'

    core=HistoricalProtocolCore();core.__dict__.update(actual.core.__dict__)
    actual.core=core
    return actual

