import copy
import importlib
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from tools.annual_readiness.station import ContinuousStationState

def api():
    try:return importlib.import_module('tools.annual_readiness.environment')
    except ModuleNotFoundError:pytest.fail('Annual readiness adapter is missing',pytrace=False)

def plant():return NS(ec_electric_kW_per_rt=.77,abs_electric_kW_per_rt=.14)

def segment(grid=21.,ups=0.,bus='GRID'):
    return dict(start_minute=0.,end_minute=15.,action=dict(gt_electric_kW=[0.,0.,0.],
        grid_import_kW=grid,diesel_kW=0.,ups_discharge_kW=ups,boiler_auxiliary_kW=1.,
        electric_chiller_cooling_kW=[0.,0.,0.],absorption_cooling_kW=0.),
        service_request_ledger=dict(delivered_kW=[[20.,0.,0.],[0.,0.,0.],[0.,0.,0.],[0.,0.,0.]]))

def core(status='OFF'):
    state=ContinuousStationState()
    state.commit_supply(0.,1440.,normal_source_supported=False)
    return NS(station=state,state=NS(diesel_status=status),grid_bus_state='GRID',
              executed=dict(grid_import_kW=21.),annual_last_supply_proof=dict(supported=True))

def hardware():
    return dict(GRID=1.,DIESEL=1.,GT_1=1.,GT_2=1.,GT_3=1.,STATION_AC=1.,GT_CONTROL_DC=1.,DG_START_DC=1.)

def test_settled_grid_proof_pays_actual_plant_not_ups():
    before=segment(); a=api().settled_normal_source(before,plant(),grid_bus_state='GRID')
    assert a['supported'] and a['proof']['electric_balance_valid']
    assert before==segment()
    assert not api().settled_normal_source(segment(0.,21.),plant(),grid_bus_state='GRID')['supported']
    assert not api().settled_normal_source(segment(),plant(),grid_bus_state='TRANSFER_OPEN')['supported']
    assert not api().settled_normal_source(segment(20.),plant(),grid_bus_state='GRID')['supported']

def test_actual_hook_uses_accepted_served_before_later_ledger_export():
    class HookParent:
        def _after_actual_segment(self,s,r):s['m22_station']={}
    cls=type('Hook',(api().ReadinessCoreMixin,HookParent),{})
    c=cls();c.station=ContinuousStationState();c.grid_bus_state='GRID';c.env=NS(plant=plant())
    s=segment();del s['service_request_ledger']
    c._after_actual_segment(s,dict(served=[[20.,0.,0.],[0.,0.,0.],[0.,0.,0.],[0.,0.,0.]]))
    assert c.station.unsupported_minutes==0.
    assert c.station.normal_source_supported is True
    assert s['annual_readiness']['actual_normal_source_proof']['supported'] is True

def test_current_grid_hardware_and_contact_must_both_still_hold():
    c=core();f=hardware()
    a=api().qualify_hardware(c,1440.,f)
    assert a['GT_1']==a['DIESEL']==1.
    f['GRID']=0.
    assert api().qualify_hardware(c,1440.,f)['GT_1']==0.
    c.grid_bus_state='TRANSFER_OPEN';f['GRID']=1.
    assert api().qualify_hardware(c,1440.,f)['GT_2']==0.

def test_hardware_dc_failure_precedes_qualified_source():
    c=core();f=hardware();f['GT_CONTROL_DC']=0.
    assert api().qualify_hardware(c,1440.,f)['GT_1']==0.
    assert api().qualify_hardware(c,1440.,f)['DIESEL']==1.

@pytest.mark.parametrize('status',['READY','CONNECTED'])
def test_start_only_dc_cannot_stop_already_running_dg(status):
    c=core(status);f=hardware();f['DG_START_DC']=0.
    c.station.diesel_starts_used=2
    a=api().qualify_hardware(c,1440.,f)
    assert a['DG_START_DC']==0. and a['DIESEL']==1.
    f['DIESEL']=0.
    assert api().qualify_hardware(c,1440.,f)['DIESEL']==0.

@pytest.mark.parametrize('status',['OFF','STARTING'])
def test_start_only_dc_still_blocks_unfinished_start(status):
    c=core(status);f=hardware();f['DG_START_DC']=0.
    assert api().qualify_hardware(c,1440.,f)['DIESEL']==0.

def test_hardware_query_never_debits_or_recovers_qualification():
    c=core();before=copy.deepcopy(vars(c.station));f=hardware();raw=copy.deepcopy(f)
    for _ in range(4):api().qualify_hardware(c,1440.,f)
    assert vars(c.station)==before and f==raw

class Parent:
    def _grid_return_ready(self,f):return True

def transfer(c,working=True,due=False,bridge=False):
    c.grid_bus_state='RETURN_PREP';c.time=100.;c.arrived_dispatch=dict(grid_import_kW=100.,diesel_kW=30.)
    c.state=NS(diesel_status='CONNECTED',diesel_energy_remaining_kWh=100.)
    c.diesel_rotor=NS(fuel_exhausted=not working)
    c.bridge_wait_started_minute=None;c.bridge_wait_fallback_reason=None
    c._critical_preservation_probe=lambda f,diesel:working if diesel else bridge
    c._destination_return_ready=lambda f:True
    c._finite_return_deadline=lambda:100. if due else 110.
    c.log=lambda *a,**k:None
    return c

def test_open_recheck_waits_on_real_dg_if_gt_lost_during_preparation():
    cls=type('Check',(api().ReadinessCoreMixin,Parent),{})
    c=transfer(cls());c.annual_config=dict(transfer_recheck=True)
    assert not c._grid_return_ready(hardware())
    assert c.bridge_wait_started_minute==100.

def test_open_recheck_keeps_explicit_finite_fallback():
    cls=type('Check',(api().ReadinessCoreMixin,Parent),{})
    c=transfer(cls(),due=True);c.annual_config=dict(transfer_recheck=True)
    assert c._grid_return_ready(hardware())
    assert c.bridge_wait_fallback_reason=='annual_finite_source_fallback'

def test_open_recheck_accepts_only_actual_current_bridge():
    cls=type('Check',(api().ReadinessCoreMixin,Parent),{})
    c=transfer(cls(),bridge=True);c.annual_config=dict(transfer_recheck=True)
    assert c._grid_return_ready(hardware())

@pytest.mark.parametrize('supplied',[False,True])
def test_qualification_boundary_is_actual_remaining_work_not_midnight(supplied):
    c=core();c.station.unsupported_minutes=1430.;c.station.normal_source_supported=supplied
    c.annual_last_supply_proof=dict(supported=supplied)
    c.time=2000.;c.annual_config=dict(requalification_minutes=None)
    c._hardware_data_at=lambda t:(None,None,None,hardware())
    # Previous supply proof cannot authorize the entire future interval:
    # actual grid imports can change at the next physical solve.
    assert api().qualification_boundary(c,3000.)==2010.

class IntervalParent:
    def _close_actual_interval(self,end,next_time,connected,f,row,cmd,sol,fixed_args):
        self.called_interval_end=next_time
        action=segment(0.)['action'];action['gt_electric_kW']=[21.,0.,0.]
        result=sol if sol is not None else dict(action=action,
            served=[[20.,0.,0.],[0.,0.,0.],[0.,0.,0.],[0.,0.,0.]])
        return next_time,result,{},None,None

def interval_core(remaining):
    cls=type('Interval',(api().ReadinessCoreMixin,IntervalParent),{})
    c=cls();c.station=ContinuousStationState();c.station.unsupported_minutes=1440.-remaining
    c.station.normal_source_supported=True;c.time=2000.;c.grid_bus_state='GRID'
    c.env=NS(plant=plant());c.executed=dict(grid_import_kW=21.)
    c.annual_last_supply_proof=dict(supported=True)
    c.annual_config=dict(requalification_minutes=None)
    c._hardware_data_at=lambda t:(None,None,None,hardware())
    return c

def test_actual_candidate_cannot_borrow_previous_source_past_half_minute():
    c=interval_core(.5)
    end,*_=c._close_actual_interval(2015.,2015.,None,hardware(),None,{},None,{})
    assert end==2000.5
    assert c.station.unsupported_minutes==1439.5

def test_zero_budget_stale_source_cannot_credit_unqualified_gt():
    c=interval_core(0.)
    with pytest.raises(RuntimeError,match='qualification'):
        c._close_actual_interval(2015.,2015.,None,hardware(),None,{},None,{})

def test_zero_budget_already_disabled_control_can_record_real_service_loss():
    c=interval_core(0.);f=hardware();f['GT_CONTROL_DC']=f['DG_START_DC']=0.
    end,*_=c._close_actual_interval(2015.,2015.,None,f,None,{},None,{})
    assert end==2015.
