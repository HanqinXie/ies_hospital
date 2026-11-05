"""Source-isolated held branches: reproduce the actual T02 mode-switch defect."""
from copy import deepcopy
from types import SimpleNamespace,MethodType
import numpy as np
import pytest
from test_noopt_forward_arithmetic import payload
from tools.noopt_unattended_20261104.fixed_branches import freeze_branches, allocate_fixed


def t02_checkpoint():
    # Actual common 59-60 minute interval, run03 T02. No model run in this test.
    served=np.zeros((4,3))
    served[:,0]=[20.,10.,10.,10.]
    served[:,2]=[21.04411634622998,18.88224197276902,14.125247800436016,3.2206194623654234]
    action=deepcopy(payload()['reference'])
    action.update(absorption_cooling_kW=0.,electric_chiller_cooling_kW=[17.34586726280143,0.,0.],
        cold_discharge_kW=39.926358318999,cold_charge_kW=0.,cold_storage_pump_kW=.79852716637998)
    return served,action


def test_t02_unchanged_ordinary_source_keeps_original_branches():
    served,action=t02_checkpoint()
    frozen=freeze_branches(payload()['plant'],served,action)
    ordinary,_=allocate_fixed(served[:,2],17.34586726280143,frozen['cold_port_shares'][:4])
    np.testing.assert_allclose(ordinary,[0.,0.,*served[2:,2]],rtol=0.,atol=1e-9)
    assert frozen['source_partition']['cold']['dedicated_delivery_kW']==pytest.approx([*served[:2,2],0.,0.])


def test_ordinary_electric_source_excludes_dedicated_delivery():
    p=payload();served=p['loads'].copy();served[:,0]=[8.,2.,20.,10.]
    action=dict(p['reference'],ups_discharge_kW=10.)
    frozen=freeze_branches(p['plant'],served,action)
    assert frozen['electric_port_shares'][:2]==[0.,0.]
    assert frozen['source_partition']['electric']['ordinary_delivery_kW']==[0.,0.,20.,10.]


def test_ambiguous_active_dedicated_source_requires_explicit_flows():
    p=payload();action=dict(p['reference'],cold_discharge_kW=10.)
    with pytest.raises(ValueError,match='[Aa]mbiguous'):
        freeze_branches(p['plant'],p['loads'],action)


def test_explicit_source_flows_close_without_changing_weights():
    p=payload();action=dict(p['reference'],cold_discharge_kW=10.)
    frozen=freeze_branches(p['plant'],p['loads'],action,source_flows={'cold':[8.,2.,0.,0.]})
    np.testing.assert_allclose(frozen['source_partition']['cold']['ordinary_delivery_kW'],[42.,23.,25.,25.])
    np.testing.assert_allclose(frozen['dedicated_cold_shares'],[.8,.2,0.,0.])
    with pytest.raises(ValueError,match='source|Source'):
        freeze_branches(p['plant'],p['loads'],action,source_flows={'cold':[7.,2.,1.,0.]})


def test_inactive_backup_keeps_registered_standby_connections():
    p=payload();frozen=freeze_branches(p['plant'],p['loads'],p['reference'])
    np.testing.assert_allclose(frozen['dedicated_cold_shares'],[2/3,1/3,0.,0.])
    assert frozen['source_partition']['cold']['basis']=='inactive_dedicated_source'
    assert frozen['source_partition']['cold']['backup_weights_basis']=='existing_connected_prefault_service_proxy'
    with pytest.raises(ValueError,match='Zero source'):
        freeze_branches(p['plant'],p['loads'],p['reference'],source_flows={'ups':[1e-15,0.,0.,0.]})


def test_fork_preserves_existing_boiler_restart_target_without_new_command():
    from test_no_priority_fixed_branches import CheckpointEnvironment,CheckpointCore
    from tools.noopt_unattended_20261104.environment import fork_fixed_unattended
    p=payload();common=CheckpointEnvironment()
    common.minute1_variant='responsive';common.plant=p['plant'];common._fixed_branch_boundary_deferred_at=60.
    common.core=CheckpointCore(env=common,time=60.,events=[],cooling_dynamics=object(),
        station=SimpleNamespace(boiler_ready=False),arrived_dispatch={'boiler_steam_lbph':750.},
        _last_running_actual=dict(p['reference'],boiler_steam_lbph=0.),_last_steam_abs_fraction=0.,
        segments=[dict(start_minute=59.,end_minute=60.,loads_kW=p['loads'].tolist(),
            unserved_kW=np.zeros((4,3)).tolist(),action=p['reference'])])
    common.step=MethodType(lambda self,action:self.native_step(action),common)
    u=fork_fixed_unattended(common)
    assert u.core._noopt_reference['boiler_steam_lbph']==750.
    assert u.core.station.boiler_ready is False
    assert common.core._last_running_actual['boiler_steam_lbph']==0.


def test_permission_only_old_command_does_not_invent_running_target():
    from tools.noopt_unattended_20261104.runtime import ArithmeticCoreMixin
    class Native:
        def boundary(self):pass
    class Core(ArithmeticCoreMixin,Native):
        def log(self,*args,**kwargs):pass
    core=object.__new__(Core);core.env=SimpleNamespace(minute1_variant='unattended')
    core.time=61.;core._noopt_reference={'boiler_steam_lbph':100.,'_steam_abs_fraction':.2}
    core.pending_commands=[dict(arrival_minute=61.,generated_minute=59.,id=1,
        dispatch=dict(boiler_steam_lbph=1000.,electric_chiller_cooling_kW=[1000.,1000.,1000.]))]
    core.boundary()
    assert core._noopt_reference=={'boiler_steam_lbph':100.,'_steam_abs_fraction':.2}


def test_grid_balancing_rejects_an_unregistered_fixed_import_contract():
    from tools.noopt_unattended_20261104.forward import evaluate_forward,ForwardBalanceError
    p=payload();p['execution_contract']['grid_import_mode']='fixed'
    with pytest.raises(ForwardBalanceError,match='arrived_ceiling'):
        evaluate_forward(**p)


def test_small_source_closure_error_cannot_create_an_ordinary_branch():
    p=payload();served=np.zeros((4,3));served[0,0]=10.
    action=dict(p['reference'],ups_discharge_kW=10.,electric_chiller_cooling_kW=[0.,0.,0.])
    with pytest.raises(ValueError,match='source'):
        freeze_branches(p['plant'],served,action,source_flows={'ups':[10.-5e-8,0.,0.,0.]})
    served[0,0]=200.
    action.update(ups_discharge_kW=0.,diesel_kW=200.)
    frozen=freeze_branches(p['plant'],served,action,source_flows={'diesel':[200.-1.2e-12,0.,0.,0.]})
    assert frozen['electric_port_shares']==[0.]*6
    assert frozen['source_partition']['electric']['ordinary_roundoff_removed_kW'][0]>0.


def test_simultaneous_dedicated_sources_need_separate_flow_evidence():
    p=payload();served=p['loads'].copy()
    action=dict(p['reference'],ups_discharge_kW=4.,diesel_kW=6.)
    with pytest.raises(ValueError,match='Ambiguous'):
        freeze_branches(p['plant'],served,action)
    frozen=freeze_branches(p['plant'],served,action,
        source_flows={'ups':[4.,0.,0.,0.],'diesel':[0.,6.,0.,0.]})
    assert frozen['source_partition']['electric']['ordinary_delivery_kW']==[16.,4.,10.,10.]
    assert frozen['dedicated_ups_shares']==[1.,0.,0.,0.]
    assert frozen['dedicated_diesel_shares']==[0.,1.,0.,0.]
    served[:,0]=[10.,10.,0.,0.]
    action.update(ups_discharge_kW=10.,diesel_kW=5.)
    with pytest.raises(ValueError,match='overlap'):
        freeze_branches(p['plant'],served,action,
            source_flows={'ups':[10.,0.,0.,0.],'diesel':[5e-8,5.-5e-8,0.,0.]})
