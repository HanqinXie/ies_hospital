from pathlib import Path

import pytest

from tools.model_closure_v22.recovery_validation import run


def test_three_real_balances_only_actual_extra_cold_reduces_thermal_energy(tmp_path):
    root = Path(__file__).resolve().parents[1]
    result = run(root, tmp_path/'recovery')
    rows = result['intervals']
    assert result['actual_IES_episode_attempts'] == 0
    assert result['actual_terminal_balance_solves'] == 3
    assert [r['actual_extra_cooling_kWh'] for r in rows[:2]] == pytest.approx([0., 0.], abs=1e-5)
    assert rows[0]['delta_after_C'] > 3.
    assert rows[1]['delta_after_C'] > 3.
    assert rows[2]['actual_extra_cooling_kWh'] > 0.
    assert rows[2]['actual_fan_kWh'] > 0.
    assert rows[2]['actual_pump_kWh'] > 0.
    assert rows[2]['delta_after_C'] == pytest.approx(0., abs=1e-5)
    assert max(abs(r['thermal_balance_residual_kWh']) for r in rows) < 1e-10
    assert max(r['maximum_source_balance_residual'] for r in rows) < 1e-4
    assert result['sources_unchanged'] is True
