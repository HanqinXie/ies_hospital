"""Real local constructor/reset acceptance; no step or formal trajectory."""
from copy import deepcopy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('sf1', [False, True])
def test_actual_local_factory_reset_preserves_binding_and_selects_only_explicit_storage(sf1):
    from tools.annual_readiness.environment import with_readiness
    from tools.annual_readiness.registration import load_registry, make_case, worker_registry
    from tools.annual_readiness.runner import _bindings
    from tools.eight_fault_study.runtime import (create_source_context, runtime_configuration,
        with_local_runtime, resolve_local_source_freeze)
    from tools.model_correction_v25.environment import MODEL_ID as CONSTRUCTOR_ID
    from tools.sci_engineering.design import SEALED_DAYS
    from tools.sci_engineering.storage import FiniteStore
    from tools.single_fault_model_20261102.environment import with_single_fault_model, decision_snapshot
    from tools.single_fault_model_20261102.runtime import MODEL_ID, ModelSpec
    from tools.single_fault_model_20261102.storage import ContinuousLossStore
    from tools.model_review_v27 import objective_snapshot

    registry = load_registry()
    case = make_case(registry, 'b07_L4_GT_ER_disturbed_M', 'M_available', mode='N',
        start_hour=33, configuration='H2')
    configuration = runtime_configuration(case, worker_registry(case, registry))
    original_configuration = deepcopy(configuration)
    frozen = _bindings()
    context = create_source_context(ROOT, case=case, configuration=configuration, bindings=frozen)
    options = dict(requalification_minutes=None, thermal_profile=case['v28_thermal_profile'])
    original = (with_single_fault_model('M_available', model_spec=ModelSpec(**{
        'physical_max_segment_minutes':.5}), **options) if sf1 else with_readiness('M_available', **options))
    adapter = with_local_runtime(original, context)
    env = adapter.OperatingHospitalIESEnv(ROOT, allowed_hours=context['allowed_hours'],
        sealed_days=SEALED_DAYS, model_id=CONSTRUCTOR_ID,
        model_config=deepcopy(configuration['model_config']), operating_rule=deepcopy(configuration['operating_rule']))
    assert getattr(env, 'core', None) is None
    assert configuration == original_configuration
    before_registry = set(objective_snapshot.PHYSICAL_CLASSES)
    observed, info = env.reset(scenario=case['scenario'], seed=20261023)
    assert set(objective_snapshot.PHYSICAL_CLASSES) == before_registry
    assert env.core.time == 0. and len(env.core.segments) == 0
    assert resolve_local_source_freeze(env) == frozen['source']
    assert type(env.core.ups) is (ContinuousLossStore if sf1 else FiniteStore)
    assert type(env.core.cold_store) is (ContinuousLossStore if sf1 else FiniteStore)
    assert env.dt_minutes == 15. and env.dt_h == .25
    assert env.core.pending_commands == [] and env.core.gt_start_pending == {}
    if sf1:
        assert env.model_id == MODEL_ID
        assert observed['single_fault_model']['physical_max_segment_minutes'] == .5
        bound = env.local_actual_witness_source_context
        assert bound.resolved_model_config['single_fault_model'] == env.single_fault_model_spec.as_dict()
        snapshot = decision_snapshot(env)
        assert snapshot['single_fault_model'] == observed['single_fault_model']
        assert snapshot['physical_execution_model_id'] == MODEL_ID
        assert 'ContinuousLossStore' in str(env.v28_demand_receipt['full_initial_state_and_core'])
    else:
        assert 'single_fault_model' not in observed
        assert 'single_fault_model' not in env.operating_model_config
