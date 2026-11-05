"""Research aggregation keeps unknowns, temperatures and individual harm visible."""
import importlib.util
from pathlib import Path

import pytest

PATH=Path(__file__).resolve().parents[1]/'outputs/v13_executed_service_20261017_run01/review/build_research_review.py'


def module():
    if not PATH.exists():pytest.fail('Saved-only research review helper is missing')
    spec=importlib.util.spec_from_file_location('v13_research_review',PATH)
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value


NAMES=['P0_failure','P0_failure_minutes']+[f'P{p}_{c}_ENS_kWh' for p in range(4) for c in 'ehc']+[f'P{p}_max_temperature_C' for p in range(3)]+['secondary_GT_trips','actual_energy_fee_USD']


def row(sid,pid,*,minutes=0.,complete=True,temp=24.,cls='N2'):
    value=dict(scenario_id=sid,policy_id=pid,analysis_class=cls,analysis_cohort='synthetic',
               root_family_id='root:'+sid,status='completed' if complete else 'model_constraint_infeasible',
               observed_until_minute=600. if complete else 300.)
    for window in ('primary','full'):
        value[window+'_full_horizon']=complete
        for name in NAMES:
            number=(int(minutes>0) if name=='P0_failure' else minutes if name=='P0_failure_minutes' else temp if 'temperature' in name else 1.)
            value[window+'_'+name]=number if complete or name=='P0_failure' and minutes>0 else None
            value[window+'_observed_prefix_'+name]=number
    return value


def review(rows):
    return module().summarize_records(rows,[],[],[],[],[],fixed='F',feedback='H',static_zero='Z',phase='development')


def test_temperature_is_maximum_and_normal_is_separate():
    rows=[row(s,p,temp=t,cls=c) for s,t,c in [('a',24.,'N2'),('b',26.,'N2'),('c',40.,'L2')] for p in ('F','H','Z')]
    out=review(rows)
    normal=next(r for r in out['absolute'] if r['analysis_class']=='N2' and r['policy_id']=='F' and r['window']=='full')
    assert normal['objectives_all_registered']['P2_max_temperature_C']==26.
    assert normal['registered']==2 and normal['fee_role']=='normal_reference'
    assert normal['objectives_all_registered']['P2_e_ENS_kWh']==2.


def test_unknown_totals_not_completed_case_imputation_and_prefix_harm_retained():
    rows=[row('a',p,minutes=(30. if p=='H' else 0.),complete=p!='H',cls='L4b') for p in ('F','H','Z')]
    out=review(rows)
    h=next(r for r in out['absolute'] if r['analysis_class']=='L4b' and r['policy_id']=='H' and r['window']=='full')
    assert h['unknown']==1 and h['objectives_all_registered']['P0_failure_minutes'] is None
    assert h['known_P0_case_ids']==['a']
    contrast=next(r for r in out['contrasts'] if r['analysis_class']=='L4b' and r['candidate']=='H' and r['reference']=='F' and r['window']=='full')
    assert contrast['new_P0_case_ids']==['a']
    assert contrast['candidate_prefix_exceeds_complete_reference_P0_minute_ids']==['a']
    assert out['freeze_selection_evidence']['baseline_fallback_due_observed_P0_harm'] is True


def test_individual_p0_harm_not_cancelled_by_another_scenario_gain():
    rows=[]
    for sid,old,new in [('harm',0.,5.),('gain',20.,0.)]:
        rows.extend([row(sid,'F',minutes=old,cls='L4b'),row(sid,'H',minutes=new,cls='L4b'),row(sid,'Z',minutes=old,cls='L4b')])
    out=review(rows)
    full=next(r for r in out['contrasts'] if r['analysis_class']=='L4b' and r['candidate']=='H' and r['reference']=='F' and r['window']=='full')
    assert full['all_registered_delta']['P0_failure_minutes']==-15.
    assert full['new_P0_case_ids']==['harm'] and full['P0_duration_worsened_ids']==['harm']
    assert out['freeze_selection_evidence']['baseline_fallback_due_observed_P0_harm'] is True


def test_missing_baseline_history_is_na_and_last_decision_history_is_not_final_ens():
    m=module();rows=[row('a',p) for p in ('F','H','Z')]
    history=[dict(scenario_id='a',policy_id='H',analysis_class='N2',decision_time_min=585.,
                  effective_service_budget_fractions_json=[0.,.1],
                  cumulative_equivalent_loss_minutes_json=[[6.,0.,0.],[12.,0.,0.]],
                  cumulative_ENS_kWh_json=[[8.,0.,0.],[9.,0.,0.]],generated_command_id=40)]
    result=m.summarize_records(rows,[],history,[],[],[],fixed='F',feedback='H',static_zero='Z',phase='development')
    h=next(r for r in result['history_and_allowance']['trajectories'] if r['policy_id']=='H')
    f=next(r for r in result['history_and_allowance']['trajectories'] if r['policy_id']=='F')
    assert h['last_saved_history_time_min']==585. and h['last_saved_history_is_final_trajectory_ENS'] is False
    assert h['minimum_effective_beta']==[0.,.1]
    assert f['minimum_effective_beta'] is None


def test_saved_csv_booleans_do_not_turn_false_censoring_into_truthy_strings(tmp_path):
    path=tmp_path/'saved.csv'
    path.write_text('right_censored,arrival_revalidation_changed,observed_execution_minutes\nFalse,True,15.0\n',encoding='utf-8')
    saved=module().csvread(path)[0]
    assert saved['right_censored'] is False and saved['arrival_revalidation_changed'] is True
    assert saved['observed_execution_minutes']==15.
