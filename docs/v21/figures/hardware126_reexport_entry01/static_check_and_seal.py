"""Static-only portable entry/selected-target closure; never execute MATLAB/tests."""
import hashlib,json,re
from datetime import datetime,timezone
from pathlib import Path
HERE=Path(__file__).resolve().parent
SEALED=Path('E:/hospital_ies_v21_20261023/reviews/hardware_figures_actual126_01')
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
read=lambda p:json.loads(Path(p).read_text(encoding='utf-8-sig'))
def ref(p):
    p=Path(p);return dict(path=str(p),bytes=p.stat().st_size,sha256=sha(p))
selection=SEALED/'PUBLICATION_FILE_SELECTION01.json';known='78f496601a2f609c5dd9fa1f09c936041104297b80c9c06e3dee67e1f0f3de92'
assert sha(selection)==known
selected=read(selection);contract=read(HERE/'PORTABLE_INPUT_CONTRACT.json')
assert sha(HERE/'PORTABLE_INPUT_CONTRACT.json')=='e960f63eb85cd915f264e65379d2126f4c4c91b0895c4edeb3ce6b7f2d252d08'
mapping={m['target_relative_path']:m for m in selected['files']}
assert len(mapping)==286 and len(contract['files'])==286
for m in contract['files']:
    s=mapping[m['path']];assert s['bytes']==m['bytes'] and s['sha256']==m['sha256']
    p=Path(s['path']);assert p.stat().st_size==m['bytes'] and sha(p)==m['sha256']
required=['data_and_code/f4_bundle/LOCAL_DATA_MANIFEST.json','data_and_code/f4_bundle/replay_remaining_figures.m','data_and_code/hw18/HW18_ACTUAL_MANIFEST.json','code/native/plot_hardware_native_process.m','code/actual_entrypoints/style_hardware_page_legends_v1.m','provenance/matlab01/f4_figures/MATLAB_FIGURE_RECEIPT.json','provenance/matlab01/f4_objects/MATLAB_OBJECT_QA.json','provenance/matlab01/hw18_figures/HW18_MATLAB_OBJECT_QA.json']
f4=read(mapping['provenance/matlab01/f4_figures/MATLAB_FIGURE_RECEIPT.json']['path'])['figures']
hw=read(mapping['data_and_code/hw18/HW18_ACTUAL_MANIFEST.json']['path'])['cases']
assert len(f4)==4 and len(hw)==18
for n in f4:required.extend(['provenance/initial_native_figures/f4_figures/'+n+'.fig','figures/f4_figures/'+n+'_PAIRED_VALUES.csv'])
for c in hw:required.extend(['provenance/initial_native_figures/hw18_figures/'+c['output_name']+'.fig','data_and_code/hw18/'+c['folder']+'/PROCESS.csv','data_and_code/hw18/'+c['folder']+'/PROCESS.mat'])
assert all(p in mapping for p in required)
entry=HERE/'reexport_verified_hardware22_figs.m';source=entry.read_text(encoding='utf-8')
assert source.startswith('function receipt=reexport_verified_hardware22_figs(bundleRoot,freshOutputRoot)')
assert len(re.findall(r'\bstyle_hardware_page_legends_v1\(',source))==2
assert not re.search(r'\b(?:system|eval|evalin|webread|webwrite|urlread|unzip|run)\s*\(',source)
assert not re.search(r'[A-Za-z]:[\\/]',source)
assert 'verifyLocalFiles(bundleRoot,C.files);' in source
assert 'sourceDir' not in source # Only the frozen style owns this interface.
assert source.count('function copyExact(source,destination)')==1
assert 'before=hashFile(source);copyfile(source,destination);' in source
assert 'hashFile(destination)==before&&hashFile(source)==before' in source
assert 'CSV_metric_recomputation' in source and 'raw_or_sidecar_read' in source
before={str(p):sha(p) for p in [selection,SEALED/'FINAL_AUTHOR_DELIVERY_RECEIPT01.json',SEALED/'FINAL_AUTHOR_DELIVERY_EXACT01.json']}
assert before[str(SEALED/'FINAL_AUTHOR_DELIVERY_RECEIPT01.json')]=='394102ec68a25965eed3e2b45876cc92ecfde66c419b59d162e085d264cdf0a7'
assert before[str(SEALED/'FINAL_AUTHOR_DELIVERY_EXACT01.json')]=='05c1de13798b1cdacbb9a184d722666c5e8628123bf68cde36e81626c0e4ae4e'
members=[]
for p in sorted(HERE.rglob('*')):
    if not p.is_file() or p.name=='commands_and_exit_codes.txt' or (p.parent.name=='logs' and p.name.startswith('portable_static_seal01.')):continue
    members.append(dict(relative=p.relative_to(HERE).as_posix(),**ref(p)))
out=HERE/'PORTABLE_ENTRY_RECEIPT.json';assert not out.exists()
result=dict(schema='hospital_ies.hardware22_portable_verified_FIG_entry_preparation.v2',created_utc=datetime.now(timezone.utc).isoformat(),status='STATIC_INTERFACE_PREPARATION_COMPLETED_NOT_REEXECUTED',mode='verified_native_FIG_reexport_not_CSV_recomputation',entry=ref(entry),README=ref(HERE/'README_PORTABLE_已验证原生FIG再导出.md'),input_contract=ref(HERE/'PORTABLE_INPUT_CONTRACT.json'),publication_source_selection_sha256=known,all286_selected_source_bytes_statically_checked=True,required_target_count=len(required),required_targets_all_present_in_selection=True,style_call_count=2,style_source_unchanged_sha256='923b7ef0269cf91f3dddaf581e5be8fe0b7b25d2bdc603fb18da6fbff46659b4',old_absolute_runtime_dependency_literals_in_entry=False,old_receipt_paths_metadata_not_followed=True,original_receipts_schema_or_SHA_relaxed=False,fresh_staging_reassembly='local initial FIG+unchanged receipts+local exact pairedCSV; original style hard pins preserved',explicit_copyExact_helper_defined_and_byte_guarded=True,predecessor_static_omission='v1 source omitted copyExact definition; predecessor files and static-only receipt retained. v2 adds only the explicit exact-copy helper; v1 is not the publication candidate.',MATLAB_execution='NOT_REEXECUTED',MATLAB_checkcode='NOT_RUN',MATLAB_parse='NOT_RUN',relocated_reexport='NOT_REEXECUTED',tests_reexecuted=False,physical_attempts=0,raw_or_sidecar_read=False,source_fullpool_recalculation=False,static_check_limit='Text/interface/target/SHA checks only. This is not MATLAB syntax, graphics execution, portability runtime or post-reexport visual QA.',sealed_inputs_unchanged_before_after=before,member_count=len(members),member_bytes=sum(m['bytes'] for m in members),members=members,suggested_publication_subdirectory='portable_reexport',publish_main_files=['reexport_verified_hardware22_figs.m','PORTABLE_INPUT_CONTRACT.json','README_PORTABLE_已验证原生FIG再导出.md'],new_archive_created=False,whole_archive_size_claim=False,scientific_gate=False)
for p,h in before.items():assert sha(p)==h
out.write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
print(json.dumps(dict(receipt=str(out),sha256=sha(out),entry_sha256=sha(entry),README_sha256=sha(HERE/'README_PORTABLE_已验证原生FIG再导出.md'),members=len(members),member_bytes=result['member_bytes'],status=result['status']),ensure_ascii=False))
