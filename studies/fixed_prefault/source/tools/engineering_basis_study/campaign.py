"""New-study plan and evidence gates; original execution/accounting preserved."""
from __future__ import annotations

import copy
import gzip
import json
import os
from pathlib import Path

from tools.operating_rules_study.campaign import (identity, read, sha, utc, require_audit,
                                                 verify_completed_run as original_verify)
from tools.sci_engineering.design import digest
from . import STUDY_ID
from .registration import load_registry
from .validation import preflight

ROOT = Path(__file__).resolve().parents[2]


def register_plan(case_files, destination, *, study_root, max_attempts=512, max_wall_hours=24.):
    from .registration import number
    limit = number(max_attempts, 'actual start budget', low=1., high=512.)
    if not limit.is_integer():
        raise ValueError('Actual start budget must be an integer')
    wall = number(max_wall_hours, 'experiment wall hours', low=.000001, high=24.)
    destination, base = Path(destination).resolve(), Path(study_root).resolve()
    if destination.exists():
        raise FileExistsError('Use a fresh plan file')
    if os.name == 'nt' and base.drive.upper() not in ('D:', 'E:'):
        raise ValueError('Scientific outputs must remain on D/E')
    registry, items, seen = load_registry(), [], set()
    for path in map(Path, case_files):
        case = preflight(read(path), registry)
        if case['id'] in seen:
            raise ValueError('Duplicate registered actual case')
        seen.add(case['id'])
        items.append(dict(case_file=str(path.resolve()), case_sha256=sha(path), case_id=case['id'],
            method=case['engineering_basis_study']['method'], output=str(base/'runs'/case['id'])))
    if not items:
        raise ValueError('An empty plan is not a completed experiment')
    plan = dict(study_id=STUDY_ID, created_utc=utc(), identity=identity(), items=items,
        study_root=str(base), max_actual_attempts=int(limit), max_wall_hours=wall, physical_workers=1,
        validation_module='tools.engineering_basis_study.validation',
        source_history_scope='same-year exposed conditional study; no independent clinical/annual claim',
        continue_only_when='complete actual process with positive observable balances and no definite meter inconsistency')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open('x', encoding='utf8') as stream:
        json.dump(plan, stream, ensure_ascii=False, allow_nan=False, indent=2)
    return plan


def verify_completed_run(item, plan, plan_path, *, receipt=None):
    if plan.get('study_id') != STUDY_ID or plan.get('validation_module') != 'tools.engineering_basis_study.validation':
        raise ValueError('Wrong named study or validation module')
    if digest(read(plan_path)) != digest(plan):
        raise RuntimeError('Passed plan bytes differ from the frozen plan file')
    if plan.get('identity') != identity():
        raise RuntimeError('Frozen source/input identity drift before completion verification')
    if item.get('case_sha256') != sha(item['case_file']):
        raise RuntimeError('Frozen case bytes changed before completion verification')
    case = preflight(read(item['case_file']))
    if case['engineering_basis_study']['method'] != item['method']:
        raise ValueError('Plan method and canonical actual case differ')
    saved = read(Path(item['output'])/'CASE.json')
    from .validation import trusted_worker_registration
    trusted_worker_registration(saved)
    audit = original_verify(item, plan, plan_path, receipt=receipt)
    with gzip.open(Path(item['output'])/'trajectory.json.gz', 'rt', encoding='utf8') as stream:
        payload = json.load(stream)
    from .steam_meter import audit_steam_meter
    meter = audit_steam_meter(payload, bindings=read(Path(item['output'])/'BINDINGS.json'),
        producer_directory=Path(item['output']), source_root=ROOT)
    audit = copy.deepcopy(audit)
    audit['engineering_basis_study'] = dict(study_id=STUDY_ID,
        case_scenario_sha256=digest(case['scenario']), boiler_restart_evidence=case['engineering_basis_study']['boiler_restart_evidence'])
    audit['accepted_primal_steam_meter'] = meter
    # Action-only evidence remains documented. A balance remainder is never a meter.
    audit['balances']['steam']['direct_action_full_closure_check'] = audit['balances']['steam']['full_closure_check']
    if meter['status'] in ('passed', 'failed'):
        audit['balances']['steam']['full_closure_check'] = 'pass' if meter['status'] == 'passed' else 'fail'
        audit['balances']['steam']['closure_source'] = 'producer-bound accepted native primal and actual service'
        audit['balances']['steam']['primal_meter_summary'] = meter['summary']
    from tools.operating_rules_study.ledger import _audit_status
    audit = _audit_status(audit)
    require_audit(audit)
    return audit
