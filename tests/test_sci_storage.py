from pathlib import Path
import sys
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
try:
    from tools.sci_engineering.storage import FiniteStore
except ImportError:
    FiniteStore=None

def store(**extra):
    assert FiniteStore is not None, 'Finite storage and actual energy debit are required'
    return FiniteStore(dict(capacity_kWh=10.,power_kW=20.,initial_soc=.5,
        charge_efficiency=.9,discharge_efficiency=.8,loss_per_hour=0.,**extra))

def test_storage_debits_real_delivered_energy_and_recharges_with_loss():
    s=store();r=s.commit(discharge_kW=8.,charge_kW=0.,dt_h=.5)
    assert s.energy_kWh==pytest.approx(0.)
    assert r['discharged_kWh']==pytest.approx(4.)
    assert r['conversion_loss_kWh']==pytest.approx(1.)
    r=s.commit(discharge_kW=0.,charge_kW=10.,dt_h=1.)
    assert s.energy_kWh==pytest.approx(9.)
    assert r['charged_input_kWh']==pytest.approx(10.)

def test_exhausted_storage_does_not_supply_or_refill_on_restore():
    s=store();s.commit(discharge_kW=8.,charge_kW=0.,dt_h=.5)
    assert s.limits(.25,charge_allowed=False)['discharge_kW']==0.
    s.commit(discharge_kW=0.,charge_kW=0.,dt_h=1.)
    assert s.energy_kWh==0.

@pytest.mark.parametrize('d,c,dt',[(20.,0.,1.),(1.,1.,.1),(0.,30.,.1),(0.,20.,1.)])
def test_storage_rejects_overdraft_simultaneous_ports_power_and_overfill(d,c,dt):
    s=store()
    with pytest.raises(ValueError):s.commit(discharge_kW=d,charge_kW=c,dt_h=dt)
