"""Four actual v3 replay inputs remain required five-layer regressions.

These cases are true RED failures until one uniform numerical implementation
closes the original111 and these inputs. No xfail, skip, or expected exception
turns an unresolved production solver failure into an accepted trajectory.
"""
from pathlib import Path
import json
import pytest
from tests.test_p2c_solver import solve_core_case
from hospital_ies.solver_diagnostics import LayerCollector,collect_layers

CASES=json.loads((Path(__file__).parent/'fixtures/p2c_replay_new4_counterexamples.json').read_text(encoding='utf-8'))

@pytest.mark.parametrize('case',CASES,ids=lambda c:f'{c["scenario_id"]}-{c["policy_id"]}-105min')
def test_actual_new_replay_input_completes_all_five_layers(case):
    assert case['time_minute']==105.
    assert case['phase']=='fixed_execution'
    assert case['input_context']['execution_action']['gt_electric_kW']==[0.,225.,225.]
    collector=LayerCollector({'actual_replay_case':case['call_id']})
    with collect_layers(collector):result=solve_core_case(case)
    assert len(collector.layers)==5
    assert all(layer['result']['status']==0 for layer in collector.layers)
    assert all(layer['validation']['max_original_violation']<=1e-7 for layer in collector.layers)
    assert all(layer['validation']['max_lock_violation']<=1e-10 for layer in collector.layers)
    assert all(layer['validation']['max_integrality_distance']<=1e-9 for layer in collector.layers)
    assert len(result['lex_values'])==4
    assert result['optimizer_warning']==''
    assert max(map(abs,result['energy_balance_residuals']))<=1e-4
    assert abs(result['steam_balance_residual_lbph'])<=1e-4
