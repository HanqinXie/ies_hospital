"""One saved current-problem counterexample; no trajectory or annual loader."""
import copy
import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
OUT=ROOT/'outputs/v11_autonomous_dispatch_20261016_run01/validation/numerics'
from hospital_ies.model import Plant
from hospital_ies.resilience import RuntimeState
from hospital_ies.solver_diagnostics import LayerCollector,collect_layers


def problem():
    saved=json.loads((OUT/'current_problem.json').read_text(encoding='utf-8'))['current']
    p=copy.deepcopy(saved);p['row']=SimpleNamespace(**p['row']);p['row'].timestamp=pd.Timestamp(p['row'].timestamp)
    for key in ('electric_chillers_rt','levels'):p['plant'][key]=tuple(p['plant'][key])
    p['plant']=Plant(**p['plant'])
    for key in ('gt_prev','overload_minutes','disconnected_loads'):p['state'][key]=np.asarray(p['state'][key],float)
    p['state']['extra_faulted']=set(p['state']['extra_faulted']);p['state']=RuntimeState(**p['state'])
    p['loads']=np.asarray(p['loads'],float);return p


def api():
    try:return importlib.import_module('tools.autonomous_dispatch.numerics')
    except ModuleNotFoundError:return None


@pytest.mark.parametrize('route',['connections','margin'])
def test_saved_problem_meets_original_unit_gate_with_same_canonical_problem(route):
    module=api();p=problem()
    if module is None:
        function=importlib.import_module('tools.autonomous_dispatch.planner_'+route).solve_joint
    else:
        function=module._connections_solve_joint if route=='connections' else module.solve_joint
    collector=LayerCollector({'test_case':'broad_01_t0_saved_only','route':route})
    prefix='red' if module is None else 'green'
    try:
        with collect_layers(collector):result=function(**p)
    finally:collector.export(OUT/(prefix+'_'+route))
    assert all(layer['result']['status']==0 for layer in collector.layers)
    for layer in collector.layers:
        validation=layer['validation']
        assert validation['max_original_violation']<=1e-7
        assert validation['max_lock_violation']<=1e-10
        assert validation['max_integrality_distance']<=1e-9
        np.testing.assert_array_equal(layer['row_scales'],np.ones(len(layer['rows'])))
    assert result['lex_values'][:2]==pytest.approx([0.,0.],abs=1e-12)
    assert max(map(abs,result['energy_balance_residuals']))<=1e-7
    # Original coefficient arrays and original bounds are exact, not merely
    # similar physical outcomes. Old failing rank7 is preserved in red files.
    for layer in collector.layers[:8]:
        with np.load(OUT/('red_'+route)/f"layer_{layer['rank']:02}.npz") as old:
            np.testing.assert_array_equal(layer['matrix'].data,old['matrix_data'])
            np.testing.assert_array_equal(layer['matrix'].indices,old['matrix_indices'])
            np.testing.assert_array_equal(layer['matrix'].indptr,old['matrix_indptr'])
            for name in ('objective','integrality','lower','upper','bounds_lower','bounds_upper'):
                np.testing.assert_array_equal(layer[name],old[name])
        metadata=json.loads((OUT/('red_'+route)/f"layer_{layer['rank']:02}.json").read_text(encoding='utf-8'))
        assert layer['rows']==metadata['rows']
        for new,old in zip(layer['locks'],metadata['locks']):
            assert new['source_rank']==old['source_rank']
            assert new['upper']==old['upper'] and new['tolerance']==old['tolerance']


def test_active_margin_uses_original_units_and_does_not_mutate_frozen_modules():
    module=api();assert module is not None
    frozen=[importlib.import_module('tools.autonomous_dispatch.'+name) for name in
            ('planner','planner_connections','planner_margin')]
    before=[(m.solve_joint,m.solve_layer) for m in frozen]
    p=problem();p['joint_options']['critical_reserve_multiplier']=1.25
    collector=LayerCollector({'test_case':'same_saved_current_pressure_1_25'})
    with collect_layers(collector):result=module.solve_joint(**p)
    collector.export(OUT/'green_active_margin')
    assert [(m.solve_joint,m.solve_layer) for m in frozen]==before
    assert module.NUMERICAL_CONTRACT_ID=='unscaled_original_rows.v1'
    assert result['joint_diagnostic']['numerical_contract_id']==module.NUMERICAL_CONTRACT_ID
    assert all(np.all(layer['row_scales']==1.) for layer in collector.layers)
    assert result['lex_values'][:2]==pytest.approx([0.,0.],abs=1e-12)
    assert result['joint_diagnostic']['critical_reserve_multiplier']==1.25


def test_new_numerical_route_rejects_future_fields_and_false_retention_certificate():
    module=api();assert module is not None
    p=problem();p['joint_options']['future_load_minute']=30.
    with pytest.raises(ValueError):module.solve_joint(**p)
    p=problem();p['retention_eligible']=True
    with pytest.raises(ValueError):module.solve_joint(**p)
