import copy,csv,importlib.util,json
from pathlib import Path
import pytest
spec=importlib.util.spec_from_file_location('outcomes',Path(__file__).with_name('engineering_outcomes.py'))
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

def fixture(tmp_path):
    result={k:v for k,v in dict(scenario_id='scene',policy_id='C',configuration_id='H0',
        source_block_id='block',source_binding_sha256='source').items()}
    result['metrics']=dict(event_counts={'protection_trip':1},strategy_fallback_steps=0,
        projection_fallback_steps=0,projection_changed_steps=1)
    p=tmp_path/'RESULT.json';p.write_text(json.dumps(result),encoding='utf8')
    row={**{k:result[k] for k in result if k!='metrics'},**{k:1. for k in m.FIELDS},
        'phase':'formal','window_minutes':'720','canonical_class':'L2','season_quarter':'2',
        'fault_target':'GRID','eligible':'True','result_path':str(p),'result_sha256':m.sha(p)}
    return row,result,p

def table(tmp_path,rows):
    p=tmp_path/'CASE_METRICS.csv';m.write_csv(p,rows)
    receipt=tmp_path/'PARALLEL_ANALYSIS_RECEIPT.json'
    receipt.write_text(json.dumps(dict(export_receipt=dict(files=[dict(path=p.name,sha256=m.sha(p))]))),encoding='utf8')
    return p,receipt

def test_honest_source_bound_event_table(tmp_path):
    row,_,_=fixture(tmp_path);p,receipt=table(tmp_path,[row])
    r=m.execute(p,tmp_path/'out',receipt)
    assert r['cases']==1
    rows=list(csv.DictReader((tmp_path/'out/CASE_PROCESS_OUTCOMES.csv').open(encoding='utf-8-sig')))
    assert rows[0]['protection_trip_events']=='1'

def test_changed_CASE_csv_blocked_by_native_export_binding(tmp_path):
    row,_,_=fixture(tmp_path);p,receipt=table(tmp_path,[row]);p.write_bytes(p.read_bytes()+b'\n')
    with pytest.raises(ValueError,match='CASE'):m.execute(p,tmp_path/'out',receipt)

def test_missing_event_counter_is_unknown_not_zero(tmp_path):
    row,result,p=fixture(tmp_path);result['metrics'].pop('event_counts')
    p.write_text(json.dumps(result),encoding='utf8');row['result_sha256']=m.sha(p)
    with pytest.raises(ValueError,match='event counter'):m.case_outcome(row)

def test_mixed_source_table_rejected_before_result_reads(tmp_path):
    row,_,_=fixture(tmp_path);other={**row,'scenario_id':'other','source_binding_sha256':'different'}
    p,receipt=table(tmp_path,[row,other])
    with pytest.raises(ValueError,match='Mixed'):m.execute(p,tmp_path/'out',receipt)

def test_nonfinite_scientific_value_rejected(tmp_path):
    row,_,_=fixture(tmp_path);row['P0_c_ENS_kWh']='nan'
    with pytest.raises(ValueError,match='Nonfinite'):m.case_outcome(row)

@pytest.mark.parametrize('name',['strategy_fallback_steps','projection_fallback_steps','projection_changed_steps'])
@pytest.mark.parametrize('value',[float('nan'),float('inf'),-1,'0',True,None])
def test_fallback_counter_invalid_is_unknown_not_scientific_zero(tmp_path,name,value):
    row,result,p=fixture(tmp_path);result['metrics'][name]=value
    p.write_text(json.dumps(result),encoding='utf8');row['result_sha256']=m.sha(p)
    with pytest.raises(ValueError,match='counter'):m.case_outcome(row)

def test_analysis_receipt_drift_during_read_is_blocked(tmp_path,monkeypatch):
    row,_,_=fixture(tmp_path);p,receipt=table(tmp_path,[row])
    native=m.case_outcome
    def changed(row):
        result=native(row);receipt.write_bytes(receipt.read_bytes()+b'\n');return result
    monkeypatch.setattr(m,'case_outcome',changed)
    with pytest.raises(ValueError,match='receipt drift'):m.execute(p,tmp_path/'out',receipt)

def test_background_mean_not_replicate_weighted():
    cases=[]
    for block,value in [('one',1.),('one',3.),('two',10.)]:
        cases.append(dict(phase='formal',window_minutes=720,policy_id='C',configuration_id='H0',
            canonical_class='L2',season_quarter='2',failure_path='GRID',source_block_id=block,
            protection_trip_events=0,**{k:value for k in m.FIELDS}))
    result=m.summarize(cases)[0]
    assert result['scenarios']==3 and result['source_backgrounds']==2
    assert result['P0_c_ENS_kWh_equal_background_mean']==6.

def test_failure_path_description_keeps_propagation_class_and_season_separate():
    base=dict(phase='formal',window_minutes=720,policy_id='C',configuration_id='H0',
        failure_path='GRID',source_block_id='one',protection_trip_events=0,**{k:1. for k in m.FIELDS})
    rows=[{**base,'canonical_class':c,'season_quarter':q} for c,q in [('L1','1'),('L2','1'),('L2','2')]]
    assert len(m.summarize(rows))==3

def test_demand_only_ignores_unused_fault_target():
    assert m.mechanism(dict(canonical_class='L3',fault_target='TOWER'))=='DEMAND_ONLY'

def test_compound_not_labelled_ordinary_grid_outage():
    assert m.mechanism(dict(canonical_class='X_GRID_ALL_GT0',fault_target='GRID'))=='COMPOUND_GRID_ALL_GT'
