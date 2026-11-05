"""Pure metadata permission and source clocks for the approved annual study.

These local SHA bindings are audit controls, not account authentication. This
module neither reads numeric input nor opens the legacy final-blind benchmark.
Runtime integration must validate its current input map and frozen run plan
before using the permission; a receipt alone does not freeze a policy or model.
"""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from numbers import Integral, Real
from pathlib import PurePosixPath
import re

from . import MODEL_ID
from tools.sci_engineering.design import SEALED_DAYS


SCHEMA = 'hospital_ies.annual_program.data_approval.v1'
YEAR_HOURS = 8760
YEAR_MINUTES = YEAR_HOURS * 60.
PARENT_HEAD = 'e8d9e11d29cd9802d7b2f22fa7eae7e47f04170b'
APPROVAL_QUESTION = '完整8760小时必然使用原封存的12天负荷数据。是否按已写好的年度协议执行：允许这些小时参加本次冻结年度计算，原12个基准场景及旧结果保持原样；本次结果标为非盲测，并如实记录这些日期已被年度运行使用？'
APPROVAL_ANSWER = '允许按该年度协议使用全部8760小时'
APPROVAL_DATE = '2026-10-30'


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
        ensure_ascii=False, allow_nan=False).encode('utf8')).hexdigest()


def _text_sha(value):
    return hashlib.sha256(value.encode('utf8')).hexdigest()


def _minute(value):
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value):
        raise ValueError('Source minute must be a finite real number, not a bool')
    return float(value)


def source_hour_at(minute, *, terminal_export=False):
    """Return the exact current source hour; exact final export returns None.

    None explicitly instructs a caller to export the existing final state without
    reading demand or performing another physical solve. It is never a substitute
    for a source hour inside or outside the integration interval.
    """
    minute = _minute(minute)
    if not isinstance(terminal_export, bool):
        raise ValueError('terminal_export must be a bool')
    if terminal_export:
        if minute != YEAR_MINUTES:
            raise ValueError('Source-free terminal export requires exact source-year end')
        return None
    if not 0. <= minute < YEAR_MINUTES:
        raise ValueError('Source read must stay inside [0,8760 hours); no clamping')
    return math.floor(minute / 60.) + 1


def requested_interval_support(start_minute, end_minute):
    """Exact half-open integration support, without reserving an endpoint hour."""
    start, end = _minute(start_minute), _minute(end_minute)
    if not 0. <= start < end <= YEAR_MINUTES:
        raise ValueError('Integration interval must satisfy 0 <= start < end <= source-year end')
    first, last = math.floor(start / 60.) + 1, math.ceil(end / 60.)
    return dict(raw_start_minute=start, raw_end_minute=end,
        integration_interval='[start,end)', source_hour_first=first,
        source_hour_last=last, source_hour_count=last-first+1,
        terminal_source_hour_reserved=False)


def _input_map(values):
    if not isinstance(values, dict) or not values:
        raise ValueError('A nonempty current source input SHA-256 map is required')
    result = {}
    for path, sha in values.items():
        if not isinstance(path, str) or not path or '\\' in path or ':' in path:
            raise ValueError('Input identity must use a relative POSIX path')
        identity = PurePosixPath(path)
        if identity.is_absolute() or '..' in identity.parts or str(identity) != path:
            raise ValueError('Input identity must remain a canonical relative path')
        if not isinstance(sha, str) or re.fullmatch('[0-9a-f]{64}', sha) is None:
            raise ValueError('Input binding must contain lowercase SHA-256 values')
        result[path] = sha
    return dict(sorted(result.items()))


def _approval_time(approved_at):
    if not isinstance(approved_at, str):
        raise ValueError('Approval recording time must be an ISO timestamp')
    try:
        stamp = datetime.fromisoformat(approved_at.replace('Z', '+00:00'))
    except ValueError as exc:
        raise ValueError('Invalid approval recording timestamp') from exc
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError('Approval recording timestamp requires an explicit UTC offset')
    local_date = stamp.astimezone(timezone(timedelta(hours=8))).date().isoformat()
    if local_date != APPROVAL_DATE:
        raise ValueError('Approval date differs from the actual authorized 2026-10-30 session')
    return stamp.astimezone(timezone.utc).isoformat(), local_date


def issue_approval_receipt(*, approval_question, approval_answer, parent_head,
        source_input_sha256, requested_interval_minutes=(0., YEAR_MINUTES), approved_at=None):
    """Bind the exact approved question and answer without opening any input.

    The source map is supplied by the caller's independently hashed inventory.
    This is permission metadata; actual parsing is recorded by exposure_record.
    No receipt authorizes parameter feedback from the old reserved source days.
    """
    if approval_question != APPROVAL_QUESTION or approval_answer != APPROVAL_ANSWER:
        raise ValueError('Both exact approved messages are required for this annual protocol')
    if parent_head != PARENT_HEAD:
        raise ValueError('Annual protocol parent HEAD differs from the approved source parent')
    inputs = _input_map(source_input_sha256)
    if not isinstance(requested_interval_minutes, (tuple, list)) or len(requested_interval_minutes) != 2:
        raise ValueError('An explicit two-bound half-open requested interval is required')
    support = requested_interval_support(*requested_interval_minutes)
    approved_at, local_date = _approval_time(
        datetime.now(timezone.utc).isoformat() if approved_at is None else approved_at)
    sealed_hours = [h for h in range(support['source_hour_first'], support['source_hour_last']+1)
                    if (h-1)//24 in SEALED_DAYS]
    receipt = dict(schema=SCHEMA, model_id=MODEL_ID,
        approval_question=approval_question, approval_answer=approval_answer,
        approval_question_sha256=_text_sha(approval_question),
        approval_answer_sha256=_text_sha(approval_answer),
        approved_at_utc=approved_at, approved_date_Asia_Shanghai=local_date,
        parent_head=parent_head, source_input_sha256=inputs,
        requested_interval_minutes=[support['raw_start_minute'], support['raw_end_minute']],
        source_support=support, sealed_days=list(SEALED_DAYS),
        sealed_source_hours_authorized=bool(sealed_hours),
        sealed_source_hour_count_authorized=len(sealed_hours),
        sealed_source_hours_processed=False,
        original_benchmark_run=False, old_results_read_only=True,
        annual_study_is_blind=False, calendar_is_online_policy_input=False,
        parameter_feedback_from_sealed_days_allowed=False,
        runtime_plan_freeze_required_before_numeric_access=True,
        legacy_final_blind_release_used=False)
    receipt['signature'] = _digest(receipt)
    return receipt


def validate_approval_receipt(receipt, *, source_input_sha256, parent_head=PARENT_HEAD,
                            requested_interval_minutes=None):
    """Validate fixed rights and independently supplied current input identity.

    A caller may bind its exact planned interval too. Unexpected fields and even
    re-signed changes to the fixed rights are rejected by canonical regeneration.
    """
    if not isinstance(receipt, dict):
        raise ValueError('A complete annual protocol approval receipt is required')
    try:
        expected = issue_approval_receipt(
            approval_question=receipt['approval_question'], approval_answer=receipt['approval_answer'],
            parent_head=parent_head, source_input_sha256=source_input_sha256,
            requested_interval_minutes=(receipt['requested_interval_minutes']
                if requested_interval_minutes is None else requested_interval_minutes),
            approved_at=receipt['approved_at_utc'])
    except (KeyError, TypeError) as exc:
        raise ValueError('Incomplete annual protocol approval receipt') from exc
    if receipt != expected:
        raise ValueError('Annual receipt differs from exact approved scope or current input identity')
    return copy.deepcopy(expected)


def _hours(values, *, allow_empty=False):
    if not isinstance(values, (list, tuple, set, frozenset)) or (not values and not allow_empty):
        raise ValueError('Explicit source-hour identities are required')
    if any(isinstance(h, bool) or not isinstance(h, Integral) or not 1 <= h <= YEAR_HOURS for h in values):
        raise ValueError('Source hours must be integers inside 1..8760')
    if len(values) != len(set(values)):
        raise ValueError('Duplicate source hours are not an exact access identity')
    return sorted(map(int, values))


def validate_requested_hours(receipt, hours, *, source_input_sha256, parent_head=PARENT_HEAD):
    """Reject a numeric source access outside the exact bound interval."""
    checked = validate_approval_receipt(receipt, source_input_sha256=source_input_sha256,
                                       parent_head=parent_head)
    requested = _hours(hours)
    support = checked['source_support']
    if requested[0] < support['source_hour_first'] or requested[-1] > support['source_hour_last']:
        raise ValueError('Numeric source hours lie outside the explicitly authorized interval')
    return requested


def validate_integration_interval(receipt, start_minute, end_minute, *,
                                  source_input_sha256, parent_head=PARENT_HEAD):
    """Check actual integration bounds, including subsets of one source hour."""
    checked = validate_approval_receipt(receipt, source_input_sha256=source_input_sha256,
                                       parent_head=parent_head)
    support = requested_interval_support(start_minute, end_minute)
    authorized = checked['source_support']
    if (support['raw_start_minute'] < authorized['raw_start_minute']
            or support['raw_end_minute'] > authorized['raw_end_minute']):
        raise ValueError('Integration exceeds the exact explicitly authorized minute interval')
    return support


def exposure_record(receipt, parsed_source_hours, *, source_input_sha256, parent_head=PARENT_HEAD):
    """Describe actual parsed hour identities separately from the permission.

    The caller must provide actual parsed-hour receipts, including failed runs;
    requesting a year is not evidence that its source hours were processed.
    """
    checked = validate_approval_receipt(receipt, source_input_sha256=source_input_sha256,
                                       parent_head=parent_head)
    hours = _hours(parsed_source_hours, allow_empty=True)
    if hours:
        validate_requested_hours(checked, hours, source_input_sha256=source_input_sha256,
                                 parent_head=parent_head)
    sealed = [h for h in hours if (h-1)//24 in SEALED_DAYS]
    result = dict(schema=SCHEMA+'.actual_exposure', model_id=MODEL_ID,
        approval_receipt_signature=checked['signature'], parsed_source_hours=hours,
        sealed_hours_processed=sealed, sealed_days_processed=sorted({(h-1)//24 for h in sealed}),
        sealed_source_hours_processed=bool(sealed), original_benchmark_run=False,
        old_results_read_only=True, annual_study_is_blind=False,
        original_final12_globally_unexposed_claim_allowed=False,
        calendar_is_online_policy_input=False, parameter_feedback_from_sealed_days_allowed=False)
    result['signature'] = _digest(result)
    return result
