"""Native queue/clock and pure accounting checks, without an IES solve.

The command and boundary functions are real production functions. Their data,
capacity-clamp and commit hooks are bounded nonphysical doubles, so these are
queue/clock tests, not a qualified minute-source or physical trajectory.
"""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import sys

import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT/'src') not in sys.path:sys.path.insert(0,str(ROOT/'src'))

from hospital_ies.state_transition import TransitionCore
from hospital_ies.resilience import empty_faults
from hospital_ies.model import load_plant
from tools.annual_program.source_runtime import AnnualSourceMixin
from tools.minute1_20261103.runtime import MinuteActualCoreMixin
from tools.sci_engineering.environment import EngineeringCore as SciCore
from tools.model_review_v30.evaluation import independent_energy_audit
from tools.annual_readiness.runner import _block_evaluation
from test_annual_readiness_runner import ORDINARY_SCI_REQUEST,segment


# Preserve the original synthetic accounting constants without borrowing the
# mutable old runner dict, including when imported after an old fake attempt.
PARAMETERS=deepcopy(dict(gas_price_usd_per_mmbtu=5.,dg_shaft_fee_USD_per_kWh=.3,
    steam_kWh_per_lb=.3,boiler_eff=.8,kWh_per_MMBtu=300.,ec_electric_kW_per_rt=.8,
    RT_TO_KW=4.,cold_pump_kW_per_kW=.02))


def test_minute_accounting_baselines_are_detached_from_legacy_runner_mutation(monkeypatch):
    import test_annual_readiness_runner as legacy
    import test_minute1_runner as minute_runner
    # Reproduce the old orchestration double returning its shared dict, then
    # the native runner replacing this value with FakeEnv.sci_config.get(...).
    monkeypatch.setitem(legacy.PARAMETERS,'cold_pump_kW_per_kW',None)
    assert PARAMETERS is not legacy.PARAMETERS
    assert minute_runner.PARAMETERS is not legacy.PARAMETERS
    assert PARAMETERS is not minute_runner.PARAMETERS
    assert PARAMETERS['cold_pump_kW_per_kW']==minute_runner.PARAMETERS['cold_pump_kW_per_kW']==.02


class QueueHarness(AnnualSourceMixin):
    def __init__(self,step_minutes=1.,onset=60.,repair=120.):
        self.time=0.;self.failed=False;self.sequence=0;self.pending_commands=[];self.events=[];self.solver_calls=[]
        self.executed=deepcopy(ORDINARY_SCI_REQUEST['ordinary_dispatch']['dispatch']);self.executed['grid_import_kW']=0.
        self.active_command=None;self.grid_outage_start=None;self.last_availability=None;self.last_shock_active=None
        self.gt_start_pending={};self.automatic_disconnected=np.zeros((4,3));self.disconnected_fraction=np.zeros((4,3));self.automatic_fraction=np.zeros((4,3))
        self.pending_shed=None;self.local_down=None;self.auto_shed_latched=False
        self.state=SimpleNamespace(disconnected_loads=np.zeros((4,3)))
        self.env=SimpleNamespace(dt_h=step_minutes/60.,dt_minutes=step_minutes,
            scenario=dict(step_minutes=step_minutes,duration_steps=int(180/step_minutes),stage=4,response_delay_minutes=15.),
            study_clock_contract='event_triggered',model_closure_events=[
                {'time_minute':onset,'availability':{'GRID':0.}},
                {'time_minute':repair,'availability':{'GRID':1.}}])
    def data_at(self,minute):return 1,SimpleNamespace(),np.ones((4,3))*100.,{'GRID':1.}
    def readiness_context(self):return {}
    def log(self,kind,**values):self.events.append(dict(kind=kind,time_minute=self.time,**deepcopy(values)))
    def _sync_connections(self,loads):pass
    def _update_support_states(self,f):pass
    def _clamp(self,dispatch,f,normal_ramp=False):return deepcopy(dispatch)
    def _commit_sources(self,dispatch):pass
    def aux(self,dispatch):return 0.
    def generate(self):return SciCore.generate_command(self,deepcopy(ORDINARY_SCI_REQUEST))
    def apply_boundary(self):return TransitionCore.boundary(self)
    def _solve(self,*args,**kwargs):raise AssertionError('No optimization/physical solve is allowed in queue tests')


def test_minute_core_uses_the_same_detached_calendar_response_clock():
    assert MinuteActualCoreMixin.delayed_at is AnnualSourceMixin.delayed_at
    core=QueueHarness()
    for minute in (0.,59.,59.999):assert core.delayed_at(minute)==(False,minute)
    for minute in (60.,60.5,61.,74.,74.999):assert core.delayed_at(minute)==(True,75.)
    assert core.delayed_at(75.)==(False,75.)
    assert core.delayed_at(120.)==(False,120.)


@pytest.mark.parametrize('step_minutes',[1.,15.])
def test_response_wait_remains15_physical_minutes_independent_of_step_count(step_minutes):
    core=QueueHarness(step_minutes=step_minutes,onset=60.25,repair=120.25)
    core.time=60.25;command=core.generate()
    assert command['generated_minute']==60.25 and command['arrival_minute']==75.25
    assert core.delayed_at(61.25)==(True,75.25)
    assert core.delayed_at(75.25)==(False,75.25)


def test_every_minute_responsive_command_waits_until75_then_latest_wins_once():
    core=QueueHarness()
    for minute in range(60,75):
        core.time=float(minute);command=core.generate()
        assert command['arrival_minute']==75.
        core.apply_boundary()
        assert core.active_command is None and core.executed['grid_import_kW']==0.
    assert len(core.pending_commands)==15
    core.time=75.;core.apply_boundary()
    assert core.pending_commands==[] and core.active_command['generated_minute']==74.
    assert core.executed['grid_import_kW']==1.
    assert sum(row['kind']=='command_arrived' for row in core.events)==15
    assert sum(row['kind']=='command_superseded' for row in core.events)==14
    assert sum(row['kind']=='command_executed' for row in core.events)==1
    core.apply_boundary()
    assert sum(row['kind']=='command_executed' for row in core.events)==1


def test_no_new_command_does_not_remove_an_old_pending_arrival():
    core=QueueHarness();core.time=60.;old=core.generate()
    before=deepcopy(core.pending_commands)
    for minute in (61.,70.,74.999):
        core.time=minute;core.apply_boundary()
        assert core.pending_commands==before and core.active_command is None
    core.time=75.;core.apply_boundary()
    assert core.active_command['id']==old['id'] and core.pending_commands==[]
    assert core.sequence==1


def test_a_future_drop_cannot_extend_the_current_release_until_it_is_observed():
    core=QueueHarness()
    # A clock-component adversarial fixture, not an admitted second-fault case.
    core.env.model_closure_events.insert(1,{'time_minute':70.,'availability':{'ABS':0.}})
    assert core.delayed_at(69.)==(True,75.)
    assert core.delayed_at(70.)==(True,85.)


def test_repeated_commands_at_one_clock_do_not_multiply_the_native_ramp_budget():
    core=QueueHarness();core.time=75.
    core.env.plant=load_plant(ROOT/'configs/plant_configs.json','doe_capacity_matched_exploratory')
    core.env.execution_contract={'gt_start_delay_minutes':0.}
    core.env.config={'gt_ramp_kW_per_hour':900.,'grid_import_limit_kW':1000.}
    core.env.scenario['grid_limit_kW']=1000.
    core.state.diesel_energy_remaining_kWh=100.
    core.ramp_time=None;core.ramp_anchor=None;core.ramp_budget=0.
    core.diesel_available=lambda *args:False
    core.data_at=lambda minute:(1,SimpleNamespace(outdoor_temperature_C=25.),np.ones((4,3))*100.,empty_faults())
    core.executed['gt_electric_kW']=[50.,50.,50.]
    request=deepcopy(core.executed);request['gt_electric_kW'][0]=100.
    core.executed=TransitionCore._clamp(core,request,empty_faults(),normal_ramp=True)
    assert core.executed['gt_electric_kW'][0]==65. and core.ramp_budget==15.
    core.executed=TransitionCore._clamp(core,request,empty_faults(),normal_ramp=True)
    assert core.executed['gt_electric_kW'][0]==65. and core.ramp_budget==15.
    core.time=76.
    core.executed=TransitionCore._clamp(core,request,empty_faults(),normal_ramp=True)
    assert core.executed['gt_electric_kW'][0]==80.


def energy_segments(step_minutes):
    rows=[]
    for minute in range(0,120,step_minutes):
        row=segment(float(minute),float(minute+step_minutes));dt=step_minutes/60.
        row['hour_of_year']=1+minute//60
        row['action'].update(grid_import_kW=60.,gt_fuel_MMBtu_h=2.,boiler_steam_lbph=100.)
        row['diesel_local_response'].update(mechanical_work_kWh=6.*dt,electrical_work_kWh=3.*dt)
        rows.append(row)
    return rows


@pytest.mark.parametrize('step_minutes',[1,15])
def test_raw_operating_fee_and_preparation_are_interval_integrals_not_command_fees(step_minutes):
    rows=energy_segments(step_minutes)
    payload=dict(segments=rows,summary={'requested_end_minute':120.},m21_initial_storage={'ups':10.,'cold_store':20.},
        accounting_parameters=deepcopy(PARAMETERS),audit_tariffs_by_hour={1:.1,2:.2})
    result=independent_energy_audit(payload)
    assert result['grid_purchase_kWh']==pytest.approx(120.)
    assert result['grid_fee_USD2001']==pytest.approx(18.)
    assert result['GT_gas_MMBtu']==pytest.approx(4.)
    assert result['DG_shaft_work_kWh']==pytest.approx(12.)
    assert result['observed_operating_fee_USD2001']==pytest.approx(42.85)
    assert result['initial_preparation_fee_USD2001']==pytest.approx(1.44)
    assert result['actual_fee_USD2001']==pytest.approx(44.29)


def test_minute_block_fees_charge_initial_storage_preparation_once():
    operating=[];preparation=[]
    for row in energy_segments(1):
        start=row['start_minute'];end=row['end_minute']
        block=_block_evaluation([row],start,end,preparation={'ups':10.,'cold_store':20.} if start==0. else {'ups':0.,'cold_store':0.},
            parameters=deepcopy(PARAMETERS),tariffs={1:.1,2:.2})
        operating.append(block['economics']['observed_operating_fee_USD2001'])
        preparation.append(block['economics']['initial_preparation_fee_USD2001'])
    assert sum(operating)==pytest.approx(42.85)
    assert preparation[0]==pytest.approx(1.44) and preparation[1:]==[0.]*119
    assert sum(operating)+sum(preparation)==pytest.approx(44.29)


def test_missing_actual_shaft_work_stays_unknown_without_free_diesel():
    rows=energy_segments(1);rows[7]['diesel_local_response'].pop('mechanical_work_kWh')
    result=independent_energy_audit(dict(segments=rows,summary={'requested_end_minute':120.},
        m21_initial_storage={'ups':0.,'cold_store':0.},accounting_parameters=deepcopy(PARAMETERS),audit_tariffs_by_hour={1:.1,2:.2}))
    assert result['DG_shaft_work_kWh'] is None and result['DG_shaft_fee_USD2001'] is None
    assert result['observed_operating_fee_USD2001'] is None and result['actual_fee_USD2001'] is None
