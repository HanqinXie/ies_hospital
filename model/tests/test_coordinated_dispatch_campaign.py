"""Small-panel campaign totals preserve unknowns and actual reference identity."""
import importlib
import pytest

def test_matched_contrasts_use_registered_references_without_secure_alias():
    try:
        module = importlib.import_module('tools.coordinated_dispatch.campaign')
    except ModuleNotFoundError:
        pytest.fail('The registered small-panel campaign adapter is absent')
    rows = []
    for pid, failure, duration, complete in (
        ('execution_reserve', 0, 0., True), ('fixed', 0, 0., True),
        ('state', 1, 12., True), ('unknown', None, None, False)):
        row = dict(scenario_id='s', root_family_id='r', analysis_class='L4b',
                   analysis_cohort='seen', policy_id=pid,
                   observed_until_minute=600. if complete else 100.,
                   design_axis='test', axis_value=0, design_axis_value=0,
                   fixed_context_json={})
        row.update({key:0 for key in ('gt_protection_trip_count','minimum_output_interlock_count',
            'no_export_interlock_count','requested_decision_count','arrived_command_count','executed_command_count')})
        for window in ('primary', 'full'):
            row[window+'_full_horizon'] = complete
            for index, target in enumerate(module.original.previous.TARGET_NAMES):
                row[window+'_'+target] = failure if index == 0 else duration if index == 1 else (0. if complete else None)
        rows.append(row)
    records = module.contrasts(rows, ['execution_reserve', 'fixed', 'state', 'unknown'])
    state = next(r for r in records if r['candidate_policy_id']=='state' and r['reference_policy_id']=='fixed')
    unknown = next(r for r in records if r['candidate_policy_id']=='unknown' and r['reference_policy_id']=='fixed')
    assert state['full_P0_failure_known_delta'] == 1
    assert state['full_P0_failure_minutes_delta'] == 12.
    assert unknown['full_P0_failure_known_delta'] is None
    assert unknown['full_P0_failure_minutes_delta'] is None
    assert all(r['reference_policy_id'] != 'secure_milp' for r in records)
