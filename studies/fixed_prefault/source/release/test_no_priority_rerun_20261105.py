"""New paired lifecycle gates; no physical optimization is started here."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[1]


def api():
    spec=importlib.util.spec_from_file_location('fixed_rerun',ROOT/'release/no_priority_rerun_20261105.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_old_contract_and_mutated_frozen_shares_are_rejected():
    a=api();reference={'schema':'reference','carrier_shares':[[.25]*3]*4}
    row=dict(actual_result_owner='arithmetic_forward',forward_contract=a.CONTRACT,dispatch_optimizer_calls=0,
        strict_unattended_forward=dict(priority_order_used=False,running_reference={
            '_fixed_branches':deepcopy(reference),'_steam_abs_fraction':.2}))
    a.check_u_segment(row,reference,.2)
    row['strict_unattended_forward']['running_reference']['_fixed_branches']['schema']='changed'
    with pytest.raises(ValueError,match='frozen'):
        a.check_u_segment(row,reference,.2)
    row['strict_unattended_forward']['running_reference']['_fixed_branches']=reference
    row['forward_contract']='hospital_ies.fixed_reference_arithmetic.v1'
    with pytest.raises(ValueError,match='contract'):
        a.check_u_segment(row,reference,.2)


def test_exact_source_freeze_includes_execution_tools(tmp_path):
    a=api();p=tmp_path/'worker.py';p.write_text('first',encoding='utf8')
    pin=a.pin(p);a.check_pin(pin)
    p.write_text('second',encoding='utf8')
    with pytest.raises(ValueError,match='changed'):
        a.check_pin(pin)


def test_all_original_producer_pointer_shapes_resolve_to_native_result():
    a=api()
    assert a.producer_path({'result_path':'E:/run/RESULT.json'})==Path('E:/run')
    assert a.producer_path({'path':'E:/unit/RESULT.json'})==Path('E:/unit')


def test_failure_records_retain_fatal_guard_and_observed_prefix():
    a=api()
    class Fatal(BaseException):pass
    value=a.failure_record(Fatal('forbidden actual entry'),63.25)
    assert value['observed_until_minute']==63.25
    assert value['type']=='Fatal' and 'forbidden actual entry' in value['message']
    assert value['complete_trajectory_claimed'] is False


def test_audit_context_uses_actual_model_defaults_and_keeps_explicit_values():
    a=api();original={'reset_physical_context':{'boiler_ready':False}}
    result=a.normalized_audit_resolved(original)
    assert result['reset_physical_context']['recovery_pump_kW_per_kW']==.02
    assert result['reset_physical_context']['recovery_fan_kW_per_kW']==0.
    assert result['reset_physical_context']['boiler_ready'] is False
    assert original=={'reset_physical_context':{'boiler_ready':False}}
    original['reset_physical_context']['recovery_fan_kW_per_kW']=.1
    assert a.normalized_audit_resolved(original)['reset_physical_context']['recovery_fan_kW_per_kW']==.1


def test_first_response_observation_follows_deferred_fault_and_command_boundary():
    from types import SimpleNamespace
    a=api();order=[];state={'fault_applied':False}
    def boundary():state['fault_applied']=True;order.append('boundary')
    capture=SimpleNamespace(env=SimpleNamespace(minute1_variant='responsive',
        core=SimpleNamespace(time=60.,boundary=boundary)),flush=lambda:order.append('flush'))
    a.settle_initial_response_boundary(capture)
    order.append('observe')
    assert state['fault_applied'] is True and order==['boundary','flush','observe']
    capture.env.minute1_variant='unattended'
    with pytest.raises(ValueError,match='response'):
        a.settle_initial_response_boundary(capture)
