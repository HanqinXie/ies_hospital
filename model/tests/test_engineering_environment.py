"""Explicit model identity/timing configuration, no annual-input fixture."""
import importlib
import sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]


def module():
    try:return importlib.import_module('tools.engineering_model.environment')
    except ModuleNotFoundError as exc:pytest.fail('Missing v14 environment: '+str(exc),pytrace=False)


def test_m0_contract_cannot_silently_accept_new_dynamics():
    m=module();base,extra=m.model_contract('M0',{})
    assert base['gt_start_delay_minutes']==0. and extra=={}
    with pytest.raises(ValueError):m.model_contract('M0',{'minimum_up_minutes':60.})


def test_finite_start_delay_is_synchronized_with_actual_parent_actuator():
    m=module();base,extra=m.model_contract('M14_engineering',{'gt_start_delay_minutes':30.})
    assert base['gt_start_delay_minutes']==extra['gt_start_delay_minutes']==30.
    assert extra['minimum_up_minutes']==60. and extra['minimum_down_minutes']==30.
    assert 'v14' in base['name']


def test_unknown_model_is_rejected():
    with pytest.raises(ValueError):module().model_contract('unregistered',{})
