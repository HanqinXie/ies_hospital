"""Limited diagnostic contracts: no replacement physical baseline or trial integration."""
import importlib
from pathlib import Path
import pandas as pd
import pytest


def diagnostic():
    try:
        return importlib.import_module('tools.model_correction_v26.cold_diagnostics')
    except ModuleNotFoundError:
        pytest.fail('Limited cold diagnostic capability missing')


def test_only_resolved_minimum_plant_iterations_may_change():
    m=diagnostic()
    source='Version,26.2;\nConvergenceLimits,1,20;\nPump:VariableSpeed,KEEP,IN,OUT,1;'
    assert m.derive_numeric_idf(source,False)==source
    changed=m.derive_numeric_idf(source,True)
    assert 'ConvergenceLimits,1,20,8,8;' in changed
    assert m.non_numeric_physics(source)==m.non_numeric_physics(changed)
    with pytest.raises(ValueError):m.derive_numeric_idf(source.replace(',1,20;',',1,30;'),True)


def test_exact_counter_key_unit_and_no_missing_zero_imputation():
    m=diagnostic();channels=m.iteration_channels()
    assert len(channels)==4
    assert all(c['key']=='SimHVAC' and c['unit']=='1' for c in channels.values())
    metadata='\n'.join('OutputVariable,'+c['variable']+',SIMHVAC,[]' for c in channels.values())
    assert len(m.qualify_counter_metadata(metadata,{k:i for i,k in enumerate(channels)}))==4
    with pytest.raises(ValueError):m.qualify_counter_metadata(metadata.replace('SIMHVAC','WRONG'),{k:i for i,k in enumerate(channels)})
    with pytest.raises(ValueError):m.qualify_counter_metadata(metadata,{k:-1 for k in channels})


def test_native_integer_count_metadata_uses_actual_sql_empty_unit_not_api_inventory():
    m=diagnostic();channels=m.iteration_channels()
    sql_metadata='\n'.join('OutputVariable,'+c['variable']+',SIMHVAC,' for c in channels.values())
    assert len(m.qualify_counter_metadata(sql_metadata,{k:i for i,k in enumerate(channels)}))==4
    with pytest.raises(ValueError):m.qualify_counter_metadata(sql_metadata.replace('SIMHVAC,','SIMHVAC,W'),{k:i for i,k in enumerate(channels)})


def test_trial_states_are_not_integrated_and_duplicate_zero_counter_samples_survive():
    m=diagnostic()
    rows=[dict(callback_ordinal=i,stage=stage,api_dt_h=dt,hvac_count=count)
          for i,(stage,dt,count) in enumerate([('inside',1/6,0),('inside',1/6,0),('inside',1/12,0),('before',1/12,3),('after',1/12,3)],1)]
    resolved=m.resolve_trace_group(rows,dict(timestamp=pd.Timestamp('2006-07-20 16:45'),bucket_end=pd.Timestamp('2006-07-20 16:50'),dt_h=1/12,substep_index=0))
    assert len(resolved)==5 and [r['hvac_count'] for r in resolved[:2]]==[0,0]
    assert all(r['integration_weight_h']==0 for r in resolved)
    assert resolved[0]['trial_dt_matches_accepted'] is False
    assert resolved[-1]['accepted_completed_sample'] is True
    assert all(r['stage_semantics']=='PRE_CURRENT_EMS_PRE_CURRENT_SOLVE' for r in resolved[:3])


def test_numerical_diagnostic_ledger_requires_both_explicit_labels():
    from tools.model_correction_v26.cold_ledger import require_ledger_qualification
    with pytest.raises(ValueError):require_ledger_qualification({},dict(output_only_qualified=False,numerical_diagnostic_qualified=True))
    with pytest.raises(ValueError):require_ledger_qualification(dict(numerical_solver_diagnostic=True),dict(output_only_qualified=False))
    require_ledger_qualification(dict(numerical_solver_diagnostic=True),dict(output_only_qualified=False,numerical_diagnostic_qualified=True))


def test_observer_supports_optional_read_only_trace_without_duplicate_after_callback():
    import inspect
    from tools.model_correction_v26.cold_observer import collect_observed
    assert 'trace' in inspect.signature(collect_observed).parameters
    source=inspect.getsource(collect_observed)
    assert source.count('callback_end_system_timestep_after_hvac_reporting(')==1
    assert 'trace.after_system' in source and 'trace.resolve_bucket' in source
