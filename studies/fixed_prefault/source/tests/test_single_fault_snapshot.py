"""Explicit serialization migration without a general subclass permission."""
import hashlib
import json

import pytest

from tools.model_review_v27.objective_snapshot import _decode, _encode
from tools.sci_engineering.storage import FiniteStore
from tools.single_fault_model_20261102.storage import ContinuousLossStore

CONFIG = dict(capacity_kWh=100., power_kW=10., initial_soc=.5,
              charge_efficiency=.9, discharge_efficiency=.8, loss_per_hour=.01)


def test_named_new_storage_roundtrip_preserves_current_stock_and_preparation_cost():
    store = ContinuousLossStore(CONFIG)
    store.energy_kWh = 17.
    encoded = _encode(store)
    assert encoded['class'] == 'tools.single_fault_model_20261102.storage.ContinuousLossStore'
    restored = _decode(encoded)
    assert type(restored) is ContinuousLossStore
    assert vars(restored) == vars(store)
    assert restored.project_energy(discharge_kW=0., charge_kW=1., dt_h=.1) == store.project_energy(
        discharge_kW=0., charge_kW=1., dt_h=.1)


@pytest.mark.parametrize('parent', [FiniteStore, ContinuousLossStore])
def test_unknown_storage_subclasses_remain_rejected(parent):
    class UnknownStore(parent): pass
    with pytest.raises(TypeError, match='Unregistered snapshot'):
        _encode(UnknownStore(CONFIG))
    with pytest.raises(ValueError, match='Unregistered.*snapshot'):
        _decode(dict(type='object', **{'class':__name__+'.UnknownStore'}, value=_encode({})))


def test_original_finite_store_snapshot_bytes_remain_pinned():
    value = json.dumps(_encode(FiniteStore(CONFIG)), sort_keys=True, separators=(',', ':'))
    assert hashlib.sha256(value.encode()).hexdigest() == 'b677fabc5ed07e2709e7477983b0d634141b8d06bb44339cc39fe693a3430708'
