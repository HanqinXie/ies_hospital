"""Reuse saved outcome definitions; explicitly describe the joint interface."""
from __future__ import annotations
import copy
from pathlib import Path
from types import FunctionType
import json
from tools.state_dispatch import analysis as original
from .contracts import validate_action


def analysis_namespace(validator=validate_action):
    namespace = dict(original.__dict__)
    namespace.update(validate_action=validator, SCHEMA='autonomous_dispatch.saved_analysis.v1')
    for name, function in original.__dict__.items():
        if isinstance(function, FunctionType) and function.__module__ == original.__name__:
            rebound = FunctionType(function.__code__, namespace, function.__name__, function.__defaults__, function.__closure__)
            rebound.__kwdefaults__ = function.__kwdefaults__
            namespace[name] = rebound
    return namespace


def analyze_batch(manifest, plan, results, audit, out_dir, *, validator=validate_action, runtime_source_binding=None):
    namespace = analysis_namespace(validator)
    report = namespace['analyze_batch'](manifest, plan, results, audit, out_dir)
    from .closed_loop import _source_binding, _sha
    records = []
    for row in report['trajectory_results']:
        payload = original.previous._open_raw(Path(row['raw_path']))
        executed = {e['command_id']: e for e in payload.get('events', []) if e.get('kind') == 'command_executed'}
        for decision in payload.get('decisions', []):
            command = decision.get('command_generated') or {}
            joint = command.get('joint_dispatch')
            if not joint:
                continue
            diagnostic = joint['diagnostic']; action = joint['planned_current_action']
            lost = diagnostic.get('planned_unserved_kW', command['disconnected'])
            risk = diagnostic.get('risk_scenarios', {})
            records.append(dict(scenario_id=row['scenario_id'], policy_id=row['policy_id'],
                decision_time_min=decision['decision_time_min'], command_id=command['id'],
                planned_arrival_minute=command['arrival_minute'],
                executed_observed=command['id'] in executed,
                executed_at_minute=None if command['id'] not in executed else executed[command['id']]['time_minute'],
                variant=diagnostic['options']['variant'], support_hours=diagnostic['options']['support_hours'],
                current_planned_grid_used_kW=action['grid_import_kW'],
                command_grid_ceiling_kW=command['dispatch']['grid_import_kW'],
                gt_total_kW=sum(action['gt_electric_kW']), diesel_kW=action['diesel_kW'],
                boiler_steam_lbph=action['boiler_steam_lbph'],
                ABS_cooling_kW=action['absorption_cooling_kW'], EC_cooling_kW=sum(action['electric_chiller_cooling_kW']),
                current_planned_electric_source_total_kW=sum(action['gt_electric_kW'])+action['grid_import_kW']+action['diesel_kW'],
                planned_P0_unserved_e_h_c_json=lost[0], planned_P1_unserved_e_h_c_json=lost[1],
                planned_P2_unserved_e_h_c_json=lost[2], planned_P3_unserved_e_h_c_json=lost[3],
                commanded_P2_disconnected_e_h_c_json=command['disconnected'][2],
                commanded_P3_disconnected_e_h_c_json=command['disconnected'][3],
                connection_mode=diagnostic.get('connection_mode', 'planned_shed'),
                retention_eligible=diagnostic.get('retention_eligible', False),
                retention_selected=diagnostic.get('retention_selected', False),
                fuel_preference_excess_kW=diagnostic['fuel_excess_kW'],
                static_max_surplus_defect_kW=max((v['electric_surplus_defect_kW'] for v in risk.values()), default=0.),
                static_risk_case_count=len(risk), planning_is_not_execution=True,
                standby_preference_requested=diagnostic.get('preserve_one_gt_standby', False),
                standby_third_gt_on_excess=diagnostic.get('standby_third_gt_on_excess'),
                standby_planned_GT_on_json=diagnostic.get('standby_planned_GT_on'),
                standby_planned_GT_on_count=diagnostic.get('standby_planned_GT_on_count'),
                standby_soft_preference_active=diagnostic.get('standby_soft_preference_active', False),
                standby_contract_id=diagnostic.get('standby_contract_id'),
                standby_is_execution_guarantee=False,
                execution_model_id=payload['execution_model_id']))
    original._write_table(Path(out_dir) / 'JOINT_DECISION_DETAILS.csv', records)
    receipt = dict(schema='autonomous_dispatch.analysis_source_binding.v1',
        wrapper_sha256=_sha(Path(__file__)), registered_runtime_sources=(runtime_source_binding or _source_binding)(),
        report_sha256=_sha(Path(out_dir) / 'SUMMARY.json'), joint_decision_count=len(records),
        saved_raw_analysis_only=True, physical_trajectories_started=0,
        legacy_six_port_projection_metrics_inapplicable_to_joint_requests=True)
    (Path(out_dir) / 'AUTONOMOUS_SOURCE_BINDING.json').write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    return report
