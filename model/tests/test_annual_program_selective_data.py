"""Synthetic annual data-access routing; no real sealed values or physical run."""
import copy
from datetime import datetime, timezone
import hashlib
import importlib
import json
from types import SimpleNamespace

import pandas as pd
import pytest

from tools.annual_program import data_contract
from tools.autonomous_dispatch import selective_data
from tools.sci_engineering.design import SEALED_DAYS
from tools.sci_engineering.runner import input_binding


HOURS = [21*24+1, 21*24+2]


def api():
    try:
        return importlib.import_module('tools.annual_program.access')
    except ModuleNotFoundError as exc:
        if exc.name != 'tools.annual_program.access':
            raise
        pytest.fail('Frozen annual data-access boundary is missing', pytrace=False)


def signed(value):
    value['signature'] = hashlib.sha256(json.dumps(
        {k:v for k,v in value.items() if k != 'signature'}, ensure_ascii=False,
        sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf8')).hexdigest()
    return value


@pytest.fixture
def registered(tmp_path):
    root = tmp_path/'workspace'
    (root/'src').mkdir(parents=True)
    (root/'src/fixture.py').write_text('frozen = 1\n', encoding='utf8')
    (root/'inputs/loads').mkdir(parents=True)
    for name in selective_data.FILES:
        (root/'inputs/loads'/name).write_text('synthetic byte identity only\n', encoding='utf8')
    authority = root/selective_data.SEALED_AUTHORITY
    authority.parent.mkdir(parents=True)
    authority.write_text(json.dumps(dict(p2b_time_blocks=[
        dict(time_block_id='fixture_'+str(day), reserved_only=True) for day in SEALED_DAYS])), encoding='utf8')
    approval = data_contract.issue_approval_receipt(
        approval_question=data_contract.APPROVAL_QUESTION,
        approval_answer=data_contract.APPROVAL_ANSWER,
        parent_head=data_contract.PARENT_HEAD, source_input_sha256=input_binding(root),
        approved_at='2026-10-30T12:00:00+08:00')
    case = dict(id='annual_synthetic_case', scenario=dict(id='annual_synthetic_scene',
        start_hour=1, duration_steps=35040, step_minutes=15.))
    configuration = dict(H2=dict(synthetic_fixture=True))
    freeze = api().create_runtime_freeze(root, approval_receipt=approval,
        case=case, configuration=configuration)
    context = dict(approval_receipt=approval, runtime_freeze_receipt=freeze,
                   case=case, configuration=configuration)
    return SimpleNamespace(root=root, approval=approval, freeze=freeze, context=context)


class BeforeNumeric(Exception):
    pass


def before_numeric(monkeypatch):
    calls = []
    def stop(path, **kwargs):
        calls.append(str(path))
        raise BeforeNumeric('Synthetic stop before physical-value parsing')
    monkeypatch.setattr(selective_data, '_identity', stop)
    return calls


def test_only_full_bound_context_reaches_the_synthetic_input_boundary(registered, monkeypatch):
    calls = before_numeric(monkeypatch)
    result = api().validate_annual_access(registered.root, HOURS, SEALED_DAYS, registered.context)
    assert result['phase'] == 'nonblind_frozen_annual'
    assert result['original_benchmark_run'] is False
    assert result['runtime_freeze_signature'] == registered.freeze['signature']
    with pytest.raises(BeforeNumeric):
        selective_data.selective_inputs(registered.root, HOURS, sealed_days=SEALED_DAYS,
                                       annual_data_context=registered.context)
    assert len(calls) == 1


@pytest.mark.parametrize('manual_stop',[False,True])
def test_real_public_runner_keeps_orchestration_outside_exact_numeric_grant(registered,monkeypatch,tmp_path,manual_stop):
    from tools.annual_program import runner
    from tools.sci_engineering.runner import source_binding
    called=[]
    class BeforeActualRuntime(RuntimeError):pass
    def before_runtime(*args,**kwargs):
        called.append(True);raise BeforeActualRuntime('Checked real data gate; no environment constructed')
    monkeypatch.setattr(runner,'ROOT',registered.root)
    monkeypatch.setattr(runner,'validate_case',lambda case:copy.deepcopy(case))
    monkeypatch.setattr(runner,'load_registry',lambda:{})
    monkeypatch.setattr(runner,'_configuration',lambda *args:copy.deepcopy(registered.context['configuration']))
    monkeypatch.setattr(runner,'required_hours',lambda case:set(HOURS))
    monkeypatch.setattr(runner,'_bindings',lambda:dict(git_commit='synthetic-public-gate',
        source=source_binding(registered.root),inputs=input_binding(registered.root)))
    monkeypatch.setattr(runner,'_build_runtime',before_runtime)
    # Real create_runtime_freeze + validate_annual_access run here. Earlier
    # driver fixtures replaced both and missed the fifth orchestration field.
    result=runner.run_case(registered.context['case'],tmp_path/'public-run',
        approval_receipt=registered.approval,
        stop_file=tmp_path/'external-stop.request' if manual_stop else None)
    assert called==[True] and result['error']['type']=='BeforeActualRuntime'
    context=json.loads((tmp_path/'public-run/ANNUAL_ACCESS_CONTEXT.json').read_text(encoding='utf8'))
    assert set(context)==api().CONTEXT_FIELDS
    assert (tmp_path/'public-run/ORCHESTRATION_FREEZE.json').is_file()


@pytest.mark.parametrize('mutation', [
    lambda f: f.context.update(case=dict(id='different', scenario={})),
    lambda f: f.context.update(configuration=dict(H2=dict(added_ups=True))),
    lambda f: f.context['runtime_freeze_receipt'].update(signature='0'*64),
    lambda f: f.context['runtime_freeze_receipt'].update(root='different-root'),
    lambda f: f.context['runtime_freeze_receipt'].update(approved=True),
    lambda f: f.context['approval_receipt'].update(calendar_is_online_policy_input=True),
    lambda f: f.context.pop('runtime_freeze_receipt'),
    lambda f: (f.root/'src/fixture.py').write_text('frozen = 2\n', encoding='utf8'),
    lambda f: (f.root/'src/new_unfrozen.py').write_text('new = 1\n', encoding='utf8'),
    lambda f: (f.root/'inputs/loads'/selective_data.FILES[0]).write_text('changed bytes\n', encoding='utf8'),
])
def test_drift_or_fake_rights_fail_before_any_numeric_parser(registered, monkeypatch, mutation):
    calls = before_numeric(monkeypatch)
    mutation(registered)
    with pytest.raises(ValueError):
        selective_data.selective_inputs(registered.root, HOURS, sealed_days=SEALED_DAYS,
                                       annual_data_context=registered.context)
    assert calls == []


def test_resigned_boolean_bypass_and_resigned_case_drift_still_fail(registered, monkeypatch):
    calls = before_numeric(monkeypatch)
    for change in (dict(frozen=True), dict(case_sha256='0'*64), dict(annual_study_is_blind=True)):
        context = copy.deepcopy(registered.context)
        context['runtime_freeze_receipt'].update(change)
        signed(context['runtime_freeze_receipt'])
        with pytest.raises(ValueError):
            selective_data.selective_inputs(registered.root, HOURS, sealed_days=SEALED_DAYS,
                                           annual_data_context=context)
    assert calls == []


@pytest.mark.parametrize('context', [None, True, {}, {'approved': True}])
def test_default_and_fake_annual_context_preserve_the_original_seal(tmp_path, monkeypatch, context):
    calls = before_numeric(monkeypatch)
    with pytest.raises(ValueError):
        selective_data.selective_inputs(tmp_path, HOURS, sealed_days=SEALED_DAYS,
                                       annual_data_context=context)
    assert calls == []


def test_old_and_annual_envelopes_are_mutually_exclusive(registered, monkeypatch):
    calls = before_numeric(monkeypatch)
    with pytest.raises(ValueError, match='exclusive'):
        selective_data.selective_inputs(registered.root, HOURS, sealed_days=SEALED_DAYS,
            release_receipt={'synthetic': True}, annual_data_context=registered.context)
    assert calls == []


@pytest.mark.parametrize('sealed', [(), (21,), tuple(day for day in SEALED_DAYS if day != 21)])
def test_annual_permission_never_changes_the_canonical_sealed_day_set(registered, monkeypatch, sealed):
    calls = before_numeric(monkeypatch)
    with pytest.raises(ValueError):
        selective_data.selective_inputs(registered.root, HOURS, sealed_days=sealed,
                                       annual_data_context=registered.context)
    assert calls == []


def test_approval_interval_cannot_be_extended_by_the_runtime_freeze(registered, monkeypatch):
    approval = data_contract.issue_approval_receipt(
        approval_question=data_contract.APPROVAL_QUESTION,
        approval_answer=data_contract.APPROVAL_ANSWER,
        parent_head=data_contract.PARENT_HEAD, source_input_sha256=input_binding(registered.root),
        requested_interval_minutes=(0., 60.),approved_at='2026-10-30T12:00:00+08:00')
    context = dict(approval_receipt=approval, case=registered.context['case'],
                   configuration=registered.context['configuration'])
    context['runtime_freeze_receipt'] = api().create_runtime_freeze(registered.root,
        approval_receipt=approval, case=context['case'], configuration=context['configuration'])
    calls = before_numeric(monkeypatch)
    with pytest.raises(ValueError):
        selective_data.selective_inputs(registered.root, HOURS, sealed_days=SEALED_DAYS,
                                       annual_data_context=context)
    assert calls == []


def test_per_file_identity_hash_is_rechecked_before_selecting_physical_values(registered, monkeypatch):
    selected = []
    monkeypatch.setattr(selective_data, '_identity', lambda *a, **k:
        (pd.DataFrame({'hour_of_year': HOURS}), '0'*64, ['hour_of_year']))
    def forbidden(*args, **kwargs):
        selected.append(True)
        pytest.fail('A changed per-file SHA must be rejected before physical parsing')
    monkeypatch.setattr(selective_data, '_selected', forbidden)
    with pytest.raises(ValueError, match='input|SHA|sha|binding'):
        selective_data.selective_inputs(registered.root, HOURS, sealed_days=SEALED_DAYS,
                                       annual_data_context=registered.context)
    assert selected == []


def synthetic_tables(monkeypatch, *, fail_after=None):
    entered = []
    def identity(path, **kwargs):
        return pd.DataFrame({'hour_of_year': HOURS}), hashlib.sha256(path.read_bytes()).hexdigest(), ['hour_of_year']
    def selected(path, identity, allowed):
        entered.append(path.name)
        if fail_after is not None and len(entered) > fail_after:
            raise RuntimeError('Synthetic parse failure must retain attempted exposure')
        return pd.DataFrame(dict(hour_of_year=allowed, synthetic_value=[1.]*len(allowed)))
    def groups(zones, primary):
        return pd.DataFrame(dict(hour_of_year=primary.hour_of_year.tolist(), P0_e_kW=[1.]*len(primary)))
    monkeypatch.setattr(selective_data, '_identity', identity)
    monkeypatch.setattr(selective_data, '_selected', selected)
    monkeypatch.setattr(selective_data, '_validate_selected', lambda *a: None)
    monkeypatch.setattr(selective_data, 'build_zone_groups', groups)
    return entered


def test_synthetic_parsing_metadata_reports_annual_exposure_without_old_final_release(registered, monkeypatch):
    entered = synthetic_tables(monkeypatch)
    hourly, zones, groups, metadata = selective_data.selective_inputs(
        registered.root, HOURS, sealed_days=SEALED_DAYS,
        annual_data_context=registered.context)
    assert len(hourly) == 8760 and len(zones) == 2 and len(groups) == 8760
    assert len(entered) == 4
    assert metadata['final_release_implemented'] is False
    assert 'm21_final_release' not in metadata
    assert metadata['annual_data_access_implemented'] is True
    annual = metadata['annual_program_access']
    assert annual['phase'] == 'nonblind_frozen_annual'
    assert annual['actual_exposure']['sealed_hours_processed'] == HOURS
    assert annual['actual_exposure']['sealed_source_hours_processed'] is True
    assert annual['actual_exposure']['original_benchmark_run'] is False
    assert metadata['sealed_numeric_rows_parsed'] == 8
    assert registered.approval['sealed_source_hours_processed'] is False


def test_partial_numeric_failure_retains_attempted_and_completed_exposure(registered, monkeypatch):
    synthetic_tables(monkeypatch, fail_after=1)
    with pytest.raises(RuntimeError) as caught:
        selective_data.selective_inputs(registered.root, HOURS, sealed_days=SEALED_DAYS,
                                       annual_data_context=registered.context)
    failure = caught.value.annual_program_access
    assert failure['status'] == 'numeric_parse_failed_or_unknown'
    assert failure['completed_numeric_files'] == [selective_data.FILES[0]]
    assert failure['attempted_numeric_file'] == selective_data.FILES[1]
    assert failure['attempted_source_hours'] == HOURS
    assert failure['actual_exposure']['sealed_source_hours_processed'] is True


def test_postparse_grouping_failure_also_retains_all_completed_exposure(registered, monkeypatch):
    entered = synthetic_tables(monkeypatch)
    def fail(*args, **kwargs):
        raise RuntimeError('Synthetic grouping failure after numeric parsing')
    monkeypatch.setattr(selective_data, 'build_zone_groups', fail)
    with pytest.raises(RuntimeError) as caught:
        selective_data.selective_inputs(registered.root, HOURS, sealed_days=SEALED_DAYS,
                                       annual_data_context=registered.context)
    failure = caught.value.annual_program_access
    assert failure['completed_numeric_files'] == list(selective_data.FILES)
    assert failure['actual_exposure']['sealed_hours_processed'] == HOURS
    assert entered == list(selective_data.FILES)


def test_legacy_initializer_forwards_only_preinstalled_annual_context(tmp_path, monkeypatch):
    from tools.autonomous_dispatch import environment
    received = []
    def stop(*args, **kwargs):
        received.append(kwargs.get('annual_data_context'))
        raise BeforeNumeric('Stop before any plant loading')
    monkeypatch.setattr(selective_data, 'selective_inputs', stop)
    context = {'synthetic_context_envelope': True}
    for envelope in (None, context):
        obj = environment.SelectiveHospitalIESEnv.__new__(environment.SelectiveHospitalIESEnv)
        if envelope is not None:
            obj.annual_program_data_context = envelope
        with pytest.raises(BeforeNumeric):
            environment.SelectiveHospitalIESEnv.__init__(obj, tmp_path,
                allowed_hours=[1], sealed_days=SEALED_DAYS)
    assert received == [None, context]
