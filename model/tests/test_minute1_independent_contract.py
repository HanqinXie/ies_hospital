"""Independent interface regressions; no solver or physical trajectory runs."""
from types import SimpleNamespace

import numpy as np
import pandas as pd

from hospital_ies.state_transition import TransitionCore,next_input_boundary
from tools.operating_response.execution import LocalResponseMixin


def test_constructor_connection_and_cooling_basis_use_same_minute_zero_as_reset():
    old_groups={f'P{g}_{k}_kW':1. for g in range(4) for k in 'ehc'}
    minute_groups={f'P{g}_{k}_kW':float(10*g+j+2) for g in range(4) for j,k in enumerate('ehc')}
    calls=[]
    class Source:
        def at_elapsed(self,elapsed,start_hour):
            calls.append((elapsed,start_hour))
            return 33,SimpleNamespace(minute_of_year=1921),minute_groups.copy()
    class ConstructionOnlyCore(TransitionCore):
        # Isolate constructor bookkeeping; no dispatch or energy is committed.
        def _commit_sources(self,*args,**kwargs):pass
        def aux(self,action):return 0.
    env=SimpleNamespace(scenario={'start_hour':33},minute_source=Source(),
        groups=pd.DataFrame([old_groups],index=[33]),state=SimpleNamespace())
    core=ConstructionOnlyCore(env,{})
    expected=np.array([[minute_groups[f'P{g}_{k}_kW'] for k in 'ehc'] for g in range(4)])
    np.testing.assert_array_equal(core.connection_basis_loads,expected)
    assert env.state.cooling_reference_kW==float(expected[:,2].sum())
    assert calls==[(0.,33)]


def test_actual_local_response_boundary_does_not_cross_next_minute_input():
    # This checks the bounded fork used by the real MRO, not only its base.
    core=LocalResponseMixin.__new__(LocalResponseMixin)
    core.env=SimpleNamespace(scenario={'step_minutes':1.,'duration_steps':180},
        minute_source=object(),execution_contract={},hrsg_thermal_config={})
    core.time=.25;core.pending_commands=[];core.gt_start_pending={}
    core.pending_shed=core.local_down=core.grid_outage_start=None
    core._test_state=SimpleNamespace(diesel_start_minute=None)
    # LocalResponseMixin reads self.state; a detached subclass supplies it.
    class BoundaryOnly(LocalResponseMixin):
        @property
        def state(self):return self._test_state
    core.__class__=BoundaryOnly
    assert core._next_boundary(2.)<=1.


def test_input_clock_does_not_skip_an_unreached_minute_within_epsilon():
    now=1.-.5e-9
    assert np.floor(now)==0.
    assert next_input_boundary(SimpleNamespace(minute_source=object()),now)==1.


def test_minute_target_remains_fan_independent_above_old_delivered_reference(tmp_path):
    from test_minute1_source import fixture_files
    from tools.minute1_20261103.source import MinuteSource
    from tools.model_review_v28.demand import bind_request
    from tools.sci_engineering.service import fan_independent_heat_bound
    spec,_,_=fixture_files(tmp_path,count=2)
    source=MinuteSource(tmp_path,spec)
    _,row,groups=source.at_elapsed(0.,1)
    loads=np.array([[groups[f'P{g}_{k}_kW'] for k in 'ehc'] for g in range(4)])
    row,loads=bind_request(row,loads)
    assert row.Q_h_DHW_water_use_kW==2. and row.P3_reference_h_kW==1.
    bound=fan_independent_heat_bound(row,loads)
    assert bound['maximum_fan_independent_heat_kW'][3]==2.
    assert bound['source_trace']['contract_id']=='hospital_ies.minute1.ordinary_wateruse_trace.v1'


def test_actual_SCI_heat_ledger_names_the_same_bound_minute_component_file(tmp_path,monkeypatch):
    from test_minute1_source import fixture_files
    from tools.minute1_20261103.source import MinuteSource
    from tools.sci_engineering import environment as sci
    spec,_,_=fixture_files(tmp_path,count=2)
    source=MinuteSource(tmp_path,spec);_,row,groups=source.at_elapsed(0.,1)
    loads=np.array([[groups[f'P{g}_{k}_kW'] for k in 'ehc'] for g in range(4)])
    monkeypatch.setattr(sci.LoadAccountingCore,'_hardware_data_at',lambda self,minute:(1,row,loads.copy(),{}))
    # The separate real helper regression above tests fan-independent delivery;
    # this unit isolates the provenance annotation preceding any physical solve.
    monkeypatch.setattr(sci,'fan_independent_heat_bound',lambda row,loads:{})
    core=sci.EngineeringCore.__new__(sci.EngineeringCore)
    core.env=SimpleNamespace(scenario={'step_minutes':1.,'duration_steps':180,
        'shock_start_minute':60.,'shock_duration_minutes':0.,
        'fault_start_minute':60.,'fault_duration_minutes':0.,'auxiliary_faults':{}},
        auxiliary_metadata={'fan_component_columns':{},
            'root_input_sha256':{'inputs/loads/hospital_ies_demand_8760.csv':'a'*64}})
    _,annotated,_,_=core._hardware_data_at(0.)
    ledger=annotated.sci_heat_decomposition_source
    path=str((tmp_path/'thermal_components.csv').resolve())
    assert ledger['path']==path
    assert ledger['sha256']==spec['files']['thermal_components']['sha256']
    assert ledger['minute_of_year']==1
    assert ledger['legacy_delivery_source_path']==path
    assert ledger['legacy_delivery_source_sha256']==spec['files']['thermal_components']['sha256']
