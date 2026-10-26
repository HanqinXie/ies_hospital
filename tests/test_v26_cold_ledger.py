import importlib

import numpy as np
import pytest


def module():
    try:
        return importlib.import_module('tools.model_correction_v26.cold_ledger')
    except ModuleNotFoundError:
        pytest.fail('V26 complete signed water ledger is not implemented')


def test_coils_pump_source_and_inventory_are_distinct_signed_terms():
    # Finite water warming supplies coil cooling; pump adds load, not cooling.
    result=module().water_residual([1050.,-450.],[1000.,0.],[50.,50.],[0.,500.],[0.,0.])
    np.testing.assert_array_equal(result,[0.,0.])
    # Omitting a real attached coil must leave its heat in the residual.
    result=module().water_residual([1050.],[0.],[50.],[0.],[0.])
    assert result[0]==1000.


def test_unknown_rate_cannot_silently_close_a_water_ledger():
    with pytest.raises(ValueError):
        module().water_residual([0.],[np.nan],[0.],[0.],[0.])


def test_native_key_resolver_fails_missing_or_ambiguous_contract_instead_of_zero():
    m=module()
    contract={'a':dict(key='HX',variable='Heat',unit='W')}
    assert m.channel_label(contract,'hx','Heat')=='a'
    with pytest.raises(ValueError):m.channel_label(contract,'HX','Not measured')


def test_actual_loop_mass_uses_exact_505C_source_conversion_not_nominal_5C():
    # Exact SQL volume; exact26.2 water-density table is linear999.9→999.7
    # from5→10C. Functional API at5.05C independently gives999.898kg/m3.
    volume=5.179641956509636
    density=lambda temperature:999.9+(temperature-5.)*(999.7-999.9)/5.
    assert module().loop_mass_from_volume(volume,density)==pytest.approx(5179.113633030072,abs=1e-10)
    assert module().loop_mass_from_volume(volume,density)!=pytest.approx(volume*density(5.),abs=1e-5)


def test_functional_api_late_initialization_and_teardown_stay_in_output_directory(tmp_path,monkeypatch):
    # Functional API may open default files on specific_heat, after density.
    # A startup-only chdir therefore fails to contain the real late side effect.
    from pathlib import Path
    caller=tmp_path/'caller';output=tmp_path/'output'
    caller.mkdir();output.mkdir();monkeypatch.chdir(caller)
    def mark(stage):
        with Path('functional_bootstrap.txt').open('a') as f:f.write(stage+'\n')
    class Water:
        def specific_heat(self,state,temperature):mark('late_specific_heat');return 4200.
    class StateManager:
        def new_state(self):mark('new_state');return object()
        def delete_state(self,state):mark('delete_state')
    class Functional:
        def glycol(self,state,name):mark('glycol');return Water()
    class API:
        state_manager=StateManager()
        functional=Functional()
    with pytest.raises(RuntimeError,match='after native call'):
        with module().functional_api_session(None,output,api_factory=API) as (_,state,water):
            assert water.specific_heat(state,7.)==4200.
            raise RuntimeError('after native call')
    assert Path.cwd()==caller
    assert not (caller/'functional_bootstrap.txt').exists()
    assert (output/'functional_bootstrap.txt').read_text().splitlines()==['new_state','glycol','late_specific_heat','delete_state']
