"""Pure admission/provenance tests. No trajectory or rendering is started."""
from copy import deepcopy
import importlib.util
from pathlib import Path

import pytest


def api(name='source_partition_rerun_20261105'):
    path=Path(__file__).with_name(name+'.py')
    assert path.is_file(), 'The U-only pipeline must be implemented'
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def fixture_topics():
    return [dict(id=f'T{i:02d}',title='test',start_minute=0.,end_minute=180.,
        fault_start_minute=60.,fault_end_minute=120.,conditions={role:dict(
            source=dict(role=role,result={'path':f'E:/old/{i}/{role}/RESULT.json','sha256':'a'*64}),
            segments=[dict(start_minute=0.,end_minute=180.,demand_kW=[[1.,2.,3.]]*4,
                served_kW=[[1.,2.,3.]]*4)]) for role in ('normal','unattended','response')})
        for i in range(1,9)]


def test_replacement_preserves_entire_normal_response_and_their_producer_metadata():
    m=api();old=dict(topics=fixture_topics());before=deepcopy(old)
    replacements={t['id']:deepcopy(t['conditions']['unattended']) for t in old['topics']}
    replacements['T01']['segments'][0]['served_kW'][0][0]=.5
    replacements['T01']['source']['role']='new_u_only_execution'
    new=m.merge_plot_data(old,replacements,{'path':'E:/new/SOURCE_BINDING.json','sha256':'b'*64})
    assert old==before
    for original,updated in zip(old['topics'],new['topics']):
        for role in ('normal','response'):
            assert updated['conditions'][role]==original['conditions'][role]
    assert new['new_fault_trajectories']==8 and new['response_new_execution'] is False
    assert new['all_fault_U_R_newly_executed'] is False
    m.assert_preserved_conditions(new,old)
    new['topics'][0]['conditions']['response']['source']['sha256']='changed'
    with pytest.raises(ValueError,match='Frozen response'):
        m.assert_preserved_conditions(new,old)


def test_common_origin_requires_exact_intervals_and_complete_physical_state():
    m=api();rows=[{'start_minute':0.,'end_minute':60.,'action':{'gt_electric_kW':[1.,0.,0.]}}]
    state={'time_minute':60.,'pending_commands':[{'id':3}],'state':{'fuel':1.}}
    result=m.compare_common_origin(rows,state,rows,state)
    assert result['native_prefix_exact'] and result['complete_physical_state_exact']
    changed=deepcopy(state);changed['pending_commands'][0]['id']=4
    with pytest.raises(ValueError,match='physical state'):
        m.compare_common_origin(rows,changed,rows,state)
    changed=deepcopy(rows);changed[0]['end_minute']=59.
    with pytest.raises(ValueError,match='prefix'):
        m.compare_common_origin(changed,state,rows,state)


def test_common_origin_records_remaining_computational_budget_without_hiding_physical_time():
    m=api();row={'start_minute':0.,'end_minute':1.,'cooling':{'elapsed_seconds':1.},
        'solver_backend_layers':[{'attempts':[{'elapsed_seconds':.1,'parameters':{'TimeLimit':5.999999}}]}]}
    old=deepcopy(row);old['solver_backend_layers'][0]['attempts'][0]['parameters']['TimeLimit']=5.999998
    row['solver_backend_layers'][0]['nonlinear_domain_evidence']={'capture_seconds':.001}
    old['solver_backend_layers'][0]['nonlinear_domain_evidence']={'capture_seconds':.002}
    report=m.compare_common_origin([row],{},[old],{})
    assert report['native_prefix_exact']
    assert any('TimeLimit' in v['path'] for v in report['excluded_computational_timers']['current'])
    old['cooling']['elapsed_seconds']=.999
    with pytest.raises(ValueError,match='prefix'):
        m.compare_common_origin([row],{},[old],{})


def test_ram_admission_reserves_existing_six_gib_boundary_and_does_not_oversubscribe():
    m=api();GiB=1024**3
    assert m.admit_worker(available_bytes=23*GiB,active_count=0,max_workers=4,
        worker_budget_bytes=4*GiB,reserve_bytes=6*GiB)
    assert not m.admit_worker(available_bytes=9*GiB,active_count=1,max_workers=4,
        worker_budget_bytes=4*GiB,reserve_bytes=6*GiB)
    assert not m.admit_worker(available_bytes=30*GiB,active_count=4,max_workers=4,
        worker_budget_bytes=4*GiB,reserve_bytes=6*GiB)
    assert not m.admit_worker(available_bytes=5*GiB,active_count=0,max_workers=4,
        worker_budget_bytes=4*GiB,reserve_bytes=6*GiB)


def test_common_comparison_excludes_solver_cpu_timing_but_keeps_diesel_physical_timers():
    m=api();old=[dict(start_minute=0.,end_minute=60.,diesel_local_response={'elapsed_seconds':15.},
        m23_boiler_domains={'solver_backend_layers':[{'attempts':[{'elapsed_seconds':.1,'status':'optimal'}]}]})]
    current=deepcopy(old);current[0]['m23_boiler_domains']['solver_backend_layers'][0]['attempts'][0]['elapsed_seconds']=.2
    state={'time_minute':60.}
    receipt=m.compare_common_origin(current,state,old,state)
    assert len(receipt['excluded_computational_timers']['current'])==1
    current[0]['diesel_local_response']['elapsed_seconds']=16.
    with pytest.raises(ValueError,match='prefix'):
        m.compare_common_origin(current,state,old,state)


def test_source_admission_rejects_changes_outside_the_authorized_u_modules():
    m=api();old={'src/hospital_ies/model.py':'a','tools/noopt_unattended_20261104/forward.py':'b'}
    current=dict(old);current['tools/noopt_unattended_20261104/forward.py']='c'
    assert m.admit_source_delta(old,current)['changed']==['tools/noopt_unattended_20261104/forward.py']
    current['src/hospital_ies/model.py']='x'
    with pytest.raises(ValueError,match='authorized'):
        m.admit_source_delta(old,current)


def test_historical_snapshot_uses_exact_source_and_execution_tools_zip_groups(tmp_path):
    import hashlib
    import zipfile
    m=api();scientific=b'original physical source\n';worker=b'original frozen runner\n'
    archive=tmp_path/'CODE_SNAPSHOT.zip'
    with zipfile.ZipFile(archive,'w') as output:
        output.writestr('source/tools/example.py',scientific)
        output.writestr('execution_tools/no_priority_rerun_20261105.py',worker)
    m.write(tmp_path/'CODE_SNAPSHOT_RECEIPT.json',m.pin(archive))
    binding=dict(source_root=str(tmp_path/'old_source'),
        source_files={'tools/example.py':hashlib.sha256(scientific).hexdigest()},
        execution_tools=[dict(path=str(tmp_path/'old_source/release/no_priority_rerun_20261105.py'),
            sha256=hashlib.sha256(worker).hexdigest())])
    receipt=m.verify_old_snapshot(tmp_path,binding)
    assert receipt['status']=='passed' and receipt['source_files_verified']==2
    binding['execution_tools'][0]['sha256']='0'*64
    with pytest.raises(ValueError,match='Archived historical producer source differs'):
        m.verify_old_snapshot(tmp_path,binding)


def test_renderer_retains_the_old_numeric_validation_without_false_new_response_claim():
    m=api('source_partition_figures_20261105');r=api()
    old=dict(topics=fixture_topics())
    replacements={t['id']:deepcopy(t['conditions']['unattended']) for t in old['topics']}
    data=r.merge_plot_data(old,replacements,{'path':'E:/new/SOURCE_BINDING.json','sha256':'b'*64})
    before=deepcopy(data);m.validate_data(data)
    assert data==before
    assert m.execution_roles()==dict(normal='unchanged_frozen_original_reference',
        unattended='new_source_partition_arithmetic_execution',response='unchanged_frozen_run03_response')
    bad=deepcopy(data);bad['response_new_execution']=True
    with pytest.raises(ValueError,match='U-only'):
        m.validate_data(bad)
