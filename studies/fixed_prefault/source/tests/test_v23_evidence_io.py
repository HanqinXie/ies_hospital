"""A log write failure must not become a domain or reserve decision."""
import copy
from pathlib import Path
import sys
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'src'),str(ROOT/'tests')]
from tools.model_correctness_v23 import domains,evidence,release
from hospital_ies.execution import SolverExecutionError
from test_v23_recovery import make
from test_sci_environment import scenario

class FailFirst:
    def __init__(self):self.calls=0
    def store(self,rows):
        self.calls+=1
        if self.calls==1:raise OSError('injected evidence write failure')
        return {'schema':'test-only-reference','rows':copy.deepcopy(rows)}

def test_OFF_evidence_failure_aborts_without_running_alternative():
    calls=[];sink=FailFirst()
    def solver(**kwargs):
        domain=kwargs['config']['_m23_boiler_domain'];calls.append(domain)
        return dict(action=dict(boiler_station_running=domain=='run',boiler_steam_lbph=1. if domain=='run' else 0.),
            cost_USD_step=float(domain=='run'),_m23_objective_hierarchy=[dict(name='cost',value=float(domain=='run'),lock_tolerance=0.)],
            solver_backend_layers=[dict(status=0,attempts=[dict(status=0,accepted=True,validation={'rows':[{'name':'physical'}]})])])
    with evidence.evidence_scope(sink),pytest.raises(OSError,match='injected evidence'):
        domains.solve_execution({'config':{'_model_correctness_v23':{}}},solver)
    assert calls==['off'] and sink.calls==1

def test_native_mode_wrapper_marks_failure_and_commits_no_energy():
    e=make();e.reset(scenario=scenario(),seed=9);c=e.core;e.domain_evidence=FailFirst()
    _,_,loads,f=c.data_at(0.);before=(c.ups.energy_kWh,c.cold_store.energy_kWh,c.time,len(c.segments))
    with pytest.raises((SolverExecutionError,OSError)):
        c._solve(loads,f,e.dt_h,'injected_domain_IO',{},execution_action=c.executed)
    assert c.failed
    assert before==(c.ups.energy_kWh,c.cold_store.energy_kWh,c.time,len(c.segments))
    assert c.errors[-1]['error_type']=='OSError'

def test_instantaneous_witness_does_not_downgrade_evidence_IO_to_ready_false():
    e=make();e.reset(scenario=scenario(),seed=9);e.domain_evidence=FailFirst()
    with pytest.raises(OSError,match='injected evidence'):e.core.non_diesel_witness()

def test_sustained_witness_does_not_downgrade_evidence_IO_to_ready_false(monkeypatch):
    e=make();e.reset(scenario=scenario(),seed=9)
    def failed(*args,**kwargs):raise OSError('injected sustained evidence failure')
    monkeypatch.setattr(release,'solve_physical',failed)
    with pytest.raises(OSError,match='injected sustained'):e.core.non_diesel_witness()

def test_ordered_sidecar_IO_in_instantaneous_witness_is_not_suppressed():
    e=make();e.reset(scenario=scenario(),seed=9)
    class FailedLog(list):
        def append(self,value):
            if value.get('phase')=='m22_non_DG_release_witness' and value.get('status')=='success':raise OSError('injected ordered sidecar failure')
            super().append(value)
    e.core.solver_calls=FailedLog(e.core.solver_calls)
    with pytest.raises(OSError,match='injected ordered sidecar'):e.core.non_diesel_witness()
