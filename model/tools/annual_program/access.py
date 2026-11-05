"""Opt-in annual numeric-access audits after a concrete runtime freeze.

These signatures bind local scientific identity, not cryptographic account
permissions. Hashing source/input bytes does not parse numeric input values.
The original final-blind receipt and canonical sealed days remain separate.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import json
from numbers import Integral
from pathlib import Path

from tools.sci_engineering.design import digest, SEALED_DAYS
from tools.sci_engineering.runner import source_binding, input_binding
from . import MODEL_ID
from .data_contract import validate_approval_receipt, validate_requested_hours


SCHEMA = 'hospital_ies.annual_program.runtime_data_freeze.v1'
CONTEXT_FIELDS = frozenset(('approval_receipt', 'runtime_freeze_receipt', 'case', 'configuration'))


def _recording_time(value):
    value = datetime.now(timezone.utc).isoformat() if value is None else value
    if not isinstance(value, str):
        raise ValueError('A timezone-aware freeze recording timestamp is required')
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as exc:
        raise ValueError('Invalid freeze recording timestamp') from exc
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError('Freeze recording timestamp must include a UTC offset')
    return stamp.astimezone(timezone.utc).isoformat()


def _object(value, noun):
    if not isinstance(value, dict) or not value:
        raise ValueError('An explicit nonempty '+noun+' is required')
    try:
        # Round-trip prevents hidden mutable/custom objects and nonfinite values.
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
        result = json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise ValueError(noun+' must contain finite canonical JSON values') from exc
    if result != value:
        raise ValueError(noun+' is not canonical JSON')
    return result


def create_runtime_freeze(root, *, approval_receipt, case, configuration, frozen_at=None):
    """Freeze actual source/input bytes and the exact runtime case/configuration.

    Call only after finishing permitted implementation and policy choices. The
    runner must later match its real scene/configuration to this context; this
    helper never constructs an environment or reads any numeric source rows.
    """
    root = Path(root).resolve()
    inputs = input_binding(root)
    approval = validate_approval_receipt(approval_receipt, source_input_sha256=inputs)
    sources = source_binding(root)
    if not sources:
        raise ValueError('A concrete runtime code/source inventory is required')
    checked_case, checked_config = _object(case, 'case'), _object(configuration, 'configuration')
    scene = checked_case.get('scenario')
    if not isinstance(scene, dict) or not scene:
        raise ValueError('The frozen case must contain its exact runtime scenario')
    if scene.get('is_final_blind') or scene.get('original_final12_evaluated'):
        raise ValueError('Annual permission does not execute the original final-blind benchmark')
    receipt = dict(schema=SCHEMA, model_id=MODEL_ID, root=str(root),
        frozen_at_utc=_recording_time(frozen_at),
        approval_receipt_signature=approval['signature'],
        case_sha256=digest(checked_case), configuration_sha256=digest(checked_config),
        source_sha256=sources, source_binding_sha256=digest(sources),
        input_sha256=inputs, input_binding_sha256=digest(inputs),
        annual_study_is_blind=False, original_benchmark_run=False,
        calendar_is_online_policy_input=False,
        parameter_feedback_from_sealed_days_allowed=False)
    receipt['signature'] = digest(receipt)
    return receipt


def validate_annual_access(root, allowed_hours, sealed_days, annual_data_context):
    """Fail before parsing values if any approval, code, input or case drifts."""
    if not isinstance(annual_data_context, dict) or set(annual_data_context) != CONTEXT_FIELDS:
        raise ValueError('Exact annual approval and runtime freeze context is required')
    days = list(sealed_days)
    if (any(isinstance(day, bool) or not isinstance(day, Integral) for day in days)
            or len(days) != len(set(days)) or sorted(days) != list(SEALED_DAYS)):
        raise ValueError('Annual access retains the canonical original twelve sealed days')
    context = annual_data_context
    freeze = context['runtime_freeze_receipt']
    if not isinstance(freeze, dict):
        raise ValueError('An actual source/case/configuration freeze receipt is required')
    try:
        expected = create_runtime_freeze(root, approval_receipt=context['approval_receipt'],
            case=context['case'], configuration=context['configuration'],
            frozen_at=freeze['frozen_at_utc'])
    except (KeyError, TypeError) as exc:
        raise ValueError('Incomplete frozen annual data-access context') from exc
    if freeze != expected:
        raise ValueError('Runtime freeze differs from current code/input/case/configuration binding')
    hours = validate_requested_hours(context['approval_receipt'], allowed_hours,
                                     source_input_sha256=expected['input_sha256'])
    return dict(phase='nonblind_frozen_annual', annual_study_is_blind=False,
        original_benchmark_run=False, allowed_hours=hours, sealed_days=sorted(days),
        approval_receipt_signature=expected['approval_receipt_signature'],
        runtime_freeze_signature=expected['signature'],
        source_binding_sha256=expected['source_binding_sha256'],
        input_binding_sha256=expected['input_binding_sha256'],
        case_sha256=expected['case_sha256'], configuration_sha256=expected['configuration_sha256'],
        calendar_is_online_policy_input=False,
        parameter_feedback_from_sealed_days_allowed=False)
