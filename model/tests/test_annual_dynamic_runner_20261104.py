"""Pure annual glue/typed-state tests; no actual environment or solver call."""
from copy import deepcopy
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.annual_program import runner, checkpoint
from tools.annual_dynamic_20261104 import MODEL_ID, OWNER_SCHEMA
from tools.annual_dynamic_20261104.bridge import CoolingBridgeState


def test_result_inventory_pins_actual_files_and_names_missing_without_pinning_itself(tmp_path):
    assert hasattr(runner, '_dynamic_result_inventory'), 'New result file inventory integration is missing'
    (tmp_path/'CASE.json').write_bytes(b'{}\n')
    (tmp_path/'RESULT.json').write_bytes(b'not-an-input')
    files, missing = runner._dynamic_result_inventory(tmp_path)
    assert files == {'CASE.json': {'sha256': hashlib.sha256(b'{}\n').hexdigest(), 'bytes': 3}}
    assert 'RESULT.json' not in files
    assert 'evidence/SUMMARY.json' in missing and 'RESOLVED_CASE.json' in missing


def test_dynamic_snapshot_wraps_current_legal_native_record(monkeypatch):
    monkeypatch.setattr(runner.legacy, '_snapshot', lambda env: {'decision_time_min':0., 'features':{}})
    env = SimpleNamespace(annual_dynamic_owner_contract=OWNER_SCHEMA, annual_dynamic_declared_calendar=[])
    env.core = SimpleNamespace(time=0., annual_cooling_bridge=CoolingBridgeState(initial_cooling_kW=[0.]*4,
        initial_availability={}), _hardware_data_at=lambda minute: (None,None,None,{}))
    record = runner._snapshot(env)
    assert record['annual_dynamic_execution_model_id'] == MODEL_ID
    assert record['cooling_dynamics']['elapsed_minutes'] == 0.


def test_dynamic_typed_checkpoint_uses_new_profile_and_keeps_queues_without_reset():
    from tests.test_annual_program_checkpoint import runtime, plan_for, BINDING, restore
    env, controller = runtime()
    env.annual_dynamic_owner_contract = OWNER_SCHEMA
    bridge = CoolingBridgeState(initial_cooling_kW=[0.]*4, initial_availability={})
    for minute in range(15):
        bridge.commit(float(minute),float(minute+1),[0.]*4,[0.]*4,{})
    env.core.annual_cooling_bridge = bridge
    document = checkpoint.capture_checkpoint(env,controller,binding=BINDING,
        field_plan=plan_for(env,controller),runner_state={'committed_end_minute':15.})
    assert document['schema'] != checkpoint.SCHEMA
    assert 'dynamic' in document['schema']
    restored = restore(document)['env']
    assert restored.core.annual_cooling_bridge.to_state() == bridge.to_state()
    assert restored.core.pending_commands == env.core.pending_commands
    assert restored.core.station.unsupported_minutes == env.core.station.unsupported_minutes


def test_partition_marks_immutable_fuel_objects_and_aliases_explicitly():
    from tools.annual_dynamic_20261104.checkpoint_support import ENV_ALIASES
    env = SimpleNamespace(core=SimpleNamespace(), annual_dynamic_owner_contract=OWNER_SCHEMA)
    env.single_fault_model_spec = object()
    env._single_fault_model_bound_spec = env.single_fault_model_spec
    env.single_fault_fuel_spec = object()
    env._single_fault_fuel_bound_spec = env.single_fault_fuel_spec
    env.single_fault_fuel_curve = object()
    env._single_fault_fuel_bound_curve = env.single_fault_fuel_curve
    env.reference_initialization_solver = object()
    env._sf2_bound_reference_solver = env.reference_initialization_solver
    plan = runner._partition(env,SimpleNamespace(),{})
    assert plan['env']['single_fault_fuel_curve'] == 'rebuild'
    for alias,target in ENV_ALIASES.items():
        assert plan['env'][alias] == 'alias:env.' + target


def test_resolved_document_includes_actual_stock_dynamic_and_conversion_receipts():
    assert hasattr(runner,'_resolved_case_document'), 'New resolved document integration is missing'
    class Store:
        def observe(self):
            return dict(integration_contract='single_fault.continuous_loss_held_flow.v1',
                charge_efficiency=.95,discharge_efficiency=.95,loss_per_hour=.01,
                energy_kWh=10.,capacity_kWh=20.,power_kW=5.)
    actual=SimpleNamespace(annual_dynamic_owner_contract=OWNER_SCHEMA,operating_model_config={},control_rule={},
        input_access_receipt={},plant=SimpleNamespace(steam_kWh_per_lb=1.,exchanger_eff=.9,
            abs_electric_kW_per_rt=.1,ec_electric_kW_per_rt=.2,abs_steam_lb_per_rt=1.),
        sci_config={'cold_pump_kW_per_kW':.01},operating_execution_contract={'diesel_mode':'emergency_bus'},
        annual_dynamic_config={'cooling_tau_seconds':[137.717,120.,120.,120.],
            'tower_support_tau_seconds':360.,'pump_support_tau_seconds':60.})
    actual.core=SimpleNamespace(time=0.,ups=Store(),cold_store=Store(),
        station=SimpleNamespace(config={'boiler_fixed_kW':1.,'boiler_kW_per_kWsteam':.01}),
        _hardware_data_at=lambda minute:(None,None,None,{}),
        _current_context=lambda hardware:{'recovery_pump_kW_per_kW':.01,'recovery_fan_kW_per_kW':.02})
    document=runner._resolved_case_document({'id':'fixture'},[1],actual,SimpleNamespace(policy_id='M',config={}))
    assert document['annual_dynamic_model_id']==MODEL_ID
    assert document['annual_dynamic_owner_contract']==OWNER_SCHEMA
    assert document['case']=={'id':'fixture'}
    assert document['storage_state_receipts']['cold_store']['energy_kWh']==10.
    assert document['reset_physical_context']['recovery_pump_kW_per_kW']==.01
    assert document['physical_constants']['RT_TO_KW']>0.


@pytest.mark.parametrize('mode',('N','F','D','FD'))
def test_dynamic_build_runtime_installs_real_mixins_without_constructing_core_or_solving(monkeypatch,mode):
    import json
    import tools.annual_readiness.environment as readiness
    from tools.single_fault_fuel_20261102.runtime import FuelCoreMixin
    cfg=json.loads((runner.ROOT/'configs/plant_configs.json').read_text('utf8'))
    name,setting=next(iter(cfg['study_cases'].items()))
    nominal=cfg['source_equipment']['gas_turbines']['rated_electric_kW_each']
    class NativeCore:
        pass
    env=SimpleNamespace(root=runner.ROOT,engineering_core_type=NativeCore,control_rule_id='test',config={},
        operating_model_config={},plant=SimpleNamespace(case=name,
            nominal_gt_capacity_used_for_heat_rate_kW=nominal,gt_rated_kW=nominal*setting['gas_turbine_scale']))
    def no_reset(**kwargs):
        pytest.fail('No reset/IES execution is permitted in this factory regression')
    env.reset=no_reset
    def fake_factory(*args,**kwargs):
        return env
    adapter=SimpleNamespace(OperatingHospitalIESEnv=fake_factory,
        controller_factory=lambda *args,**kwargs:SimpleNamespace(policy_id=kwargs['policy_id'],config=kwargs['config']))
    monkeypatch.setattr(readiness,'with_readiness',lambda *args,**kwargs:adapter)
    monkeypatch.setattr(runner,'required_hours',lambda case:{1})
    required=('configs/plant_configs.json','src/hospital_ies/model.py',
        'tools/single_fault_model_20261102/storage.py','tools/single_fault_model_20261102/runtime.py',
        'tools/single_fault_fuel_20261102/fuel.py','tools/single_fault_fuel_20261102/runtime.py',
        'tools/cooling_dynamics_20261104/dynamics.py','tools/annual_program/source_runtime.py')
    freeze={name:hashlib.sha256((runner.ROOT/name).read_bytes()).hexdigest() for name in required}
    case={'annual_readiness':{'method':'M_ready','parameters':{'requalification_minutes':60.}},
        'v28_thermal_profile':{},'annual_program':{'parameters':{'mode':mode}},
        'execution_revision':{'model_id':MODEL_ID,'owner_contract':OWNER_SCHEMA,'declared_calendar':[]}}
    configuration={'model_config':{},'operating_rule':{},'control_rule_id':'test','policy':{'alias':'M','config':{}}}
    actual,_=runner._build_runtime(case,configuration,{'runtime_freeze_receipt':{'source_sha256':freeze}},Path('unused'),None)
    assert actual is env and not hasattr(env,'core')
    assert env.engineering_core_type._solve is FuelCoreMixin._solve
    assert env.annual_dynamic_owner_contract==OWNER_SCHEMA


def test_dynamic_migration_metadata_does_not_claim_legacy_native_witness():
    assert hasattr(runner,'_execution_log_migrations'), 'New owner metadata migration is missing'
    value=runner._execution_log_migrations(SimpleNamespace(annual_dynamic_owner_contract=OWNER_SCHEMA))
    assert value['final_actual_primal_witness'] is None
    assert value['actual_engineering_owner']==OWNER_SCHEMA
    legacy=runner._execution_log_migrations(SimpleNamespace())
    assert legacy['final_actual_primal_witness']=='hospital_ies.annual_program.final_actual_primal_witness.v1'


def test_smoke_case_unwrap_and_revision_calendar_preserve_original_cause():
    from tools.annual_dynamic_20261104 import server_smoke
    assert hasattr(server_smoke,'smoke_case'), 'New execution_revision smoke CASE reader is missing'
    declared=[{'time_minute':1.,'availability':{'EC_1_PRIMARY_PUMP':0.}}]
    case={'execution_revision':{'declared_calendar':declared}}
    assert server_smoke.smoke_case(case)==case
    assert server_smoke.smoke_case({'case':case,'configuration':{'frozen':True}})==case
    assert server_smoke.smoke_declared_calendar(case)==declared
