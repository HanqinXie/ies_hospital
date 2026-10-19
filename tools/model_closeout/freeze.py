"""Bind the tested M19 model and finite C19 policy before a comparison."""
from __future__ import annotations
import argparse
import copy
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

DEFAULT_EVALUATION_CONTRACT = dict(
    schema='hospital_ies.v19.evaluation_contract.v1',
    main_window_minutes=[0.,600.], registered_full_window='[0, registered duration)',
    normal_costs='Separate 24/48/72-hour windows, never pooled',
    metrics=['P0_P1_carrier_ENS','material_duration','longest_interruption',
        'recovery_and_recurrence','P2_P3_actual_service','actual_sources',
        'grid_gas_and_DG_shaft_energy','actual_energy_cost','ex_post_emissions'],
    material_thresholds='Unchanged inherited source-bound analyzer thresholds',
    class_mapping=dict(L1='L1',L2='L3',L3='L2',L4='L4b'),
    propagation_label_is_not_an_actual_trip=True,
    emission_semantics='Ex-post grid/gas subtotal plus conditional diesel cases; no CO2 objective',
    original_final12='sealed', independent_confirmation_requires_support_certificate=True)


def resolved_evaluation_contract(value=None):
    from .analysis import _registry
    registry,path=_registry(r.RUN/'references/EMISSIONS_FACTOR_REGISTRY.json')
    if (registry.get('main_case_id') not in registry['cases']
            or any(case.get('is_dispatch_objective') is not False for case in registry['cases'].values())):
        raise ValueError('Emissions registry must identify its main case and remain a non-objective evaluation')
    result=copy.deepcopy(DEFAULT_EVALUATION_CONTRACT if value is None else value)
    result['emissions']=dict(registry_path=path.relative_to(r.ROOT).as_posix(),
        registry_sha256=r.sha(path),main_case_id=registry['main_case_id'],
        case_canonical_sha256=copy.deepcopy(registry['case_canonical_sha256']),
        boundary=registry['boundary'],is_dispatch_objective=False,
        diesel_total_is_conditional=True)
    return result


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--name', required=True)
    p.add_argument('--tests', required=True)
    p.add_argument('--main', action='store_true')
    p.add_argument('--model-config',default='M19_MODEL_CONFIG.json')
    p.add_argument('--evaluation-config')
    a = p.parse_args()
    if not a.name.replace('_','').isalnum() or not a.tests.replace('_','').isalnum():
        raise ValueError('Use fresh simple freeze and test gate names')
    test = r.read(B / 'logs' / (a.tests+'.command.json'))
    if test['exit_code'] != 0:
        raise ValueError('Affected regression must actually pass')
    from .gate import test_binding
    gate=r.read(B/'test_gates'/(a.tests+'.json'))
    binding,tests=r.source_binding(),test_binding()
    validate_test_gate(gate,binding,tests)
    baseline = r.read(B / 'baseline/initial.json')
    issues = [name for name, expected in baseline['files'].items() if r.sha(r.ROOT/name) != expected]
    if issues:
        raise ValueError('Original protected bytes changed: '+str(issues))
    result = dict(kind='v19_final_shared_model_freeze' if a.main else 'v19_development_source_freeze',
        utc=datetime.now(timezone.utc).isoformat(), source_sha256=binding,
        model_id='M19_model_closeout', model_config=r.model_contract(r.MODEL_ID,r.read(B/a.model_config))[1],
        evaluation_contract=resolved_evaluation_contract(r.read(B/a.evaluation_config) if a.evaluation_config else None),
        policies=r.policy_specs(), affected_regression_command=test,
        source_bound_test_gate=gate,
        original_protected_baseline_sha256=r.sha(B/'baseline/initial.json'),
        unchanged_original_file_count=len(baseline['files']),
        old80_role='seen_regression', original_final12='unopened', no_MPC=True,
        no_future_exogenous_scripts=True, no_training=True,
        physical_capacities_loads_protection_and_service_thresholds_unchanged=True,
        resource_limits=d.RESOURCE_LIMITS,
        final_archive_hard_limit_bytes=200000000,
        selection_scope='Old80 are seen regression; separate small support-isolated confirmation only after this freeze')
    if r.source_binding()!=binding or test_binding()!=tests:
        raise ValueError('Source or tests changed during protected baseline validation; rerun the gate')
    result['signature'] = d.digest(result)
    with (B/(a.name+'.json')).open('x', encoding='utf8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps(dict(freeze=a.name, signature=result['signature'], source_files=len(result['source_sha256']))))

if __name__ == '__main__':
    main()
