"""Annual permission and half-open source clocks; no numeric input is read."""
import copy
import hashlib
import importlib
import json

import pytest


QUESTION = '完整8760小时必然使用原封存的12天负荷数据。是否按已写好的年度协议执行：允许这些小时参加本次冻结年度计算，原12个基准场景及旧结果保持原样；本次结果标为非盲测，并如实记录这些日期已被年度运行使用？'
ANSWER = '允许按该年度协议使用全部8760小时'
PARENT = 'e8d9e11d29cd9802d7b2f22fa7eae7e47f04170b'
STAMP = '2026-10-30T06:00:00+00:00'
INPUTS = {'inputs/loads/fixture.csv': 'a'*64, 'requirements.txt': 'b'*64}
YEAR = 525600.


def api():
    try:
        return importlib.import_module('tools.annual_program.data_contract')
    except ModuleNotFoundError as exc:
        if exc.name not in ('tools.annual_program', 'tools.annual_program.data_contract'):
            raise
        pytest.fail('Explicit annual data authorization and endpoint contract is missing', pytrace=False)


def issue(**overrides):
    values = dict(approval_question=QUESTION, approval_answer=ANSWER, parent_head=PARENT,
                  source_input_sha256=copy.deepcopy(INPUTS), approved_at=STAMP,
                  requested_interval_minutes=(0., YEAR))
    values.update(overrides)
    return api().issue_approval_receipt(**values)


def resign(value):
    value['signature'] = hashlib.sha256(json.dumps(
        {k: v for k, v in value.items() if k != 'signature'}, sort_keys=True,
        separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf8')).hexdigest()
    return value


def test_receipt_binds_both_exact_approved_messages_and_separates_permission_from_exposure():
    receipt = issue()
    assert receipt['approval_question'] == QUESTION
    assert receipt['approval_answer'] == ANSWER
    assert receipt['approval_question_sha256'] == hashlib.sha256(QUESTION.encode('utf8')).hexdigest()
    assert receipt['approval_answer_sha256'] == hashlib.sha256(ANSWER.encode('utf8')).hexdigest()
    assert receipt['parent_head'] == PARENT
    assert receipt['source_input_sha256'] == INPUTS
    assert receipt['approved_date_Asia_Shanghai'] == '2026-10-30'
    assert receipt['requested_interval_minutes'] == [0., YEAR]
    assert receipt['source_support']['source_hour_first'] == 1
    assert receipt['source_support']['source_hour_last'] == 8760
    assert receipt['source_support']['source_hour_count'] == 8760
    assert receipt['original_benchmark_run'] is False
    assert receipt['old_results_read_only'] is True
    assert receipt['annual_study_is_blind'] is False
    assert receipt['sealed_source_hours_authorized'] is True
    assert receipt['sealed_source_hours_processed'] is False
    assert receipt['calendar_is_online_policy_input'] is False
    assert receipt['parameter_feedback_from_sealed_days_allowed'] is False
    assert api().validate_approval_receipt(receipt, source_input_sha256=INPUTS, parent_head=PARENT) == receipt


@pytest.mark.parametrize('overrides', [
    {'approval_question': QUESTION+' '}, {'approval_answer': ANSWER+' '},
    {'parent_head': 'c'*40}, {'approved_at': '2026-10-29T12:00:00+08:00'},
    {'approved_at': '2026-10-30T06:00:00'}, {'approved_at': ''}, {'approved_at': False},
    {'source_input_sha256': {}},
    {'source_input_sha256': {'inputs/x.csv': 'not-a-sha'}},
    {'source_input_sha256': {'../inputs/x.csv': 'a'*64}},
    {'source_input_sha256': {'D:/inputs/x.csv': 'a'*64}},
])
def test_unapproved_text_parent_date_or_invalid_input_binding_cannot_issue(overrides):
    with pytest.raises(ValueError):
        issue(**overrides)


@pytest.mark.parametrize('field,value', [
    ('approval_question', QUESTION+' changed'), ('approval_answer', ANSWER+' changed'),
    ('parent_head', 'c'*40), ('original_benchmark_run', True),
    ('annual_study_is_blind', True), ('sealed_source_hours_processed', True),
    ('calendar_is_online_policy_input', True),
    ('parameter_feedback_from_sealed_days_allowed', True),
    ('sealed_days', []), ('approved_date_Asia_Shanghai', '2026-10-31'),
    ('requested_interval_minutes', [0., YEAR+1]), ('unregistered_extra_right', True),
])
def test_even_resigned_receipt_cannot_broaden_the_fixed_authorization(field, value):
    receipt = issue()
    receipt[field] = value
    resign(receipt)
    with pytest.raises(ValueError):
        api().validate_approval_receipt(receipt, source_input_sha256=INPUTS, parent_head=PARENT)


def test_receipt_rejects_signature_and_current_input_drift_and_is_defensively_copied():
    receipt = issue()
    receipt['signature'] = '0'*64
    with pytest.raises(ValueError):
        api().validate_approval_receipt(receipt, source_input_sha256=INPUTS, parent_head=PARENT)
    receipt = issue()
    changed = dict(INPUTS, **{'inputs/loads/fixture.csv': 'c'*64})
    with pytest.raises(ValueError):
        api().validate_approval_receipt(receipt, source_input_sha256=changed, parent_head=PARENT)
    checked = api().validate_approval_receipt(receipt, source_input_sha256=INPUTS, parent_head=PARENT)
    checked['source_input_sha256']['inputs/loads/fixture.csv'] = 'd'*64
    assert receipt['source_input_sha256'] == INPUTS


@pytest.mark.parametrize('start,end,first,last,count', [
    (0., YEAR, 1, 8760, 8760), (YEAR-15., YEAR, 8760, 8760, 1),
    (0., 60., 1, 1, 1), (59., 60., 1, 1, 1), (59., 60.25, 1, 2, 2),
    (60., 120., 2, 2, 1),
])
def test_half_open_integration_support_never_reserves_an_endpoint_source_hour(start, end, first, last, count):
    support = api().requested_interval_support(start, end)
    assert support == dict(raw_start_minute=start, raw_end_minute=end,
                          integration_interval='[start,end)', source_hour_first=first,
                          source_hour_last=last, source_hour_count=count,
                          terminal_source_hour_reserved=False)


@pytest.mark.parametrize('start,end', [
    (-.01, 15.), (0., YEAR+.01), (YEAR, YEAR), (15., 0.),
    (0., 0.), (False, 15.), (0., True), (float('nan'), 15.),
    (0., float('inf')), ('0', 15.),
])
def test_invalid_or_outside_integration_intervals_are_rejected_without_clamping(start, end):
    with pytest.raises(ValueError):
        api().requested_interval_support(start, end)


@pytest.mark.parametrize('minute,hour', [(0., 1), (59.999, 1), (60., 2),
                                        (YEAR-60., 8760), (YEAR-.000001, 8760)])
def test_source_hour_at_preserves_exact_clock_and_last_hour(minute, hour):
    assert api().source_hour_at(minute) == hour


@pytest.mark.parametrize('minute,terminal', [
    (YEAR, False), (YEAR+.000001, False), (YEAR+.000001, True),
    (-.000001, False), (True, False), (float('nan'), False),
    (float('inf'), False), (YEAR-.000001, True), (0., True),
])
def test_only_exact_year_end_terminal_export_may_omit_a_source_read(minute, terminal):
    with pytest.raises(ValueError):
        api().source_hour_at(minute, terminal_export=terminal)


def test_exact_terminal_export_is_source_free_and_calls_no_loader():
    assert api().source_hour_at(YEAR, terminal_export=True) is None


def test_requested_hours_must_match_exact_receipt_support_and_exposure_is_actual():
    receipt = issue(requested_interval_minutes=(21.*1440., 22.*1440.))
    first = 21*24+1
    assert api().validate_requested_hours(receipt, [first, first+23], source_input_sha256=INPUTS) == [first, first+23]
    for invalid in ([first-1], [first+24], [first, first], [True], [1.5], []):
        with pytest.raises(ValueError):
            api().validate_requested_hours(receipt, invalid, source_input_sha256=INPUTS)
    empty = api().exposure_record(receipt, [], source_input_sha256=INPUTS)
    assert empty['sealed_source_hours_processed'] is False
    actual = api().exposure_record(receipt, [first], source_input_sha256=INPUTS)
    assert actual['sealed_source_hours_processed'] is True
    assert actual['sealed_hours_processed'] == [first]
    assert actual['sealed_days_processed'] == [21]
    assert actual['original_benchmark_run'] is False
    assert actual['original_final12_globally_unexposed_claim_allowed'] is False
    assert actual['approval_receipt_signature'] == receipt['signature']
    assert receipt['sealed_source_hours_processed'] is False


def test_runtime_interval_cannot_expand_authorized_minutes_within_the_same_source_hour():
    receipt = issue(requested_interval_minutes=(60., 75.))
    assert api().validate_integration_interval(receipt, 60., 75., source_input_sha256=INPUTS) == api().requested_interval_support(60., 75.)
    assert api().validate_integration_interval(receipt, 65., 70., source_input_sha256=INPUTS) == api().requested_interval_support(65., 70.)
    for start, end in ((60., 76.), (59., 75.), (60., 120.)):
        with pytest.raises(ValueError):
            api().validate_integration_interval(receipt, start, end, source_input_sha256=INPUTS)


def test_annual_receipt_cannot_be_reused_as_a_legacy_final_blind_receipt(tmp_path):
    from tools.autonomous_dispatch.selective_data import selective_inputs
    with pytest.raises(ValueError, match='unknown final release receipt schema'):
        selective_inputs(tmp_path, [21*24+1], sealed_days=api().SEALED_DAYS,
                         release_receipt=issue())


def test_unsealed_numeric_exposure_does_not_falsely_claim_sealed_hours_were_processed():
    actual = api().exposure_record(issue(), [1, 2, 8760], source_input_sha256=INPUTS)
    assert actual['sealed_source_hours_processed'] is False
    assert actual['sealed_hours_processed'] == []
    assert actual['parsed_source_hours'] == [1, 2, 8760]
    assert actual['annual_study_is_blind'] is False


def test_authorization_validation_and_terminal_export_do_not_open_any_data_file(monkeypatch):
    module = api()
    def forbidden(*args, **kwargs):
        raise AssertionError('Metadata and terminal export must not read source data')
    monkeypatch.setattr('builtins.open', forbidden)
    monkeypatch.setattr('pathlib.Path.open', forbidden)
    receipt = issue()
    assert module.validate_approval_receipt(receipt, source_input_sha256=INPUTS) == receipt
    assert module.source_hour_at(YEAR, terminal_export=True) is None
