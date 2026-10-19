"""Bind the tested M18 model and finite C18 policy before a comparison."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from . import runner as r
from . import data_design as d

B = r.RUN

def validate_test_gate(gate,binding,test_files):
    if (gate.get('passed') is not True or gate.get('exit_code')!=0
            or gate.get('source_before')!=binding or gate.get('source_after')!=binding
            or gate.get('test_files_sha256')!=test_files
            or gate.get('test_files_after_sha256')!=test_files):
        raise ValueError('Passing test gate is not bound to current source and test files')

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--name', required=True)
    p.add_argument('--tests', required=True)
    p.add_argument('--main', action='store_true')
    a = p.parse_args()
    test = r.read(B / 'logs' / (a.tests+'.command.json'))
    if test['exit_code'] != 0:
        raise ValueError('Affected regression must actually pass')
    from .gate import test_binding
    gate=r.read(B/'test_gates'/(a.tests+'.json'))
    validate_test_gate(gate,r.source_binding(),test_binding())
    baseline = r.read(B / 'baseline/initial.json')
    issues = [name for name, expected in baseline['files'].items() if r.sha(r.ROOT/name) != expected]
    if issues:
        raise ValueError('Original protected bytes changed: '+str(issues))
    result = dict(kind='v18_final_shared_model_freeze' if a.main else 'v18_development_source_freeze',
        utc=datetime.now(timezone.utc).isoformat(), source_sha256=r.source_binding(),
        model_id='M18_execution_recovery', model_config=r.read(B/'M18_MODEL_CONFIG.json'),
        policies=r.policy_specs(), affected_regression_command=test,
        source_bound_test_gate=gate,
        original_protected_baseline_sha256=r.sha(B/'baseline/initial.json'),
        unchanged_original_file_count=len(baseline['files']),
        old80_role='seen_regression', original_final12='unopened', no_MPC=True,
        no_future_exogenous_scripts=True, no_training=True,
        physical_capacities_loads_protection_and_service_thresholds_unchanged=True,
        resource_limits=d.RESOURCE_LIMITS,
        final_archive_hard_limit_bytes=200000000,
        selection_scope='Development decisions may use old80; no unseen or global optimum claim')
    result['signature'] = d.digest(result)
    with (B/(a.name+'.json')).open('x', encoding='utf8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps(dict(freeze=a.name, signature=result['signature'], source_files=len(result['source_sha256']))))

if __name__ == '__main__':
    main()
