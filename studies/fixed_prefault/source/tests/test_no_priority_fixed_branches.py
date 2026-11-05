"""Frozen pre-fault branch allocations; real arithmetic and optimizer guards."""
from copy import deepcopy
from types import SimpleNamespace
import numpy as np
import pytest
from test_noopt_forward_arithmetic import payload


def frozen_payload():
    from tools.noopt_unattended_20261104.fixed_branches import freeze_branches
    p = payload()
    p['reference']['_fixed_branches'] = freeze_branches(
        p['plant'], p['loads'], p['reference'])
    return p


def test_fixed_allocator_caps_unused_share_without_redistribution():
    from tools.noopt_unattended_20261104.fixed_branches import allocate_fixed
    served, unused = allocate_fixed([0., 100., 100., 100.], 100., [.4, .2, .2, .2])
    assert served == pytest.approx([0., 20., 20., 20.])
    assert unused == pytest.approx(40.)


def test_electric_shortage_has_no_critical_first_or_critical_station_reserve():
    from tools.noopt_unattended_20261104.forward import evaluate_forward
    p = frozen_payload()
    p['execution_action']['grid_import_kW'] = 15.
    result = evaluate_forward(**p)
    actual = result['served'][:, 0]
    assert np.all(actual > 0.)
    assert actual / p['loads'][:, 0] == pytest.approx(np.full(4, actual[0] / 20.))
    assert sum(result['action']['electric_chiller_cooling_kW']) > 0.
    assert max(map(abs, result['energy_balance_residuals'])) < 1e-4


def test_cold_shortage_uses_frozen_shares_and_not_current_demand_weights():
    from tools.noopt_unattended_20261104.forward import evaluate_forward
    p = frozen_payload()
    p['reference']['electric_chiller_cooling_kW'][0] = 62.5
    p['loads'][3, 2] = 100.
    result = evaluate_forward(**p)
    assert result['served'][:, 2] == pytest.approx([25., 12.5, 12.5, 12.5], abs=1e-6)
    assert result['allocation']['method'] == 'frozen_prefault_branch_shares_no_redistribution'


def test_demand_drop_does_not_transfer_cold_share_or_dump_cold():
    from tools.noopt_unattended_20261104.forward import evaluate_forward
    p = frozen_payload()
    p['loads'][0, 2] = 0.
    result = evaluate_forward(**p)
    assert result['served'][:, 2] == pytest.approx([0., 25., 25., 25.], abs=1e-6)
    assert sum(result['action']['electric_chiller_cooling_kW']) == pytest.approx(75., abs=1e-6)
    assert max(map(abs, result['energy_balance_residuals'])) < 1e-4


def test_missing_prefault_reference_is_refused_instead_of_reweighting():
    from tools.noopt_unattended_20261104.forward import evaluate_forward
    p=payload();p['reference'].pop('_fixed_branches')
    with pytest.raises(ValueError, match='frozen.*branch'):
        evaluate_forward(**p)


def test_common_bus_allocation_is_invariant_to_priority_label_permutation():
    from tools.noopt_unattended_20261104.forward import evaluate_forward
    from tools.noopt_unattended_20261104.fixed_branches import freeze_branches
    p = frozen_payload()
    p['execution_action']['grid_import_kW'] = 15.
    first = evaluate_forward(**p)
    perm = [3, 1, 0, 2]
    q = deepcopy(p)
    q['loads'] = q['loads'][perm]
    q['reference']['_fixed_branches'] = freeze_branches(q['plant'], q['loads'], q['reference'])
    second = evaluate_forward(**q)
    assert second['served'] == pytest.approx(first['served'][perm], abs=1e-6)


@pytest.mark.parametrize('minute', [0., 58., 60., 120.])
def test_all_u_optimizers_are_blocked_from_checkpoint_activation(minute):
    from tools.noopt_unattended_20261104.runtime import forbid_optimization, ForbiddenDispatchOptimization
    from hospital_ies.model import solve_hour
    from scipy.optimize import minimize
    for fn, args in [(solve_hour, (None, None)), (minimize, (lambda x: x[0] ** 2, [1.]))]:
        core = SimpleNamespace(time=minute, env=SimpleNamespace(minute1_variant='unattended',
            dt_minutes=1.), _noopt_optimizer_attempts=[])
        with pytest.raises(ForbiddenDispatchOptimization):
            with forbid_optimization(core):
                fn(*args)
        assert len(core._noopt_optimizer_attempts) == 1


def test_fork_requires_a_real_complete_prefault_checkpoint():
    from tools.noopt_unattended_20261104.environment import fork_fixed_unattended
    with pytest.raises(ValueError, match='checkpoint'):
        fork_fixed_unattended(SimpleNamespace())


class CheckpointCore(SimpleNamespace):
    from hospital_ies.state_transition import TransitionCore
    log=TransitionCore.log


class CheckpointEnvironment:
    def native_step(self,action):
        self.core.time+=1.
        return {},0.,False,False,{}


def test_fork_rebinds_captured_methods_and_keeps_common_checkpoint_untouched():
    from types import MethodType
    from tools.noopt_unattended_20261104.environment import fork_fixed_unattended
    p=payload();common=CheckpointEnvironment()
    common.minute1_variant='responsive';common.plant=p['plant']
    common._fixed_branch_boundary_deferred_at=60.
    common.core=CheckpointCore(env=common,time=60.,events=[],cooling_dynamics=object(),
        _last_running_actual=p['reference'],_last_steam_abs_fraction=0.,
        segments=[dict(start_minute=59.,end_minute=60.,loads_kW=p['loads'].tolist(),
            unserved_kW=np.zeros((4,3)).tolist(),action=p['reference'])])
    captured=common.native_step
    def wrapped(self,action):return captured(action)
    common.step=MethodType(wrapped,common)
    fork=fork_fixed_unattended(common)
    assert fork.core.env is fork
    with pytest.raises(ValueError,match='reset'):
        fork.reset(scenario={})
    fork.step(None)
    assert fork.core.time==61.
    assert common.core.time==60.
    assert common.core.events==[]


def test_late_old_command_cannot_change_frozen_steam_branch_share():
    from tools.noopt_unattended_20261104.runtime import ArithmeticCoreMixin
    class Native:
        def boundary(self):pass
    class Core(ArithmeticCoreMixin,Native):
        def log(self,*args,**kwargs):pass
    core=object.__new__(Core)
    core.env=SimpleNamespace(minute1_variant='unattended',plant=payload()['plant'])
    core.time=61.;core._noopt_reference=dict(_steam_abs_fraction=.2)
    core.pending_commands=[dict(arrival_minute=61.,generated_minute=59.,id=1,
        dispatch=dict(absorption_cooling_kW=50.,hrsg_steam_lbph=100.,boiler_steam_lbph=500.))]
    core.boundary()
    assert core._noopt_reference['_steam_abs_fraction']==.2


@pytest.mark.parametrize('variant,expected',[('unattended',0),('responsive',1)])
def test_u_cannot_create_priority_automatic_shedding(variant,expected):
    from tools.noopt_unattended_20261104.runtime import ArithmeticCoreMixin
    class Native:
        def _automatic_shed(self,*args):self.created+=1
    class Core(ArithmeticCoreMixin,Native):
        def log(self,*args,**kwargs):pass
    core=object.__new__(Core);core.created=0;core.pending_shed=None
    core.env=SimpleNamespace(minute1_variant=variant,scenario={'automatic_fast_shedding':True})
    core._automatic_shed(np.ones((4,3)))
    assert core.created==expected


def test_forbidden_optimizer_is_fatal_and_cannot_trigger_exception_fallback():
    from tools.noopt_unattended_20261104.runtime import forbid_optimization,ForbiddenDispatchOptimization
    from scipy.optimize import linprog
    core=SimpleNamespace(time=60.,env=SimpleNamespace(minute1_variant='unattended'),
        _noopt_optimizer_attempts=[])
    with pytest.raises(ForbiddenDispatchOptimization):
        with forbid_optimization(core):
            try:
                linprog([1.])
            except Exception:
                pass  # Ordinary physical error handling must not swallow the guard.
    assert core.failed
    with pytest.raises(ForbiddenDispatchOptimization):
        with forbid_optimization(core):
            pytest.fail('A failed U was allowed to continue')


def test_heat_uses_frozen_branch_shares_under_source_derating():
    from tools.noopt_unattended_20261104.forward import evaluate_forward
    from tools.noopt_unattended_20261104.fixed_branches import freeze_branches
    p=payload();p['loads'][:,1]=[40.,30.,20.,10.];p['loads'][:,2]=0.
    factor=p['plant'].steam_kWh_per_lb*p['plant'].exchanger_eff
    p['reference']['electric_chiller_cooling_kW']=[0.,0.,0.]
    p['reference']['boiler_steam_lbph']=100./factor
    p['reference']['station_auxiliary_kW']=.5+.012*p['plant'].steam_kWh_per_lb*100./factor
    p['reference']['_fixed_branches']=freeze_branches(p['plant'],p['loads'],p['reference'])
    p['execution_action']['boiler_steam_lbph']=50./factor
    r=evaluate_forward(**p)
    assert r['served'][:,1]==pytest.approx([20.,15.,10.,5.],abs=1e-5)
    assert max(map(abs,r['energy_balance_residuals']))<1e-4


def test_dedicated_cold_backup_keeps_its_original_connections_without_ordering():
    from tools.noopt_unattended_20261104.forward import evaluate_forward
    p=frozen_payload();p['availability']['EC_1']=0.;p['dt_h']=1./120.
    p['stores']['cold']=dict(energy_kWh=1.,capacity_kWh=1.,power_kW=100.)
    r=evaluate_forward(**p)
    assert r['served'][:,2]==pytest.approx([50.,25.,0.,0.],abs=1e-6)
    assert r['action']['cold_discharge_kW']==pytest.approx(75.)
    assert r['action']['cold_storage_pump_kW']==pytest.approx(1.5)
    assert max(map(abs,r['energy_balance_residuals']))<1e-4


def test_native_gurobi_entry_is_blocked_before_constructor_body():
    from tools.noopt_unattended_20261104.runtime import forbid_optimization,ForbiddenDispatchOptimization
    import gurobipy
    core=SimpleNamespace(time=60.,env=SimpleNamespace(minute1_variant='unattended'),_noopt_optimizer_attempts=[])
    with pytest.raises(ForbiddenDispatchOptimization):
        with forbid_optimization(core):
            gurobipy.Model('forbidden_u_entry_probe')
