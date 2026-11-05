"""Pure current proofs retain the actual solver records they generated."""
import copy
import pickle
import time

from test_v22_core_environment import make, scenario


def test_current_probe_logs_keep_real_backend_layers_without_committing_state(monkeypatch):
    from hospital_ies.execution import ModelConstraintInfeasible
    from tools.model_closure_v22 import environment as module

    e=make(dict(sci_engineering=dict(
        ups=dict(capacity_kWh=40.,power_kW=160.,initial_soc=.5),
        cold_store=dict(capacity_kWh=100.,power_kW=100.,initial_soc=.5))))
    e.reset(scenario=scenario(),seed=29);c=e.core

    def actual_state():
        return pickle.dumps((c.time,c.state,c.station.observe(),c.ups.energy_kWh,
            c.cold_store.energy_kWh,c.cold_recharge.active,c.gt_clocks.__dict__,
            c.gt_start_pending,c._m22_gt_progress,c.arrived_dispatch,c.executed))

    actual_solve=module.solve_physical;returned_layers=[]
    def captured(*args,**kwargs):
        result=actual_solve(*args,**kwargs)
        returned_layers.append(copy.deepcopy(result['solver_backend_layers']))
        return result
    monkeypatch.setattr(module,'solve_physical',captured)
    before=actual_state();first=len(c.solver_calls);started=time.perf_counter()
    witness=c.non_diesel_witness();wall=time.perf_counter()-started
    assert witness['status']=='solved'
    assert actual_state()==before
    records=c.solver_calls[first:]
    assert [x['phase'] for x in records]==[
        'm22_current_port_selection','m22_non_DG_release_witness']
    assert len(returned_layers)==2
    for record,layers in zip(records,returned_layers):
        assert record['status']=='success'
        assert record['current_only'] and record['inventory_committed'] is False
        assert 0.<=record['elapsed_seconds']<=wall
        assert record['solver_backend_layers']==layers
        assert layers and all(x['backend']!='injected' and x['status']==0 for x in layers)
        assert all(x['attempts'] for x in layers)

    # This registered exception fixture tests metadata forwarding only; the
    # successful backend records above came from two actual physical solves.
    attempts=[dict(backend='registered_exception_fixture',status=2,
        layer='fixture_only',attempts=[],fixture_only=True)]
    def rejected(*args,**kwargs):
        exc=ModelConstraintInfeasible('registered rejected held-mode candidate')
        exc.solver_backend_attempts=copy.deepcopy(attempts)
        raise exc
    monkeypatch.setattr(module,'solve_physical',rejected)
    c._m22_selection_key=None
    _,_,loads,f=c.data_at(c.time);c._select_current_ports(loads,f)
    rejected_record=c.solver_calls[-1]
    assert rejected_record['status']=='candidate_infeasible'
    assert rejected_record['solver_backend_layers']==attempts
    assert rejected_record['current_only'] and rejected_record['inventory_committed'] is False
    assert rejected_record['elapsed_seconds']>=0.
    assert actual_state()==before
