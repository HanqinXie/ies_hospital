"""Real annual reset uses the V27 typed encoder before any trajectory step."""
import pytest

from tools.annual_dynamic_20261104.bridge import CoolingBridgeState
from tools.model_review_v27.objective_snapshot import _encode, _decode


def test_annual_cooling_state_roundtrips_in_reset_snapshot():
    state = CoolingBridgeState(initial_cooling_kW=[10., 20., 30., 40.],
                               initial_availability={})
    restored = _decode(_encode(state))
    assert type(restored) is CoolingBridgeState
    assert restored.to_state() == state.to_state()
    restored.dynamic._q[0] = 99.
    assert state.dynamic._q[0] == 10.


def test_unregistered_snapshot_mutable_type_still_rejected():
    class Unknown:
        pass
    with pytest.raises(TypeError, match='Unregistered snapshot mutable class'):
        _encode(Unknown())
