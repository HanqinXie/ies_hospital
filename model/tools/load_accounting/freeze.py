"""Freeze M20 source, actual original-input scope, preferences and a real gate."""
from __future__ import annotations
import argparse
import copy
from datetime import datetime, timezone
import json
from pathlib import Path

from . import runner as r
from . import data_design as d
from .gate import test_binding
from tools.model_closeout.freeze import validate_test_gate

DEFAULT_EVALUATION_CONTRACT = dict(
    schema='hospital_ies.v20.evaluation_contract.v1',
    main_window_minutes=[0.,600.], registered_full_window='[0, registered duration)',
    normal_costs='Separate 24/48/72-hour windows, never pooled',
    metrics=['P0_P1_carrier_requested_delivered_ENS','material_duration','longest_interruption',
        'active_disconnection_vs_connected_unserved','recovery_and_recurrence','P2_P3_actual_service',
        'actual_sources','grid_gas_and_DG_shaft_energy','actual_energy_cost','ex_post_emissions'],
    material_thresholds='Unchanged inherited source-bound analyzer thresholds',
    class_mapping=dict(L1='L1',L2='L3',L3='L2',L4='L4b'),
    original_final12='sealed',fixed_eight_backgrounds='seen_fixed_background_recheck',
    independent_confirmation_claim=False,emissions_is_dispatch_objective=False)


def resolved_evaluation_contract(value=None):
    from .analysis import _registry
    registry,path = _registry()
    result = copy.deepcopy(DEFAULT_EVALUATION_CONTRACT if value is None else value)
    if any(case.get('is_dispatch_objective') is not False for case in registry['cases'].values()):
        raise ValueError('Emissions remains ex-post evaluation only')
    result['emissions'] = dict(registry_path=path.relative_to(r.ROOT).as_posix(),registry_sha256=r.sha(path),
        main_case_id=registry['main_case_id'],case_canonical_sha256=registry['case_canonical_sha256'],
        boundary=registry['boundary'],is_dispatch_objective=False,diesel_total_is_conditional=True)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--name',required=True)
    parser.add_argument('--tests',required=True)
    parser.add_argument('--main',action='store_true')
    parser.add_argument('--model-config',default='M20_MODEL_CONFIG.json')
    parser.add_argument('--evaluation-config')
    parser.add_argument('--provenance',help='Optional new original-input/start-commit provenance JSON in this run')
    a = parser.parse_args(argv)
    if not all(x.replace('_','').isalnum() for x in (a.name,a.tests)):
        raise ValueError('Fresh simple freeze/gate names required')
    destination = r.RUN/(a.name+'.json')
    if destination.exists():
        raise FileExistsError(destination)
    command = r.read(r.RUN/'logs'/(a.tests+'.command.json'))
    gate = r.read(r.RUN/'test_gates'/(a.tests+'.json'))
    binding, tests = r.source_binding(),test_binding()
    validate_test_gate(gate,binding,tests)
    if command['exit_code'] != 0:
        raise ValueError('Actual affected regression command must pass')
    inventory = d.runtime_input_inventory(r.ROOT)
    # Reconcile original CSV identities to their original export receipt.
    receipt = r.read(r.ROOT/'inputs/reference/provenance.json')
    by_name = {row['path']:row['sha256'] for row in inventory}
    for name,expected in receipt['corrected_csv_sha256'].items():
        if by_name.get('inputs/loads/'+name) != expected:
            raise ValueError('Original load CSV changed: '+name)
    provenance = None
    if a.provenance:
        path = (r.RUN/a.provenance).resolve()
        if not path.is_relative_to(r.RUN.resolve()):
            raise ValueError('Provenance must be a new record within this V20 run')
        provenance = dict(path=path.relative_to(r.ROOT).as_posix(),sha256=r.sha(path),record=r.read(path))
    result = dict(kind='v20_final_shared_model_freeze' if a.main else 'v20_development_source_freeze',
        utc=datetime.now(timezone.utc).isoformat(),model_id=r.MODEL_ID,source_sha256=binding,
        model_config=r.model_contract(r.MODEL_ID,r.read(r.RUN/a.model_config))[1],
        runtime_inputs=inventory,load_accounting_contract_id=d.LOAD_ACCOUNTING_CONTRACT_ID,
        policies=r.policy_specs(),source_bound_test_gate=gate,affected_regression_command=command,
        evaluation_contract=resolved_evaluation_contract(r.read(r.RUN/a.evaluation_config) if a.evaluation_config else None),
        original_input_bytes_match_export_provenance=True,original_input_byte_receipt_sha256=r.sha(r.ROOT/'inputs/reference/provenance.json'),
        starting_v19_commit='01baf68c3e12d0336a5d9d54026ca479a7023b6d',v19_preservation_tag='v19-model-closeout',
        original_provenance=provenance,
        grouping_arithmetic_changed=True,raw_inputs_physically_rewritten=False,
        equipment_conversion_queue_contacts_protection_DG_budget_service_thresholds_unchanged=True,
        no_MPC=True,no_future_exogenous_scripts=True,no_training=True,original_final12='unopened',
        fixed_eight_backgrounds_are_seen_rechecks=True,new_independent_background_count=0,
        resource_limits=d.RESOURCE_LIMITS,final_archive_hard_limit_bytes=200000000)
    if r.source_binding()!=binding or test_binding()!=tests or d.runtime_input_inventory(r.ROOT)!=inventory:
        raise ValueError('Source/tests/inputs changed while freezing; retain evidence and rerun gate')
    result['signature']=d.digest(result)
    r.write(destination,result)
    print(json.dumps(dict(freeze=a.name,signature=result['signature'],source_files=len(binding),input_files=len(inventory))))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
