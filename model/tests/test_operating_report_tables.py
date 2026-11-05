"""Tiny saved-analysis fixtures; no physical core, episode or raw access."""
import importlib
import json
from pathlib import Path
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
E='endurance_restore_p0_ready_v14r6'
K1='readiness_handoff_K1_v15'
K2='readiness_handoff_K2_v15'
NAMES=['P0_failure','P0_failure_minutes',*[f'P{p}_{c}_ENS_kWh' for p in range(4) for c in 'ehc'],
       'P0_max_temperature_C','P1_max_temperature_C','P2_max_temperature_C','secondary_GT_trips','actual_energy_fee_USD']


def api():
    try:return importlib.import_module('tools.operating_reporting.report_tables')
    except ModuleNotFoundError as exc:pytest.fail('Missing saved report tables: '+str(exc),pytrace=False)


def put(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value),encoding='utf8')


def window(complete=True,failure=False,ens=0.,end=600.):
    matrix=[[ens if p==0 and c==0 else 0. for c in range(3)] for p in range(4)]
    targets=[float(failure),2. if failure else 0.,*[v for row in matrix for v in row],23.,24.,25.,1.,99.]
    if not complete:targets=[None]*19
    return {'window_minutes':[0.,end],'complete':complete,'observed_minutes':end if complete else 200.,
        'original_19':{'target_names':NAMES,'targets':targets,
            'observed_prefix_max_temperature_C':[23.,24.,25.],
            'observed_prefix_secondary_GT_trips':1},
        'observed_prefix':{'total_ENS_kWh':matrix,'P0_failure_minutes':2. if failure else 0.,
            'material_minutes':[[1.,0.,0.]]*4,'longest_material_minutes':[[1.,0.,0.]]*4,
            'gt_actual':{'GT_1':{'observed_running_minutes':100.,'zero_to_positive_count':1}},
            'service_recovery':{'first_failure_minute':1. if failure else None,
                'confirmed_30min_interval':[3.,33.] if failure else None}},
        'all_registered':{} if complete else None}


def batch(run,name='one',*,records=None,phase='development',end=600.,policies=(E,K1,K2)):
    directory=run/'experiments'/name
    put(directory/'plan.json',{'phase':phase,'model_id':'M15_local_response','model_config':{},
        'source_sha256':{'source':'abc'},'scenario_ids':['s'],'policy_specs':[{'policy_id':p} for p in policies],
        'registered_pairs':len(policies)})
    put(directory/'manifest.json',{'scenarios':[{'id':'s','analysis_class':'L1','duration_steps':end/15.,
        'step_minutes':15.,'first_event_minute':0.,'physical_scenario_signature':'same'}]})
    rows=[]
    for pid,w in (records or {}).items():
        rows.append({'scenario_id':'s','policy_id':pid,'model_id':'M15_local_response',
            'status':'completed' if w['complete'] else 'local_response_domain_exceeded',
            'analysis':{'observed_until_minute':w['observed_minutes'],'requested_end_minute':end,
                'windows':{'full':w,'registered_full':w},'engineering':{},
                'diesel_local_response':{'observed_prefix':{'electrical_work_kWh':2.,'mechanical_work_kWh':3.,
                    'loss_work_kWh':.5,'rotor_delta_kWh':.5,'fuel_budget_debit_kWh_equivalent':3.}}}})
    put(directory/'analysis/RESULTS.json',{'records':rows})
    return directory,rows


def only(rows,**keys):
    matching=[r for r in rows if all(r.get(k)==v for k,v in keys.items())]
    assert len(matching)==1
    return matching[0]


def test_observed_failure_and_unknown_remain_distinct_in_full_denominator(tmp_path):
    a=api();run=tmp_path/'run';batch(run,records={E:window(),K1:window(False,True,5.),K2:window(False)})
    report=a.collect(run)
    k1=only(report['tables']['WINDOW_GROUPS'],policy_id=K1,window='full')
    assert (k1['registered_n'],k1['complete_n'],k1['observed_P0_failure_n'],k1['unknown_without_observed_P0_failure_n'])==(1,0,1,0)
    assert k1['observed_P0_e_ENS_lower_bound_kWh']==5.
    assert k1['complete_P0_e_ENS_kWh_sum'] is None and k1['complete_P0_e_ENS_kWh_n']==0
    assert k1['observed_P0_max_temperature_C']==23. and k1['P3_disturbed_temperature_C'] is None
    k2=only(report['tables']['WINDOW_GROUPS'],policy_id=K2,window='full')
    assert k2['unknown_without_observed_P0_failure_n']==1
    pairs=report['tables']['E_PAIRED_WINDOWS']
    assert only(pairs,candidate_policy_id=K1,window='full')['comparison_outcome']=='new_observed_P0_failure'
    assert only(pairs,candidate_policy_id=K2,window='full')['comparison_outcome']=='undetermined'
    assert report['raw_files_opened']==0


def test_avoided_failure_requires_complete_clear_candidate_missing_analysis_is_unknown(tmp_path):
    a=api();run=tmp_path/'run';batch(run,records={E:window(True,True,6.),K1:window()})
    report=a.collect(run);pairs=report['tables']['E_PAIRED_WINDOWS']
    assert only(pairs,candidate_policy_id=K1,window='full')['comparison_outcome']=='avoided_observed_P0_failure'
    assert only(pairs,candidate_policy_id=K2,window='full')['comparison_outcome']=='undetermined'
    group=only(report['tables']['WINDOW_GROUPS'],policy_id=K2,window='full')
    assert group['registered_n']==1 and group['missing_analysis_n']==1 and group['unknown_without_observed_P0_failure_n']==1
    assert group['observed_P0_e_ENS_lower_bound_kWh'] is None


def test_same_named_windows_with_different_bounds_are_not_grouped_or_paired(tmp_path):
    a=api();run=tmp_path/'run'
    batch(run,'short',records={E:window(end=600.)},policies=(E,),end=600.)
    batch(run,'long',records={K1:window(end=900.)},policies=(K1,),end=900.)
    report=a.collect(run)
    rows=[r for r in report['tables']['WINDOW_GROUPS'] if r['window']=='full']
    assert len(rows)==2 and {r['window_end_minute'] for r in rows}=={600.,900.}
    pair=only(report['tables']['E_PAIRED_WINDOWS'],candidate_policy_id=K1,window='full')
    assert pair['comparison_outcome']=='missing_reference' and pair['reference_n']==0


def test_partial_pair_saved_analysis_is_used_without_raw_and_duplicate_reference_is_ambiguous(tmp_path,monkeypatch):
    a=api();run=tmp_path/'run';directory,records=batch(run,records={E:window(),K1:window()})
    (directory/'analysis/RESULTS.json').unlink()
    for row in records:put(directory/'pairs'/('s__'+row['policy_id'])/'RESULT.json',row)
    batch(run,'repeat',records={E:window()},policies=(E,))
    original=Path.open
    def no_raw(path,*args,**kwargs):
        if path.suffix=='.gz' or path.name.startswith('trajectory'):pytest.fail('Raw file opened')
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'open',no_raw)
    report=a.collect(run)
    pair=only(report['tables']['E_PAIRED_WINDOWS'],candidate_policy_id=K1,window='full')
    assert pair['comparison_outcome']=='ambiguous_repeated_cells' and pair['reference_n']==2
    assert report['registered_pairs']==4


def test_fresh_csv_json_export_preserves_windows_and_trajectory_scope(tmp_path):
    a=api();run=tmp_path/'run';batch(run,records={E:window(),K1:window()},policies=(E,K1))
    out=tmp_path/'tables';receipt=a.export(run,out)
    assert receipt['raw_files_opened']==0 and receipt['physical_episodes_started']==0
    assert (out/'WINDOW_GROUPS.csv').is_file() and (out/'REPORT_TABLES.json').is_file()
    saved=json.loads((out/'REPORT_TABLES.json').read_text(encoding='utf8'))
    op=saved['tables']['OPERATION_TRAJECTORIES'][0]
    assert op['accounting_scope']=='trajectory_observed_prefix' and op['mechanical_work_kWh']==3.
    assert 'mechanical_work_kWh' not in saved['tables']['WINDOW_GROUPS'][0]
    assert receipt['output_sha256']['WINDOW_GROUPS.csv']
    with pytest.raises(FileExistsError):a.export(run,out)


def test_stages_and_operation_requested_durations_keep_separate_denominators(tmp_path):
    a=api();run=tmp_path/'run'
    batch(run,'known',records={E:window()},policies=(E,),phase='pilot_known')
    batch(run,'frozen',records={E:window(end=900.)},policies=(E,),phase='frozen_confirmation',end=900.)
    directory,_=batch(run,'normal',records={E:window()},policies=(E,),phase='dispersed_normal')
    manifest=json.loads((directory/'manifest.json').read_text());manifest['scenarios'][0]['analysis_class']='N2'
    put(directory/'manifest.json',manifest)
    report=a.collect(run)
    # A newly designed N2 panel is ordinary normal development, not the
    # explicitly imported exact-v14 normal identity.
    assert {r['stage'] for r in report['tables']['OPERATION_GROUPS']}=={'known','frozen','normal'}
    for row in report['tables']['OPERATION_GROUPS']:
        assert row['registered_n']==1 and row['observed_mechanical_work_kWh_sum']==3.
        assert row['observed_mechanical_work_kWh_sum_n']==1
    assert only(report['tables']['OPERATION_GROUPS'],stage='frozen')['requested_end_minute']==900.
    assert only(report['tables']['OPERATION_GROUPS'],stage='normal')['phase']=='dispersed_normal'


@pytest.mark.parametrize('scene,phase,stage',[
    ({'analysis_class':'N2'},'targeted_exact_v14_normal','exact-normal'),
    ({'analysis_class':'N2','known_case_role':'exact_v14_normal_day47'},'pilot_known','exact-normal'),
    ({'analysis_class':'N2'},'pilot_normal','normal'),
    ({'analysis_class':'N2','experiment_role':'confirmation'},'frozen_confirmation','frozen'),
    ({'analysis_class':'N2'},'similar_targeted_exact_v14_normal','normal'),
    ({'analysis_class':'N2','known_case_role':'day47_recovery'},'pilot_known','known'),
    ({'analysis_class':'L1'},'dispersed_development_incidents','development'),
])
def test_exact_normal_requires_explicit_v14_identity(scene,phase,stage):
    assert api()._stage(scene,phase)==stage


def test_all_state_speed_zero_is_not_connected_speed_failure_and_fuel_flag_is_separate(tmp_path):
    a=api();run=tmp_path/'run';directory,records=batch(run,records={E:window()},policies=(E,))
    analysis=records[0]['analysis'];analysis['diesel_local_response']['observed_prefix'].update(speed_min_pu=0.,speed_max_pu=1.02)
    analysis['timelines']={'diesel_local_response':[
        {'operation':'advance','actual_terminal_kW':0.,'speed_min_pu':0.,'speed_max_pu':0.,'domain_exceeded':False},
        {'operation':'advance','actual_terminal_kW':50.,'speed_min_pu':.98,'speed_max_pu':1.02,
         'domain_exceeded':False,'fuel_budget_domain_exhausted':True}]}
    put(directory/'analysis/RESULTS.json',{'records':records})
    row=a.collect(run)['tables']['OPERATION_TRAJECTORIES'][0]
    assert row['all_states_speed_min_pu']==0. and 'speed_min_pu' not in row
    assert row['positive_Pe_advance_speed_min_pu']==.98
    assert row['fuel_budget_exhausted_segment_n']==1 and row['rotor_domain_exceeded_segment_n']==0
