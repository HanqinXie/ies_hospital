"""Exact actual-result evidence from the inherited hook in a local runtime."""
from copy import deepcopy
import hashlib

import pytest

from test_annual_program_witness import actual, legacy_case, api as witness_api
from test_annual_compiled_witness import captured
from test_eight_fault_local_runtime import registered, construct, ROOT, api
from reviewed_source_fixtures import reviewed_source_bytes


@pytest.fixture
def local(captured, registered):
    env, calls, context = construct(registered)
    env.reset(scenario=registered.case['scenario'])
    core = env.core
    core.__dict__.update({k:v for k,v in captured.core.__dict__.items() if k!='env'})
    core.env = env
    captured.core = core
    return captured


def test_local_compiled_capture_after_native_commit_preserves_exact_argument(local):
    before = deepcopy(local.result)
    assert local.core._after_actual_segment(local.segment, local.result) == 'committed'
    assert local.segment['native_commit'] is local.result
    assert local.result == before
    receipt = local.segment[witness_api().FIELD]
    assert receipt['status'] == 'recorded'
    logged = local.core.solver_calls[receipt['solver_call_index']]['annual_actual_witness']
    assert logged['compiled_flow_binding']['status'] == 'recorded'
    assert logged['compiled_flow_input'] == before['_compiled_flow_input']
    assert local.core.env.annual_program_data_context is None
    assert len(local.core.solver_calls) == local.writer.entry_count == 1


@pytest.mark.parametrize('change', ['context', 'scene', 'calendar', 'config', 'operating_contract', 'dual', 'hook_source'])
def test_bad_local_binding_never_publishes_recorded(local, change):
    env = local.core.env
    if change == 'context': env.local_actual_witness_source_context = {'self_asserted':True}
    elif change == 'scene': env.scenario['response_delay_minutes'] = 0.
    elif change == 'calendar': env.model_closure_events = []
    elif change == 'config': env.operating_model_config['changed'] = True
    elif change == 'operating_contract': env.operating_execution_contract['diesel_dispatch_mode']='unregistered-mode'
    elif change == 'dual': env.annual_program_data_context = {'runtime_freeze_receipt':{}}
    else: env.local_actual_witness_source_context.declaration['bindings']['source']['tools/annual_program/source_runtime.py'] = 'a'*64
    with pytest.raises(ValueError): local.core._after_actual_segment(local.segment, local.result)
    assert witness_api().FIELD not in local.segment
    assert local.core.solver_calls == [] and local.writer.entry_count == 0


def test_root_drift_retains_native_unknown_source_receipt_without_recorded_flag(local):
    # The original recorder classifies an unrecognized native source before
    # attempting compiled attribution. Keep that explicit unknown behavior.
    local.core.env.root = ROOT.parent
    local.core._after_actual_segment(local.segment, local.result)
    assert local.segment[witness_api().FIELD]['status'] == 'unknown'
    assert local.core.solver_calls == [] and local.writer.entry_count == 0


def test_equal_candidate_cannot_replace_the_exact_final_actual_result(local):
    candidate = deepcopy(local.result)
    with pytest.raises(ValueError, match='exact final actual-result hook'):
        witness_api().record_actual_witness(local.core, local.segment, candidate)
    assert witness_api().FIELD not in local.segment
    assert local.core.solver_calls == []


def test_local_sink_io_failure_never_publishes_recorded(local):
    local.writer.close()
    with pytest.raises(RuntimeError): local.core._after_actual_segment(local.segment, local.result)
    assert witness_api().FIELD not in local.segment


def test_new_recorder_and_frozen_cache_pairs_remain_exact():
    from tools.annual_program import flow_source_contracts as c
    # "New" refers to the reviewed output-only release here. Its exact old
    # recorder assertions must survive the current separate scientific epoch.
    recorder = hashlib.sha256(reviewed_source_bytes('tools/annual_program/witness.py')).hexdigest()
    assert recorder==c.CAPTURE_RECORDER_SHA256
    current = dict(c.CAPTURE_NATIVE_SOURCE_CONTRACT, **{'src/hospital_ies/solver_backend.py':c.EXACT_CACHE_BACKEND_SHA256})
    old16 = dict(c.CAPTURE_NATIVE_SOURCE_CONTRACT, **{'src/hospital_ies/solver_backend.py':c.FROZEN16_EXACT_CACHE_BACKEND_SHA256})
    assert c.classify_recorder_source_pair(recorder,current)['compiled_source_profile_allowed']
    assert c.classify_recorder_source_pair(c.FROZEN_A60602_CAPTURE_RECORDER_SHA256,current)['compiled_source_profile_allowed']
    assert c.classify_recorder_source_pair(c.FROZEN16_CAPTURE_RECORDER_SHA256,old16)['compiled_source_profile_allowed']
    assert not c.classify_recorder_source_pair(recorder,old16)['supported']
    assert not c.classify_recorder_source_pair(c.FROZEN12_CAPTURE_RECORDER_SHA256,current)['supported']
    mixed = dict(current, **{'tools/operating_response/physical_balance.py':'a'*64})
    assert not c.classify_recorder_source_pair(recorder,mixed)['supported']


def test_current_scientific_recorder_is_separate_from_frozen_capture_pairs():
    from tools.annual_program import flow_source_contracts as c
    from reviewed_sci1_source_fixtures import reviewed_source_bytes
    recorder=hashlib.sha256(reviewed_source_bytes('tools/annual_program/witness.py')).hexdigest()
    current={name:hashlib.sha256(reviewed_source_bytes(name)).hexdigest() for name in c.LEGACY_NATIVE_SOURCE_CONTRACT}
    assert recorder==c.SCIENTIFIC_CAPTURE_RECORDER_SHA256
    assert c.match_native_source_profile(current)==c.SCIENTIFIC_CAPTURE_PROFILE
    assert c.classify_recorder_source_pair(recorder,current)['compiled_source_profile_allowed']
    frozen=dict(c.CAPTURE_NATIVE_SOURCE_CONTRACT,**{'src/hospital_ies/solver_backend.py':c.EXACT_CACHE_BACKEND_SHA256})
    assert not c.classify_recorder_source_pair(recorder,frozen)['supported']
    assert not c.classify_recorder_source_pair(c.CAPTURE_RECORDER_SHA256,current)['supported']
