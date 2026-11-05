"""Synthetic continuous runner contracts; never run a native environment here."""
from copy import deepcopy
import gzip
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


def api():
    assert (Path(__file__).parents[1]/'tools/annual_readiness/runner.py').is_file(), 'continuous single-case runner is not implemented'
    return importlib.import_module('tools.annual_readiness.runner')


def segment(start, end):
    request = [[12., 8., 4.]]+[[0., 0., 0.] for _ in range(3)]
    delivered = [[9., 6., 2.]]+[[0., 0., 0.] for _ in range(3)]
    cut = [[1., 1., 1.]]+[[0., 0., 0.] for _ in range(3)]
    connected = [[2., 1., 1.]]+[[0., 0., 0.] for _ in range(3)]
    return dict(start_minute=start, end_minute=end, hour_of_year=1,
        service_request_ledger=dict(original_demand_kW=request, delivered_kW=delivered,
            active_cut_kW=cut, connected_unserved_kW=connected),
        action=dict(grid_import_kW=1., gt_fuel_MMBtu_h=0., boiler_steam_lbph=0.),
        diesel_local_response=dict(mechanical_work_kWh=0., electrical_work_kWh=0.),
        m21_storage={name: dict(initial_kWh=10.-start/60., final_kWh=10.-end/60., charged_input_kWh=0.,
            discharged_kWh=(end-start)/60., standing_loss_kWh=0., conversion_loss_kWh=0.) for name in ('ups','cold_store')})


class CallLog(list):
    writer = 'must remain attached'


class FakeEnv:
    def __init__(self, *, fail_step=None, fail_before_segment=False):
        self.reset_count = 0
        self.core = SimpleNamespace(time=0., segments=[], events=[], solver_calls=CallLog(), errors=[], _m23_observed_segments=0)
        self.segment_history = self.core.segments
        self.event_history = self.core.events
        self.steps_done = 0
        self.actual_segment_observer = None
        self.fail_step, self.fail_before_segment = fail_step, fail_before_segment
        self.operating_model_config = {'resolved': True}
        self.plant = None
        self.sci_config = {}

    def reset(self, *, scenario, seed):
        self.reset_count += 1
        assert self.reset_count == 1
        self.scenario = deepcopy(scenario)
        self.core.ups = self.core.cold_store = SimpleNamespace(initial_charged_input_kWh=0.)
        return {}, {}

    def step(self, request):
        self.core.solver_calls.extend([{'status':'failed', 'full_error':{'unknown':None}}, {'status':'success', 'step':self.steps_done}])
        self.core.events.append(dict(kind='command_generated', event_id=len(self.core.events),
            time_minute=self.core.time, command={'id':self.steps_done, 'request':deepcopy(request)}))
        failed = self.steps_done == self.fail_step
        if failed and self.fail_before_segment:
            raise RuntimeError('failed before any actual interval')
        end = self.core.time+(5. if failed else 15.)
        actual = segment(self.core.time, end)
        self.core.segments.append(actual)
        if self.actual_segment_observer:
            self.actual_segment_observer(actual)
            self.core._m23_observed_segments += 1
        self.core.time = end
        if failed:
            raise RuntimeError('actual solver failure after partial interval')
        self.steps_done += 1
        return {}, 0., self.steps_done >= self.scenario['duration_steps'], False, dict(segments=[deepcopy(actual)], command_id=self.steps_done-1)


class FakeController:
    def __init__(self):
        self.history_lengths = []

    def decide(self, records):
        assert [r['decision_time_min'] for r in records] == list(range(0, 15*len(records), 15))
        assert all('calendar' not in row and 'scenario' not in row for row in records)
        self.history_lengths.append(len(records))
        return deepcopy(ACTUAL_SCI_REQUEST), {'full_context':{'unknown':None}}


PARAMETERS = dict(gas_price_usd_per_mmbtu=5., dg_shaft_fee_USD_per_kWh=.3,
    steam_kWh_per_lb=.3, boiler_eff=.8, kWh_per_MMBtu=300., ec_electric_kW_per_rt=.8,
    RT_TO_KW=4., cold_pump_kW_per_kW=.02)

# Exact legal request saved by the failed epoch-1 first pilot, before any
# actual interval. The adopted SCI interface supersedes the older P2A targets
# fixture; preserving this request catches use of the wrong validator.
ACTUAL_SCI_REQUEST = {'state_coordinated_joint': {
    'options': {'variant':'coupled','support_hours':2.,'risk_tolerance':1e-7,
        'connection_mode':'retain_when_no_gt','regulator_consistent':True,
        'critical_reserve_multiplier':1.,'preserve_one_gt_standby':True,
        'service_budget_enabled':True,'service_budget_fractions':[.05,.2],
        'handoff_enabled':True,'coordination_preference':'standby_first','features_enabled':True},
    'coordination': {'response_minutes':30.,'support_minutes':60.,'delay_bound_minutes':30.,
        'cooling_preparation':False,'time_energy_preparation':False,'execution_recovery':True,
        'critical_reserve':False,'policy_mode':'current','reserve_enabled':False,
        'critical_preparation_first':False,'reserve_rule':'none','legacy_C19_order':False,
        'online_GT_count_order':'after_cost','fixed_reserve_hours':1.,'release_after_minutes':120.,
        'release_floor_hours':.5,'observed_outage_minutes':0.,'critical_non_diesel_ready':True},
    'risk_loads_kW': [[77.68989642887371,29.801808113825096,43.88265825397186],
        [49.25479539049845,36.76775416089868,28.876216269234618],
        [52.950900422199226,0.,36.986280943062006],
        [474.6126493298864,72.20683342759074,16.882249165317802]]}}


ORDINARY_SCI_REQUEST = {'ordinary_dispatch': {'dispatch': {
    'gt_electric_kW':[0.,0.,0.], 'grid_import_kW':1., 'diesel_kW':0.,
    'boiler_steam_lbph':0., 'absorption_cooling_kW':0.,
    'electric_chiller_cooling_kW':[0.,0.,0.], 'hrsg_steam_lbph':0.,
    'tower_rejection_kW':0., 'gt_fuel_MMBtu_h':0.},
    'disconnected_kW':[[0.,0.,0.] for _ in range(4)], 'rule_diagnostic':{'unknown':None}}}


def execute(tmp_path, monkeypatch, *, retain_history=False, fail_step=None, fail_before_segment=False, journal_type=None):
    module = api()
    monkeypatch.setattr(module, '_snapshot', lambda env: dict(decision_time_min=env.core.time, observed=True))
    monkeypatch.setattr(module, '_terminal', lambda env, end: dict(snapshot_time_minute=end, actual_clock=env.core.time))
    from tools.annual_readiness.journal import BlockJournal
    journal = (journal_type or BlockJournal)(tmp_path/'evidence', identity={'case_id':'fake', 'source':'frozen'})
    env = FakeEnv(fail_step=fail_step, fail_before_segment=fail_before_segment)
    controller = FakeController()
    result = module._execute_loop(env, controller, {'id':'fake', 'duration_steps':3, 'step_minutes':15., 'initialization_seed':1},
        journal, tmp_path, retain_history=retain_history, accounting_parameters=PARAMETERS, tariffs_by_hour={1:.2})
    return result, env, controller


def blocks(path):
    result = []
    for raw in sorted((path/'evidence'/'blocks').glob('*.gz')):
        with gzip.open(raw, 'rt', encoding='utf8') as stream:
            result.append(json.load(stream))
    return result


def test_continuous_three_decisions_reset_once_preserves_history_and_releases_only_after_commit(tmp_path, monkeypatch):
    result, env, controller = execute(tmp_path, monkeypatch)
    assert result['status'] == 'completed'
    assert result['observed_until_minute'] == 45. and result['unknown_tail_minutes'] == 0.
    assert env.reset_count == 1 and controller.history_lengths == [1,2,3]
    # Epoch-2 disproved the last-segment-only assumption: the original history
    # adapter recomputes all cumulative and trailing service from minute zero.
    assert env.segment_history is env.core.segments and len(env.core.segments) == 3
    assert env.core._m23_observed_segments == 3
    assert isinstance(env.core.solver_calls, CallLog) and env.core.solver_calls.writer == 'must remain attached'
    assert len(env.core.solver_calls) == 0
    assert len(env.core.events) == 3
    raw = blocks(tmp_path)
    assert [b['solver_index_base'] for b in raw] == [0,2,4]
    assert [len(b['solver_calls']) for b in raw] == [2,2,2]
    assert [len(b['events']) for b in raw] == [1,1,1]
    assert raw[0]['solver_calls'][0]['full_error']['unknown'] is None
    assert raw[0]['decision']['requested_command'] == ACTUAL_SCI_REQUEST
    assert result['summary']['carrier_service']['P0']['e']['ENS_kWh'] == pytest.approx(2.25)
    assert result['economics']['actual_fee_USD2001'] == pytest.approx(.15)
    assert raw[0]['native_material_intervals']['P0']['e'] == [[0.,15.]]


def test_retain_history_changes_retention_only_not_scientific_blocks(tmp_path, monkeypatch):
    a = tmp_path/'bounded'; a.mkdir()
    b = tmp_path/'full'; b.mkdir()
    bounded, _, _ = execute(a, monkeypatch)
    full, env, _ = execute(b, monkeypatch, retain_history=True)
    assert len(env.core.segments) == 3 and len(env.core.solver_calls) == 6
    for key in ('carrier_service','storage','terminal'):
        assert bounded['summary'][key] == full['summary'][key]
    for left, right in zip(blocks(a), blocks(b)):
        for key in ('segments','solver_calls','events','native_material_intervals'):
            assert left[key] == right[key]


def test_partial_actual_failed_step_is_its_own_committed_block_and_unknown_tail(tmp_path, monkeypatch):
    result, env, _ = execute(tmp_path, monkeypatch, fail_step=1)
    assert result['status'] == 'failed'
    assert result['observed_until_minute'] == 20. and result['unknown_tail_minutes'] == 25.
    assert result['summary']['block_count'] == 2
    assert result['error']['type'] == 'RuntimeError'
    assert result['summary']['carrier_service']['P0']['e']['ENS_kWh'] == pytest.approx(1.)
    raw = blocks(tmp_path)
    assert raw[-1]['end_minute'] == 20. and raw[-1]['error']['type'] == 'RuntimeError'
    assert raw[-1]['solver_calls'][0]['status'] == 'failed'
    assert env.reset_count == 1


def test_failure_without_actual_interval_preserves_solver_context_separately(tmp_path, monkeypatch):
    result, env, _ = execute(tmp_path, monkeypatch, fail_step=0, fail_before_segment=True)
    assert result['status'] == 'failed' and result['observed_until_minute'] == 0.
    assert result['unknown_tail_minutes'] == 45.
    assert result['summary']['block_count'] == 0
    context = list(tmp_path.glob('FAILURE_CONTEXT*.json.gz'))
    assert len(context) == 1
    with gzip.open(context[0], 'rt', encoding='utf8') as stream:
        saved = json.load(stream)
    assert len(saved['solver_calls']) == 2 and saved['segments'] == []
    assert len(env.core.solver_calls) == 2


def test_rejected_journal_append_does_not_release_live_context(tmp_path, monkeypatch):
    from tools.annual_readiness.journal import BlockJournal
    class RejectingJournal(BlockJournal):
        def append(self, block):
            raise OSError('evidence target unavailable')
    result, env, _ = execute(tmp_path, monkeypatch, journal_type=RejectingJournal)
    assert result['status'] == 'failed'
    assert result['observed_until_minute'] == 0. and result['unknown_tail_minutes'] == 45.
    assert len(env.core.solver_calls) == 2 and len(env.core.segments) == 1
    assert len(list(tmp_path.glob('REJECTED_BLOCK*.json.gz'))) == 1
    assert result['actual_core_until_minute'] == 15.


def test_input_identity_drift_is_an_execution_failure(tmp_path, monkeypatch):
    module = api()
    monkeypatch.setattr(module, '_bindings', lambda: {'source':{'new.py':'changed'}, 'inputs':{}, 'git_commit':'newhead'})
    assert module._bindings_unchanged({'source':{'new.py':'old'}, 'inputs':{}, 'git_commit':'oldhead'}, {}) is False


def test_cli_missing_arguments_cannot_start_native_work():
    with pytest.raises(SystemExit) as caught:
        api().main([])
    assert caught.value.code == 2


def test_resolved_state_hook_runs_after_single_reset_before_first_decision(tmp_path, monkeypatch):
    module = api()
    monkeypatch.setattr(module,'_snapshot',lambda env: dict(decision_time_min=env.core.time))
    monkeypatch.setattr(module,'_terminal',lambda env,end: dict(snapshot_time_minute=end))
    from tools.annual_readiness.journal import BlockJournal
    env, controller = FakeEnv(), FakeController()
    observed = []
    def resolved(actual):
        observed.append((actual.reset_count,actual.steps_done,len(actual.core.solver_calls)))
    result = module._execute_loop(env,controller,{'id':'fake','duration_steps':1,'step_minutes':15.},
        BlockJournal(tmp_path/'evidence',identity={'case_id':'fake'}),tmp_path,
        accounting_parameters=PARAMETERS,tariffs_by_hour={1:.2},on_reset=resolved)
    assert result['status'] == 'completed'
    assert observed == [(1,0,0)]


def test_runtime_constructor_preserves_old_model_gate_and_complete_logging_inputs(tmp_path, monkeypatch):
    module = api()
    from tools.annual_readiness import environment
    from tools.model_correction_v25.environment import MODEL_ID as OLD_MODEL_ID
    from tools.model_closure_v22 import validation
    from tools.sci_engineering import design
    captured = {}
    def create(root, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(control_rule_id='sustained_critical_60min',control_rule=kwargs['operating_rule'],plant='unchangedplant')
    def controller(alias, **kwargs):
        captured['controller_alias'] = alias
        return SimpleNamespace(config=kwargs['config'],policy_id=kwargs['policy_id'])
    def adapter(method, **kwargs):
        captured['method'] = method; captured['adapter'] = kwargs
        return SimpleNamespace(OperatingHospitalIESEnv=create,controller_factory=controller)
    monkeypatch.setattr(environment,'with_readiness',adapter)
    monkeypatch.setattr(validation,'runtime_policy',lambda *args: {'alias':'M','config':{'registered':True}})
    monkeypatch.setattr(design,'allowed_hours',lambda panel: {11,12,13})
    registry = {'hardware_profiles':{'H0':{'physical_capacity':123.}},'default_operating_rule':'current_service_bridge'}
    for key in ('model_closure_v22','model_correctness_v23','engineering_completion_v24','model_correction_v25'):
        registry[key] = {'unchanged':key}
    original = deepcopy(registry)
    case = dict(configuration='H0',annual_readiness={'method':'M_ready','parameters':{'requalification_minutes':60.}},
        operating_rule_id='sustained_critical_60min',scenario={'id':'fake'},
        v27_load_overlay={'registered':'overlay'},v28_thermal_profile={'registered':'thermal'})
    writer = object()
    module._build_runtime(case,registry,tmp_path,writer)
    assert captured['model_id'] == OLD_MODEL_ID
    assert captured['model_config']['physical_capacity'] == 123.
    assert captured['operating_rule'] == {'name':'sustained_critical','design_minutes':60.}
    assert captured['allowed_hours'] == {11,12,13}
    assert captured['sealed_days'] == design.SEALED_DAYS
    assert captured['solver_row_sidecar'] is writer
    assert captured['enable_all_validation_rows'] is True
    assert captured['solver_evidence_directory'] == tmp_path/'domain_evidence'
    assert captured['adapter']['overlay_spec'] == case['v27_load_overlay']
    assert captured['adapter']['thermal_profile'] == case['v28_thermal_profile']
    assert registry == original


@pytest.mark.parametrize('folder',['inputs','src','tools','outputs','configs','docs','tests'])
def test_output_path_cannot_modify_project_source_inputs_or_old_outputs(folder):
    module = api()
    candidate = module.ROOT/folder/'annual_readiness_forbidden_new_attempt'
    with pytest.raises(ValueError,match='protected|source|immutable'):
        module._checked_output(candidate)
    assert not candidate.exists()


def test_native_failure_diagnostic_and_error_ledger_survive_partial_commit(tmp_path, monkeypatch):
    module = api()
    monkeypatch.setattr(module,'_snapshot',lambda env: dict(decision_time_min=env.core.time))
    monkeypatch.setattr(module,'_terminal',lambda env,end: dict(snapshot_time_minute=end))
    from tools.annual_readiness.journal import BlockJournal
    class RichFailureEnv(FakeEnv):
        def step(self, request):
            try:
                return super().step(request)
            except RuntimeError as exc:
                exc.category = 'model_constraint_infeasible'
                exc.simulation_diagnostic = {'pending_commands':[{'unknown':None}], 'physical_state':{'stock':2.}}
                exc.solver_backend_attempts = [{'status':3,'rows':[{'name':'failed-full-row','bound':None}]}]
                self.core.errors.append(deepcopy(exc.simulation_diagnostic))
                raise
    env = RichFailureEnv(fail_step=0)
    result = module._execute_loop(env,FakeController(),{'id':'fake','duration_steps':3,'step_minutes':15.},
        BlockJournal(tmp_path/'evidence',identity={'case_id':'fake'}),tmp_path,
        accounting_parameters=PARAMETERS,tariffs_by_hour={1:.2})
    assert result['error']['diagnostic']['pending_commands'] == [{'unknown':None}]
    assert result['error']['underlying_category'] == 'model_constraint_infeasible'
    assert result['error']['solver_backend_attempts'][0]['rows'][0]['bound'] is None
    assert result['solver_errors'][0]['physical_state']['stock'] == 2.
    assert blocks(tmp_path)[0]['solver_errors'] == result['solver_errors']


def test_native_service_evaluation_failure_is_not_a_completed_run(tmp_path, monkeypatch):
    module = api()
    def invalid(*args,**kwargs):
        raise ValueError('native actual service ledger is inconsistent')
    monkeypatch.setattr(module,'_block_evaluation',invalid)
    result, _, _ = execute(tmp_path,monkeypatch)
    assert result['status'] == 'failed'
    assert result['summary']['block_count'] == 1
    assert result['observed_until_minute'] == 15. and result['unknown_tail_minutes'] == 30.
    assert blocks(tmp_path)[0]['native_evaluation_error']['type'] == 'ValueError'


def test_direct_callable_temp_directory_is_scoped_to_attempt_and_restored(tmp_path, monkeypatch):
    module = api()
    import os
    import tempfile
    original = {name:os.environ.get(name) for name in ('TEMP','TMP')}
    original_cached = tempfile.tempdir
    (tmp_path/'temp').mkdir()
    monkeypatch.setattr(module,'_prepare_attempt',lambda *args,**kwargs: ({'id':'fake'},tmp_path,{}))
    def execute_attempt(output):
        assert os.environ['TEMP'] == str(tmp_path/'temp')
        assert os.environ['TMP'] == str(tmp_path/'temp')
        assert Path(tempfile.gettempdir()) == tmp_path/'temp'
        return {'status':'completed'}
    monkeypatch.setattr(module,'_execute_attempt',execute_attempt)
    assert module.run_case({'id':'fake'},tmp_path)['status'] == 'completed'
    assert {name:os.environ.get(name) for name in ('TEMP','TMP')} == original
    assert tempfile.tempdir == original_cached


def test_terminal_export_error_preserves_primary_step_failure_and_actual_prefix(tmp_path, monkeypatch):
    module = api()
    monkeypatch.setattr(module,'_snapshot',lambda env: dict(decision_time_min=env.core.time))
    def fail_terminal(*args):
        raise KeyError('terminal serialization unavailable')
    monkeypatch.setattr(module,'_terminal',fail_terminal)
    from tools.annual_readiness.journal import BlockJournal
    result = module._execute_loop(FakeEnv(fail_step=0),FakeController(),{'id':'fake','duration_steps':3,'step_minutes':15.},
        BlockJournal(tmp_path/'evidence',identity={'case_id':'fake'}),tmp_path,
        accounting_parameters=PARAMETERS,tariffs_by_hour={1:.2})
    assert result['status'] == 'failed' and result['error']['type'] == 'RuntimeError'
    assert result['observed_until_minute'] == 5.
    actual = blocks(tmp_path)[0]
    assert actual['terminal']['completeness'] == 'unknown_terminal_export_error'
    assert actual['terminal']['error']['type'] == 'KeyError'


@pytest.mark.parametrize('reject_ack',[False,True])
@pytest.mark.parametrize('retain_history',[False,True])
def test_original_sidecar_is_closed_and_verified_before_ack_or_release(tmp_path, monkeypatch, reject_ack, retain_history):
    module = api()
    monkeypatch.setattr(module,'_snapshot',lambda env: dict(decision_time_min=env.core.time))
    monkeypatch.setattr(module,'_terminal',lambda env,end: dict(snapshot_time_minute=end))
    from tools.annual_readiness.journal import BlockJournal
    from tools.sci_engineering.solver_log import SolverRowSidecar,SolverCallLog,iter_sidecar_entries
    from tools.model_correctness_v23.evidence import all_rows_solver_log,DomainEvidenceStore
    initial_writer = SolverRowSidecar(tmp_path/'solver_rows_block_000000.jsonl.gz')
    class DurableEnv(FakeEnv):
        def step(self, request):
            returned = super().step(request)
            self.core.solver_calls.append({'status':'success','solver_backend_layers':[{'status':0,
                'validation':{'rows':[{'name':'full_actual_constraint','unknown':None}]}}]})
            return returned
    env = DurableEnv()
    store = DomainEvidenceStore(tmp_path/'domain_evidence',enable_all_validation_rows=True)
    env.core.solver_calls = all_rows_solver_log(SolverCallLog(writer=initial_writer),store)
    original_sink = env.core.solver_calls
    env._solver_row_sidecar = initial_writer
    checked = []
    class CheckingJournal(BlockJournal):
        def append(self, block):
            receipt = block['solver_row_sidecar']
            assert receipt['closed'] is True and env.core.solver_calls.writer.closed is True
            assert len(env.core.solver_calls) == (3*(len(checked)+1) if retain_history else 3)
            entries = list(iter_sidecar_entries(receipt['path']))
            assert entries[0]['rows'][0]['unknown'] is None
            checked.append(receipt['path'])
            if reject_ack:
                raise OSError('ack rejected after full rows were durably saved')
            return super().append(block)
    result = module._execute_loop(env,FakeController(),{'id':'fake','duration_steps':2,'step_minutes':15.},
        CheckingJournal(tmp_path/'evidence',identity={'case_id':'fake'}),tmp_path,
        accounting_parameters=PARAMETERS,tariffs_by_hour={1:.2},retain_history=retain_history)
    assert env.core.solver_calls is original_sink
    if reject_ack:
        assert result['status'] == 'failed' and result['observed_until_minute'] == 0.
        assert len(env.core.solver_calls) == 3 and len(checked) == 1
    else:
        assert result['status'] == 'completed' and result['observed_until_minute'] == 30.
        assert len(checked) == 2 and checked[0] != checked[1]
        raw = blocks(tmp_path)
        assert raw[0]['initial_preparation'] == {'ups':0.,'cold_store':0.}
        assert raw[1]['initial_preparation'] is None
        assert [r['solver_row_sidecar']['closed'] for r in raw] == [True,True]
        assert [r['solver_index_base'] for r in raw] == [0,3]


@pytest.mark.parametrize('drift_at_finish',[False,True])
def test_attempt_orchestration_checks_final_source_identity_and_preserves_real_status(tmp_path, monkeypatch, drift_at_finish):
    module = api()
    from tools.annual_readiness import registration
    from tools.model_review_v30 import evaluation
    from tools.sci_engineering import design
    case = {'id':'fake','configuration':'H0','scenario':{'id':'fake','duration_steps':1,'step_minutes':15.},
        'annual_readiness':{'method':'M_ready'},'v28_thermal_profile':{'files':{}}}
    registry = {'frozen':True}
    bound = {'source':{},'inputs':{},'git_commit':'frozen-head'}
    module._write_new(tmp_path/'CASE.json',dict(case=case,registry=registry,retain_history=False,case_input_binding=None))
    module._write_new(tmp_path/'BINDINGS.json',bound)
    monkeypatch.setattr(registration,'load_registry',lambda: registry)
    monkeypatch.setattr(registration,'validate_case',lambda supplied,reg: deepcopy(supplied))
    monkeypatch.setattr(registration,'worker_registry',lambda supplied,reg: deepcopy(reg))
    monkeypatch.setattr(module,'_extra_inputs',lambda *args: {})
    calls = 0
    def binding_now():
        nonlocal calls
        calls += 1
        return dict(bound,git_commit='changed-head') if drift_at_finish and calls > 1 else deepcopy(bound)
    monkeypatch.setattr(module,'_bindings',binding_now)
    monkeypatch.setattr(design,'allowed_hours',lambda panel: {1})
    monkeypatch.setattr(evaluation,'_load_frozen_accounting',lambda *args: (PARAMETERS,{1:.2},{'frozen-accounting':'sha'}))
    class ReceiptCheckingController(FakeController):
        def decide(self, records):
            assert (tmp_path/'RESOLVED_CASE.json').is_file()
            return super().decide(records)
    monkeypatch.setattr(module,'_build_runtime',lambda *args: (FakeEnv(),ReceiptCheckingController(),{'policy':{'alias':'M'}}))
    monkeypatch.setattr(module,'_snapshot',lambda env: dict(decision_time_min=env.core.time))
    monkeypatch.setattr(module,'_terminal',lambda env,end: dict(snapshot_time_minute=end))
    result = module._execute_attempt(tmp_path)
    assert result['actual_episode_attempts'] == 1
    assert result['observed_until_minute'] == 15. and result['unknown_tail_minutes'] == 0.
    assert result['source_and_inputs_unchanged'] is (not drift_at_finish)
    assert result['status'] == ('failed' if drift_at_finish else 'completed')
    assert json.loads((tmp_path/'RESULT.json').read_text(encoding='utf8'))['status'] == result['status']
    assert (tmp_path/'SUMMARY.json').is_file() and (tmp_path/'SOLVER_ROWS_RECEIPT.json').is_file()


def test_original_sidecar_close_failure_retains_failure_receipt_without_ack(tmp_path, monkeypatch):
    module = api()
    monkeypatch.setattr(module,'_snapshot',lambda env: dict(decision_time_min=env.core.time))
    monkeypatch.setattr(module,'_terminal',lambda env,end: dict(snapshot_time_minute=end))
    from tools.annual_readiness.journal import BlockJournal
    from tools.sci_engineering.solver_log import SolverRowSidecar,SolverCallLog
    writer = SolverRowSidecar(tmp_path/'solver_rows_block_000000.jsonl.gz')
    class DurableEnv(FakeEnv):
        def step(self, request):
            returned = super().step(request)
            self.core.solver_calls.append({'status':'success','solver_backend_layers':[{'status':0,
                'validation':{'rows':[{'full':'constraint'}]}}]})
            return returned
    env = DurableEnv()
    env.core.solver_calls = SolverCallLog(writer=writer)
    def fail_flush(data):
        raise OSError('full solver-row evidence disk')
    monkeypatch.setattr(writer,'_flush_block',fail_flush)
    result = module._execute_loop(env,FakeController(),{'id':'fake','duration_steps':1,'step_minutes':15.},
        BlockJournal(tmp_path/'evidence',identity={'case_id':'fake'}),tmp_path,
        accounting_parameters=PARAMETERS,tariffs_by_hour={1:.2})
    assert result['status'] == 'failed' and result['observed_until_minute'] == 0.
    assert result['unknown_tail_minutes'] == 15. and len(env.core.solver_calls) == 3
    assert result['solver_row_sidecars'][0]['status'] == 'failed'
    with gzip.open(next(tmp_path.glob('REJECTED_BLOCK*.json.gz')),'rt',encoding='utf8') as stream:
        rejected = json.load(stream)
    assert rejected['solver_row_sidecar']['status'] == 'failed'
    assert rejected['solver_row_sidecar']['close_error']['type'] == 'OSError'


def execute_request(tmp_path,monkeypatch,request):
    module = api()
    monkeypatch.setattr(module,'_snapshot',lambda env: dict(decision_time_min=env.core.time))
    monkeypatch.setattr(module,'_terminal',lambda env,end: dict(snapshot_time_minute=end))
    from tools.annual_readiness.journal import BlockJournal
    class RecordingEnv(FakeEnv):
        def step(self, submitted):
            self.actual_request = submitted
            return super().step(submitted)
    class Controller:
        def decide(self, records):
            return request, {'request_contract':'unchanged_adopted_SCI'}
    env = RecordingEnv()
    result = module._execute_loop(env,Controller(),{'id':'fake','duration_steps':1,'step_minutes':15.},
        BlockJournal(tmp_path/'evidence',identity={'case_id':'action_contract'}),tmp_path,
        accounting_parameters=PARAMETERS,tariffs_by_hour={1:.2})
    return result,env


@pytest.mark.parametrize('command',[ACTUAL_SCI_REQUEST,ORDINARY_SCI_REQUEST])
def test_adopted_routes_reach_step_without_transforming_original_request(tmp_path,monkeypatch,command):
    submitted = deepcopy(command)
    before = deepcopy(submitted)
    result,env = execute_request(tmp_path,monkeypatch,submitted)
    assert result['status'] == 'completed'
    assert env.actual_request is submitted and submitted == before
    assert blocks(tmp_path)[0]['decision']['requested_command'] == before


@pytest.mark.parametrize('command',[
    {'targets':{'grid_import':1.}},
    {'state_coordinated_joint':{'future_fault_script':[]}},
    {'ordinary_dispatch':dict(ORDINARY_SCI_REQUEST['ordinary_dispatch'],force_execute=True)},
    {'future_controller':{}},
])
def test_adopted_routes_reject_foreign_or_forbidden_commands_before_step(tmp_path,monkeypatch,command):
    result,env = execute_request(tmp_path,monkeypatch,deepcopy(command))
    assert result['status'] == 'failed'
    assert result['error']['type'] == 'ValueError'
    assert result['observed_until_minute'] == 0. and result['unknown_tail_minutes'] == 15.
    assert not hasattr(env,'actual_request')


def execute_original_history_consumers(output,monkeypatch,*,retain_history):
    """Use the real inherited completed-prefix consumers, without native solves."""
    module = api()
    from tools.annual_readiness.journal import BlockJournal
    from tools.executed_service.history import make_snapshot,validate_history
    from tools.state_coordinated.environment import actual_supply_telemetry
    from tools.integrated_learning.policies import legal_history

    class HistoryEnv(FakeEnv):
        def __init__(self):
            super().__init__()
            self.config = dict(p0_solver_feasibility_tol_kW=.001,
                p0_unserved_abs_tol_kW=.01,p0_unserved_rel_tol=.001)

        def step(self,request):
            self.core.solver_calls.extend([{'status':'failed','full_error':{'unknown':None}},
                {'status':'success','step':self.steps_done}])
            command = self.steps_done+1
            self.core.events.append(dict(kind='command_generated',event_id=len(self.core.events),
                time_minute=self.core.time,command={'id':command,'request':deepcopy(request)}))
            start = self.core.time
            actual = []
            for part,(left,right) in enumerate(((start,start+1.),(start+1.,start+15.))):
                row = segment(left,right)
                ledger = row['service_request_ledger']
                losses = ([[2.,0.,3.],[1.,2.,0.]] if part == 0 else [[0.,4.,3.],[1.,0.,3.]])
                if self.steps_done % 2:
                    losses = [[1.,0.,0.],[0.,0.,0.]]
                for index,demand,loss in zip((2,3),([10.,20.,30.],[5.,10.,15.]),losses):
                    ledger['original_demand_kW'][index] = demand
                    ledger['delivered_kW'][index] = [a-b for a,b in zip(demand,loss)]
                    ledger['active_cut_kW'][index] = [0.,0.,0.]
                    ledger['connected_unserved_kW'][index] = loss
                row.update(command_id=command,loads_kW=deepcopy(ledger['original_demand_kW']),
                    unserved_kW=[[a+b for a,b in zip(cut,connected)] for cut,connected in
                        zip(ledger['active_cut_kW'],ledger['connected_unserved_kW'])])
                row['action'].update(deepcopy(ORDINARY_SCI_REQUEST['ordinary_dispatch']['dispatch']))
                self.core.segments.append(row)
                actual.append(row)
                if self.actual_segment_observer:
                    self.actual_segment_observer(row)
                    self.core._m23_observed_segments += 1
            self.core.time = start+15.
            self.steps_done += 1
            return {},0.,self.steps_done >= self.scenario['duration_steps'],False,dict(segments=deepcopy(actual),command_id=command)

    def original_consumers(env):
        now = env.core.time
        base = dict(decision_time_min=now,observation_time_min=now,
            features={'P2_e_demand_kW':5.+now},
            feature_metadata={'P2_e_demand_kW':{'available_time_min':now,'valid_time_min':now}})
        value = make_snapshot(env,base)
        value['actual_supply_telemetry'] = actual_supply_telemetry(env)
        return value

    class HistoryController:
        def __init__(self):
            self.snapshots = []
            self.requests = []
            self.history_lengths = []

        def decide(self,records):
            legal = legal_history(records,max_length=len(records))
            for row in records:
                validate_history(row['executed_service_history'],row['decision_time_min'])
            self.snapshots.append(deepcopy(records[-1]))
            self.history_lengths.append(len(legal))
            request = deepcopy(ACTUAL_SCI_REQUEST)
            request['state_coordinated_joint']['risk_loads_kW'][2][0] = min(r['features']['P2_e_demand_kW'] for r in legal)
            self.requests.append(deepcopy(request))
            return request, {'full_legal_history_count':len(legal)}

    monkeypatch.setattr(module,'_snapshot',original_consumers)
    monkeypatch.setattr(module,'_terminal',lambda env,end: dict(snapshot_time_minute=end))
    env,controller = HistoryEnv(),HistoryController()
    result = module._execute_loop(env,controller,{'id':'history_fake','duration_steps':5,'step_minutes':15.},
        BlockJournal(output/'evidence',identity={'case_id':'original_history'}),output,
        retain_history=retain_history,accounting_parameters=PARAMETERS,tariffs_by_hour={1:.2})
    return result,env,controller,original_consumers


def test_original_prefix_history_and_supply_consumers_survive_all_decisions(tmp_path,monkeypatch):
    result,env,controller,snapshot = execute_original_history_consumers(tmp_path,monkeypatch,retain_history=False)
    assert result['status'] == 'completed'
    assert [s['decision_time_min'] for s in controller.snapshots] == [0.,15.,30.,45.,60.]
    assert controller.history_lengths == [1,2,3,4,5]
    assert env.reset_count == 1 and len(env.core.segments) == 10
    assert env.core._m23_observed_segments == 10
    assert len(env.core.solver_calls) == 0
    at30 = controller.snapshots[2]['executed_service_history']
    assert at30['history_start_min'] == 0. and at30['history_end_min'] == 30.
    assert at30['cumulative_ENS_kWh'][0] == pytest.approx([17./60.,56./60.,45./60.])
    assert at30['cumulative_material_minutes'][0] == [16.,14.,15.]
    assert at30['trailing_material_minutes'][0] == [15.,0.,0.]
    assert at30['cumulative_ENS_kWh'][1] == pytest.approx([15./60.,2./60.,42./60.])
    assert at30['trailing_material_minutes'][1] == [0.,0.,0.]
    assert snapshot(env)['actual_supply_telemetry']['interval_end_minute'] == 75.
    assert controller.requests[-1]['state_coordinated_joint']['risk_loads_kW'][2][0] == 5.


def test_full_policy_observations_and_requests_match_retained_solver_history(tmp_path,monkeypatch):
    a,b = tmp_path/'released_solver',tmp_path/'retained_solver'
    a.mkdir();b.mkdir()
    released,_,rc,_ = execute_original_history_consumers(a,monkeypatch,retain_history=False)
    retained,_,fc,_ = execute_original_history_consumers(b,monkeypatch,retain_history=True)
    assert released['status'] == retained['status'] == 'completed'
    assert rc.snapshots == fc.snapshots and rc.requests == fc.requests
    assert released['summary']['carrier_service'] == retained['summary']['carrier_service']
    assert released['summary']['storage'] == retained['summary']['storage']
    assert released['economics'] == retained['economics']
