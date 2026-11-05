"""Pure package gates for the explicitly registered annual engineering owner."""
import hashlib
import json
import os
from pathlib import Path
import importlib.util

_receipt_spec=importlib.util.spec_from_file_location(__name__+'_engineering_receipts',
    Path(__file__).resolve().parent/'engineering_receipts.py')
_receipts=importlib.util.module_from_spec(_receipt_spec);_receipt_spec.loader.exec_module(_receipts)
validated_numerical_profile=_receipts.validated_numerical_profile
canonical_profile=_receipts.canonical_profile
ENGINEERING_MATCH_SCHEMA=_receipts.ENGINEERING_MATCH_SCHEMA
comparison_policy=_receipts.comparison_policy

MODEL_ID = 'annual_responsive_dynamic_bridge_20261104.v1'
OWNER_SCHEMA = 'hospital_ies.annual_dynamic.accepted_engineering_owner.v1'
MATCH_SCHEMA = 'hospital_ies.annual_dynamic.strict_recovery_comparison.v1'
AUDIT_SCHEMA = 'hospital_ies.annual_dynamic.engineering_audit.v1'


def execution_contract():
    return dict(model_id=MODEL_ID, owner_contract=OWNER_SCHEMA, load_cadence='hourly_original_held',
                dispatch_minutes=15, physical_max_segment_minutes=1)


def require_case(case):
    revision = case.get('execution_revision', {})
    if any(revision.get(key) != value for key, value in execution_contract().items()) or not isinstance(revision.get('declared_calendar'), list):
        raise ValueError('Explicit current annual engineering CASE profile is required')
    validated_numerical_profile(case)
    return case


def require_result(result):
    if result.get('execution_model_id') != MODEL_ID or result.get('annual_dynamic_owner_contract') != OWNER_SCHEMA or not isinstance(result.get('files'), dict):
        raise ValueError('Old/mixed producer cannot qualify the current annual engineering execution')
    return result


def require_match(result):
    if result.get('numerical_profile') is not None:
        if (result.get('schema') != ENGINEERING_MATCH_SCHEMA
                or result.get('execution_model_id') != MODEL_ID or result.get('annual_dynamic_owner_contract') != OWNER_SCHEMA
                or result.get('numerical_profile') != 'engineering_fast_v1'
                or result.get('numerical_execution') != canonical_profile()
                or result.get('comparison_policy') != comparison_policy()
                or result.get('status') != 'engineering_match' or result.get('difference_count') != 0
                or result.get('physical_elapsed_fields_retained') is not True
                or result.get('dynamic_state_and_queues_compared') is not True
                or result.get('critical_service_compared') is not True
                or result.get('event_and_queue_clocks_strict') is not True
                or result.get('unregistered_numeric_fields_strict') is not True):
            raise ValueError('Registered profile-aware engineering recovery comparison is required')
        return result
    if (result.get('schema') != MATCH_SCHEMA or result.get('execution_model_id') != MODEL_ID
            or result.get('annual_dynamic_owner_contract') != OWNER_SCHEMA
            or result.get('status') != 'exact_match' or result.get('difference_count') != 0
            or result.get('maximum_absolute_numeric_difference') != 0.
            or result.get('physical_elapsed_fields_retained') is not True
            or result.get('dynamic_state_and_queues_compared') is not True):
        raise ValueError('New-owner exact streaming recovery comparison is required')
    return result


def _sha(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024), b''): value.update(block)
    return value.hexdigest()


def _inventory(root):
    root = Path(root).resolve(); chain = hashlib.sha256(); files = dirs_count = total = 0
    def fail(error): raise error
    for current, dirs, names in os.walk(root, topdown=True, onerror=fail, followlinks=False):
        dirs.sort(); names.sort()
        for name, kind in [(name,'directory') for name in dirs]+[(name,'file') for name in names]:
            path = Path(current)/name
            if path.is_symlink() or not path.resolve().is_relative_to(root): raise ValueError('Linked inventory member')
            row = dict(path=path.relative_to(root).as_posix(),kind=kind)
            if kind == 'file':
                size=path.stat().st_size; row.update(bytes=size,sha256=_sha(path)); files+=1; total+=size
            else: dirs_count+=1
            chain.update((json.dumps(row,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode('utf8'))
    return dict(root=str(root),file_count=files,directory_count=dirs_count,total_file_bytes=total,
        inventory_sha256=chain.hexdigest(),coverage='all_files_and_directories_no_exclusions',
        includes_git_and_files_outside_scientific_binding=True)


def verify_account(package, account, producer, result):
    package, account, producer = [Path(path).resolve() for path in (package,account,producer)]
    source=package/'source'; script=package/'scripts/audit_engineering_year.py'
    numerical=result.get('numerical_execution')
    engineering=numerical is not None
    if engineering and (numerical!=canonical_profile() or result.get('numerical_profile')!=numerical['profile']):
        raise ValueError('Account numerical profile is not the registered canonical policy')
    expected_dependency_mode='profile_aware_source_formula_adapter' if engineering else 'unchanged_source_dependency'
    if (result.get('schema') != AUDIT_SCHEMA or result.get('status') != 'passed'
            or result.get('source') != str(source) or result.get('producer') != str(producer)
            or result.get('script_sha256') != _sha(script)
            or result.get('source_bytes_unchanged') is not True or result.get('original_bytes_unchanged') is not True
            or result.get('legacy_compiled_native_primal_qualified') is not False
            or result.get('binding',{}).get('execution_model_id') != MODEL_ID
            or result.get('binding',{}).get('owner_contract') != OWNER_SCHEMA
            or result.get('coverage',{}).get('requested_coverage_complete') is not True
            or result.get('audit_dependency',{}).get('mode') != expected_dependency_mode):
        raise ValueError('New engineering account does not bind its producer/source/script/owner')
    counts=result.get('counts')
    if not isinstance(counts,dict) or set(counts)!={'e','c','steam'}:
        raise ValueError('Engineering carrier counts are incomplete')
    for row in counts.values():
        if (not isinstance(row,dict) or type(row.get('passed')) is not int or row['passed']<=0
                or set(row)-{'passed','failed','unknown'}
                or any(type(value) is not int or value < 0 for value in row.values())
                or row.get('failed',0) or row.get('unknown',0)):
            raise ValueError('Engineering carrier count failed or remains unknown')
    dependency=result['audit_dependency']; native=source/'tools/cooling_dynamics_20261104/audit_campaign.py'
    if dependency.get('path') != str(native) or dependency.get('sha256') != _sha(native):
        raise ValueError('Original engineering formula dependency changed')
    if engineering:
        adapter=package/'scripts/engineering_audit_rows.py'; policy=package/'scripts/engineering_receipts.py'
        if (dependency.get('adapter_path')!=str(adapter) or dependency.get('adapter_sha256')!=_sha(adapter)
                or dependency.get('policy_helper_path')!=str(policy) or dependency.get('policy_helper_sha256')!=_sha(policy)
                or dependency.get('numerical_profile')!=numerical['profile']
                or dependency.get('instantaneous_rate_tolerance')!=numerical['tolerances']['physical_rows_bounds']
                or dependency.get('balance_rate_tolerance')!=1e-4 or dependency.get('inventory_tolerance_kWh')!=1e-7
                or dependency.get('clock_tolerance_minutes')!=1e-8):
            raise ValueError('Declared engineering audit adapter/policy/tolerance binding differs')
    for label, root in (('SOURCE',source),('PRODUCER',producer)):
        before=result.get('inventories',{}).get('before',{}).get(label)
        after=result.get('inventories',{}).get('after',{}).get(label)
        if not isinstance(before,dict) or before!=after or before!=_inventory(root):
            raise ValueError('Full current preservation inventory coverage differs: '+label)
        for suffix in ('BEFORE','AFTER'):
            if _sha(account/(label+'_'+suffix+'.jsonl')) != before.get('inventory_sha256'):
                raise ValueError('Original full preservation inventory bytes differ')
    return True
