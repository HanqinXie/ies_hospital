"""Runner-only contract checks. Fixture APIs never reset/step or call a solver."""
import copy
import json
from pathlib import Path
import sys
from contextlib import nullcontext
from types import ModuleType, SimpleNamespace
import unittest
import pytest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.model_closure_v22 import validation as runner


def segment():
    return dict(start_minute=0., end_minute=15., command_id=1,
        service_request_ledger=dict(original_demand_kW=[[2.]*3 for _ in range(4)],
            delivered_kW=[[1.]*3 for _ in range(4)], active_cut_kW=[[.25]*3 for _ in range(4)],
            connected_unserved_kW=[[.75]*3 for _ in range(4)]),
        action=dict(diesel_kW=12., gt_start_electric_kW=10., boiler_restart_electric_kW=.5),
        m22_station=dict(boiler_ready=False, boiler_powered_minutes=.5),
        m22_mode_selection=dict(cold_mode='discharge', inventory_committed=False),
        m21_storage=dict(ups=dict(initial_kWh=40., final_kWh=39., discharged_kWh=.95)))


class TestValidationContracts(unittest.TestCase):
    @pytest.fixture(autouse=True)
    def sandbox(self, tmp_path, monkeypatch):
        self.sandbox_path = tmp_path
        # worker prepends its selected root; restore sys.path after every test.
        monkeypatch.setattr(sys, 'path', list(sys.path))
        monkeypatch.setattr(sys, 'dont_write_bytecode', sys.dont_write_bytecode)

    def fake_worker(self, output, *, events=(), legacy=False, postprocess_error=False, policy_override=None, closure_override=False):
        # The former id-only fixture bypassed fields the actual exporter reads.
        # Explicit complete provenance keeps these no-physics API tests honest.
        scene = dict(id='unit_fixture', family_id='unit_family', duration_steps=2, step_minutes=15.,
            parent_template_id='unit_parent', base_state_id='unit_initial', exogenous_stream_id='unit_exogenous',
            distribution_id='fixed_unit_test', design_family='unit_family', sampling_cohort='unit_only',
            sampling_method='fixed_unit_test', start_hour=4001, stage=1, event_class='L1')
        if events:
            scene['model_closure_events'] = list(events)
        case = dict(id='unit_fixture', scenario=scene, configuration='H0', policy=dict(alias='C', config={'C_only': True}))
        if policy_override:
            case['policy_override_alias'] = policy_override
        if closure_override:
            case['model_closure_v22'] = {}
        registry = dict(hardware_profiles={'H0': {}}, model_closure_v22={}, schemas={},
            selected_policy_parameters={'F': {'fixed_reserve_hours': 1.}, 'A': {'release_after_minutes': 240.}})
        runner.write(output / 'CASE.json', dict(registry=registry, case=case))
        runner.write(output / 'INVOCATION.json', dict(test_only=True, no_physical_runtime=True))
        calls = dict(construct=0, episode=0)
        payload = dict(summary={'status': 'completed'}, error=None, segments=[segment()])
        package = ModuleType('fixture_package')
        package.MODEL_ID = 'UNIT_FIXTURE_NO_PHYSICS'
        package.CONTRACT_ID = 'unit_fixture'
        environment = ModuleType('fixture_environment')

        class Env:
            def __init__(self, *args, **kwargs):
                calls['construct'] += 1
                self.plant = None
                self.input_access_receipt = {'unit_fixture_only': True}
                self.operating_model_config = {'resolved_only': 'actual_environment_config'}

        def episode(*args, **kwargs):
            calls['episode'] += 1
            self.captured_manifest = copy.deepcopy(args[-1])
            if postprocess_error:
                env = args[0]
                env.core = SimpleNamespace(segments=[segment()], events=[dict(kind='command_generated', time_minute=0.)],
                    solver_calls=[], errors=[])
                env.segment_history = env.core.segments
                env.event_history = env.core.events
                env.zone_history = []
                raise KeyError('unit_postprocessing_failure')
            return copy.deepcopy(payload)

        environment.OperatingHospitalIESEnv = Env
        environment.run_episode = episode
        environment.validate_scenario = lambda scene: scene
        policy = ModuleType('fixture_policy')
        policy.SciPolicy = lambda *args, **kwargs: SimpleNamespace(config=copy.deepcopy(kwargs['config']))
        policy.policy_specs = lambda: {
            'M': {'config': {'policy_mode': 'current', 'reserve_enabled': False}},
            'A': {'config': {'reserve_rule': 'causal_release', 'release_after_minutes': 120. if legacy else 240.}},
            'F': {'config': {'reserve_rule': 'fixed_hours', 'fixed_reserve_hours': 1.}}}
        metrics_module = ModuleType('tools.sci_engineering.runner')
        metrics_module.compact_metrics = lambda *args, **kwargs: dict(observed_until_minute=15.,
            requested_end_minute=30., unknown_tail_minutes=15., complete=False)
        metrics_module.source_binding = lambda *args: {'unit_fixture_only': 'test'}
        metrics_module.input_binding = lambda *args: {}
        design = ModuleType('tools.sci_engineering.design')
        design.allowed_hours = lambda *args: {1}
        design.SEALED_DAYS = ()
        prefix = 'tools.sci_engineering' if legacy else 'tools.model_closure_v22'
        modules = {prefix: package, prefix + '.environment': environment, prefix + '.policy': policy,
            'tools.sci_engineering.runner': metrics_module, 'tools.sci_engineering.design': design}
        with patch.dict(sys.modules, modules), patch.object(runner, 'OUTPUT_ROOT', output.parent):
            code = runner.worker(output / 'CASE.json', ROOT, output, legacy)
        return code, runner.read(output / 'RESULT.json'), calls

    def test_finally_retains_known_metric_prefix_when_summary_omits_extent(self):
        with nullcontext(self.sandbox_path) as temporary:
            output = Path(temporary) / 'case'; output.mkdir()
            code, result, calls = self.fake_worker(output)
            self.assertEqual(code, 1)
            self.assertEqual(calls['episode'], 1)  # Fixture call, never an IES call.
            self.assertEqual(result['observed_until_minute'], 15.)
            self.assertEqual(result['unknown_tail_minutes'], 15.)

    def test_legacy_refuses_new_event_schedule_before_environment_construction(self):
        with nullcontext(self.sandbox_path) as temporary:
            output = Path(temporary) / 'case'; output.mkdir()
            code, result, calls = self.fake_worker(output, legacy=True,
                events=[dict(time_minute=20., availability={'GRID': 0.})])
            self.assertEqual(code, 1)
            self.assertEqual(calls, dict(construct=0, episode=0))
            self.assertEqual(result['actual_episode_attempts'], 0)
            self.assertIn('model_closure_events', result['error']['message'])

    def test_process_retains_m22_mode_and_accounted_startup_ports(self):
        with nullcontext(self.sandbox_path) as temporary:
            path = Path(temporary) / 'PROCESS.csv'
            runner.native_process({'segments': [segment()]}, path)
            import csv
            with path.open(encoding='utf-8-sig', newline='') as stream:
                row = next(csv.DictReader(stream))
            self.assertEqual(json.loads(row['m22_mode_selection_json'])['cold_mode'], 'discharge')
            self.assertEqual(json.loads(row['m22_station_json'])['boiler_powered_minutes'], .5)
            self.assertEqual(float(row['gt_start_electric_kW']), 10.)
            self.assertEqual(float(row['ups_final_kWh']), 39.)

    def test_missing_ledger_is_unknown_and_creates_no_false_process(self):
        with nullcontext(self.sandbox_path) as temporary:
            path = Path(temporary) / 'PROCESS.csv'
            with self.assertRaisesRegex(ValueError, 'Missing service ledger'):
                runner.native_process({'segments': [dict(start_minute=0., end_minute=15.)]}, path)
            self.assertFalse(path.exists())

    def test_manifest_covers_real_exporter_and_operating_wrapper_fields(self):
        with nullcontext(self.sandbox_path) as temporary:
            output = Path(temporary) / 'case'; output.mkdir()
            _, result, _ = self.fake_worker(output)
            required = {'signature', 'simulator_hash', 'execution_contract_id', 'assumption_set_id',
                'schemas', 'splits', 'model_config_signature', 'engineering_model_contract_id'}
            self.assertTrue(required <= set(self.captured_manifest), required-set(self.captured_manifest))
            self.assertEqual(self.captured_manifest['model_config_signature'], result['resolved_model_config_sha256'])
            self.assertEqual(self.captured_manifest['engineering_model_contract_id'], 'unit_fixture')

    def test_postprocessing_exception_recovers_actual_core_prefix_and_stays_failed(self):
        with nullcontext(self.sandbox_path) as temporary:
            output = Path(temporary) / 'case'; output.mkdir()
            code, result, _ = self.fake_worker(output, postprocess_error=True)
            self.assertEqual(code, 1)
            self.assertEqual(result['status'], 'failed_unknown')
            self.assertIsNotNone(result['raw_path'])
            self.assertTrue(result['recovered_prefix'])
            self.assertEqual(result['observed_until_minute'], 15.)
            import gzip
            with gzip.open(result['raw_path'], 'rt', encoding='utf8') as stream:
                raw = json.load(stream)
            self.assertFalse(raw['summary']['full_trajectory_outcome_valid'])
            self.assertEqual(len(raw['segments']), 1)
            self.assertEqual(raw['events'][0]['kind'], 'command_generated')

    def test_policy_override_does_not_copy_original_C_configuration(self):
        with nullcontext(self.sandbox_path) as temporary:
            output = Path(temporary) / 'case'; output.mkdir()
            _, result, _ = self.fake_worker(output, policy_override='M')
            self.assertEqual(result['policy']['alias'], 'M')
            self.assertEqual(result['policy']['config'], {'policy_mode': 'current', 'reserve_enabled': False})

    def test_legacy_A_override_uses_selected_240_minutes_and_preserves_case_binding(self):
        with nullcontext(self.sandbox_path) as temporary:
            output = Path(temporary) / 'case'; output.mkdir()
            _, result, _ = self.fake_worker(output, legacy=True, policy_override='A')
            self.assertEqual(result['policy']['config']['release_after_minutes'], 240.)
            self.assertEqual(result['case_binding']['scenario_before_sha256'], result['case_binding']['scenario_after_sha256'])
            resolved = runner.read(output / 'RESOLVED_CASE.json')
            self.assertEqual(resolved['policy']['alias'], 'A')
            self.assertEqual(resolved['model_config'], result['resolved_model_config'])

    def test_legacy_refuses_attached_M22_configuration_before_environment_construction(self):
        with nullcontext(self.sandbox_path) as temporary:
            output = Path(temporary) / 'case'; output.mkdir()
            code, result, calls = self.fake_worker(output, legacy=True, closure_override=True)
            self.assertEqual(code, 1)
            self.assertEqual(calls, dict(construct=0, episode=0))
            self.assertEqual(result['actual_episode_attempts'], 0)
            self.assertIn('model_closure_v22', result['error']['message'])

    def test_external_case_keeps_registered_hardware_and_binds_input_bytes(self):
        with nullcontext(self.sandbox_path) as temporary:
            directory = Path(temporary)
            path = directory / 'case.json'
            case = dict(id='external_fixture', scenario=copy.deepcopy(runner.read(runner.CASES)['cases'][0]['scenario']), configuration='H1',
                policy=dict(alias='C', config={}), model_closure_v22={'station': {'boiler_fixed_kW': .5}})
            path.write_text(json.dumps(case), encoding='utf8')
            registry = dict(hardware_profiles={'H1': {}}, model_closure_v22=case['model_closure_v22'])
            before = path.read_bytes()
            with patch.object(runner, 'RECIPES_ROOT', directory):
                loaded, binding = runner.load_case_file(path, registry)
            self.assertEqual(loaded, case)
            self.assertEqual(binding['sha256'], runner.sha(path))
            self.assertEqual(path.read_bytes(), before)

    def test_external_case_rejects_unregistered_hardware_profile(self):
        with nullcontext(self.sandbox_path) as temporary:
            directory = Path(temporary); path = directory / 'case.json'
            path.write_text(json.dumps(dict(id='bad', scenario={}, configuration='FREE_CAPACITY',
                policy=dict(alias='C', config={}))), encoding='utf8')
            with patch.object(runner, 'RECIPES_ROOT', directory), self.assertRaisesRegex(ValueError, 'registered hardware'):
                runner.load_case_file(path, dict(hardware_profiles={'H0': {}}, model_closure_v22={}))

    def test_missing_exporter_metadata_is_rejected_before_an_episode(self):
        directory = self.sandbox_path
        case = copy.deepcopy(runner.read(runner.CASES)['cases'][0])
        del case['scenario']['sampling_method']
        path = directory/'missing_sampling_method.json'
        path.write_text(json.dumps(case), encoding='utf-8')
        with patch.object(runner, 'RECIPES_ROOT', directory), self.assertRaisesRegex(ValueError, 'sampling_method'):
            runner.load_case_file(path, runner.read(runner.CASES))

