"""Pure causal HRSG heat-ready gate; no environment, solve or episode."""
import copy
import importlib
import sys
from pathlib import Path

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def api():
    try:return importlib.import_module('tools.operating_response.thermal_readiness')
    except ModuleNotFoundError as exc:pytest.fail('Missing pure HRSG readiness helper: '+str(exc),pytrace=False)


def hardware():
    return {'GT_1':1.,'GT_2':1.,'GT_3':1.,'HRSG_1':.6,'HRSG_2':.7,'HRSG_3':1.,'GRID':.5,'BOILER_1':1.}


def view(a,*,time=20.,running=(True,True,False),last_on=(-60.,10.,-60.),delay=15.,availability=None):
    return a.hrsg_thermal_readiness(hardware() if availability is None else availability,time_minute=time,
        gt_running=list(running),last_actual_on_minute=list(last_on),config={'warmup_delay_minutes':delay})


def test_zero_default_leaves_original_full_availability_unchanged():
    a=api();f=hardware();original=copy.deepcopy(f)
    result=view(a,running=(False,False,False),delay=0.,availability=f)
    assert a.validate_hrsg_thermal_config()=={'warmup_delay_minutes':0.}
    assert result['effective_availability']==original==f
    assert result['hardware_availability']==original


def test_positive_delay_masks_new_or_off_heat_but_keeps_continuous_online_and_unrelated_ports():
    a=api();f=hardware();result=view(a,availability=f)
    assert result['effective_availability']==dict(f,HRSG_2=0.,HRSG_3=0.)
    assert result['devices'][0]['thermal_ready'] is True
    assert result['devices'][1]['thermal_ready'] is False
    assert result['devices'][1]['heat_ready_minute']==25.
    assert result['devices'][2]['heat_ready_minute'] is None
    assert result['clock_history_available'] is True


def test_exact_causal_boundary_restores_raw_hrsg_fraction_and_never_restores_hardware_fault():
    a=api();before=view(a,time=24.9999);at=view(a,time=25.)
    assert before['effective_availability']['HRSG_2']==0.
    assert at['effective_availability']['HRSG_2']==.7
    f=hardware();f['HRSG_2']=0.
    assert view(a,time=25.,availability=f)['effective_availability']['HRSG_2']==0.
    f=hardware();f['GT_2']=0.
    assert view(a,time=25.,availability=f)['effective_availability']['HRSG_2']==0.
    assert f['HRSG_2']==.7


def test_trip_and_real_restart_use_supplied_actual_history_without_reinitializing_other_gt():
    a=api();f=hardware();running=[True,False,False];last_on=[-60.,10.,-60.]
    original=copy.deepcopy((f,running,last_on))
    stopped=a.hrsg_thermal_readiness(f,time_minute=30.,gt_running=running,last_actual_on_minute=last_on,
        config={'warmup_delay_minutes':15.})
    assert stopped['effective_availability']['HRSG_2']==0.
    restarted=view(a,time=40.,running=(True,True,False),last_on=(-60.,35.,-60.))
    assert restarted['devices'][1]['heat_ready_minute']==50.
    assert restarted['effective_availability']['HRSG_1']==.6
    assert restarted['effective_availability']['HRSG_2']==0.
    assert (f,running,last_on)==original


def test_next_boundary_uses_only_running_last_on_and_returns_none_at_or_after_deadline():
    a=api();args=dict(time_minute=20.,gt_running=[True,True,False],last_actual_on_minute=[-60.,10.,18.],
        config={'warmup_delay_minutes':15.})
    assert a.next_hrsg_ready_minute(**args)==25.
    assert a.next_hrsg_ready_minute(**dict(args,time_minute=25.)) is None
    assert a.next_hrsg_ready_minute(**dict(args,gt_running=[True,False,False])) is None
    assert a.next_hrsg_ready_minute(**dict(args,config={'warmup_delay_minutes':0.})) is None


def test_bootstrap_without_clock_is_explicit_not_a_new_fictional_gt_history():
    a=api();f=hardware()
    result=a.hrsg_thermal_readiness(f,time_minute=0.,gt_running=None,last_actual_on_minute=None,
        config={'warmup_delay_minutes':15.})
    assert result['effective_availability']==f and result['clock_history_available'] is False
    assert all(d['thermal_ready'] is None for d in result['devices'])
    assert a.next_hrsg_ready_minute(time_minute=0.,gt_running=None,last_actual_on_minute=None,
        config={'warmup_delay_minutes':15.}) is None


@pytest.mark.parametrize('config',[{'warmup_delay_minutes':-1.},{'warmup_delay_minutes':True},
    {'warmup_delay_minutes':float('nan')},{'gt_start_delay_minutes':15.}])
def test_config_rejects_invalid_or_unrelated_parameters(config):
    with pytest.raises(ValueError):api().validate_hrsg_thermal_config(config)


def test_partial_or_future_clock_is_rejected():
    a=api()
    with pytest.raises(ValueError):
        a.hrsg_thermal_readiness(hardware(),time_minute=0.,gt_running=[True,False,False],last_actual_on_minute=None,
            config={'warmup_delay_minutes':15.})
    with pytest.raises(ValueError):view(a,last_on=(-60.,25.,-60.))
