"""Supported252 fixed NORMAL C/H0 + boundary067 C/A/H0; never launch physics/MATLAB."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil

HERE=Path(__file__).resolve().parent;BASE=Path('E:/hospital_ies_v21_20261023')
NORMAL=BASE/'reviews/normal_process_predeclaration01/NORMAL_PROCESS_PREDECLARATION.json'
NORMAL_SHA='d766b698e47cb4505afb150cb674f77e7205ab1bc3620f8fb03f9fe59b2dc23b'
BOUNDARY=BASE/'reviews/followup02_boundary_process_predeclaration01/PREDECLARATION.json'
BOUNDARY_SHA='2cfd6176d1993f69cc156fe5656649a8882e70292a559569c917cd7fc249d79c'
SOURCE='4dfe62ae105d2696b7782dc36c83ec63c6551e2cded601d6f15c0d4083058add'
INPUTS='659ecc488bda87c9338afde283cbb74135ca2ebdf2769ce463d0e61f0c6288d8'
JOBS=BASE/'followup02/JOBS.json'
JOBS_SHA='f30cd261224f1b856cc2a3130e039da6fdb71f845f4ec3787d587244d5b3553d'
HARDWARE_HELPER=BASE/'reviews/remaining_matlab_plan01/hardware_process_v1/hardware18.py'
HARDWARE_HELPER_SHA='3a6bd13794ef0315d358d190f5d9bd8601171d075d1ef07ebdaefca0f8d5ba61'
BASE_HELPER=BASE/'reviews/remaining_matlab_plan01/prepare_remaining.py'
BASE_HELPER_SHA='4db5ef1e37674799a5b4e947920488271fb3d32a3da0fd7a1bda298c552b0505'
PYTHON='C:/Users/HanqinXie/AppData/Local/Programs/Python/Python310/python.exe'
ROOT='D:/hospital_ies_v21_20261023'
LOGGER=ROOT+'/tools/p0_audit/run_logged.py'
EXPORT=BASE/'reviews/event_timeline_export01/export_event_timeline.py'
EXPORT_SHA='cec0770e6a21a97f49d6e0a12a5c403188711ca2e5c4a2202877148606d2a0cb'
ORIGINAL=BASE/'reviews/remaining_matlab_plan01/fixed_process_v1/fixed3.py'
ORIGINAL_SHA='e56883d83a4e5451ec0062ff6908bc01ab54da3060244a579c801d1f4ad892d9'
BRIDGE=BASE/'reviews/followup02_capability_plan02/recovery_evidence.py'
BRIDGE_SHA='e8be09562ba1920f233991c764daa964fabcf2450c0d0230dc1bc06b218192bb'
AMENDMENT=Path(ROOT)/'docs/v21/FOLLOWUP_EXECUTION_AMENDMENT_20261024.md'
AMENDMENT_SHA='be99124b8f9b3cd5b3ee9280ea057d8a01ebc2c396a36942647cf2e858a9ed5d'

def check_pins():
    for path,known in [(ORIGINAL,ORIGINAL_SHA),(BASE_HELPER,BASE_HELPER_SHA),
        (HARDWARE_HELPER,HARDWARE_HELPER_SHA),(BRIDGE,BRIDGE_SHA),
        (EXPORT,EXPORT_SHA),(NORMAL,NORMAL_SHA),(BOUNDARY,BOUNDARY_SHA),(AMENDMENT,AMENDMENT_SHA)]:checked(path,known)

def load_bridge():
    check_pins();return load(BRIDGE,'fixed3_supported252_recovery',BRIDGE_SHA)

def run_bound(args,action):
    out=Path(args.output).resolve();need(not out.exists(),'Fresh operation output required')
    authority=out.with_name(out.name+'_RECOVERY_AUTHORITY')
    need(not authority.exists(),'Fresh recovery authority prefix required')
    check_pins();bridge=load_bridge()
    context=bridge.begin(args.evidence,args.evidence_sha,authority)
    args._recovery=context
    action(args)
    final=bridge.finish(context,authority);check_pins()
    write(out/'RECOVERY_EXECUTION_RECEIPT.json',dict(
        schema='hospital_ies.fixed3_supported252_execution.v1',
        original_registered_CASE_count=256,complete_native_CASE_count=252,
        known_constructor_failures_retained=4,original256_all_passed=False,
        recovery_evidence_index_path=str(Path(args.evidence).resolve()),
        recovery_evidence_index_sha256=args.evidence_sha,
        amendment_path=str(AMENDMENT),amendment_sha256=AMENDMENT_SHA,
        original_protocols_unchanged=True,final_recovery_verification=final,
        actual_physics_started=0,MATLAB='NOT_RUN',scientific_gate=False))


def need(ok,message):
    if not ok:raise ValueError(message)
def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda:f.read(1048576),b''):h.update(b)
    return h.hexdigest()
def checked(path,known):
    p=Path(path).resolve();need(sha(p)==known,'SHA mismatch: '+str(p));return p
def read(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))
def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)
def digest(value):return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()
def write(path,value):Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
def load(path,name,known):
    checked(path,known);spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def select_metadata(normal,boundary,plan):
    n,b=normal,boundary;s=n['original_scene_metadata']
    need(n['source_binding_sha256']==SOURCE and n['runtime_inputs_binding_sha256']==INPUTS,'Normal source/input differs')
    need(type(n['original_panel_scene_index_zero_based']) is int and n['original_panel_scene_index_zero_based']==0,'Not original first NORMAL')
    need(n['scene_id']==s['id']=='m21_normal_69f6a40aaa99e6c7b6ec' and n['window_minutes']==720. and s['duration_steps']*s['step_minutes']==720.,'Normal fixed identity/window differs')
    need(n['canonical_class']==s['canonical_class']=='NORMAL' and n['phase_expected_native']=='sensitivity','Normal phase/class differs')
    need(all(s.get(k)=={} for k in ['faults','auxiliary_faults','shock','shock_by_priority','shock_by_zone']) and s.get('exogenous_events')==[],'NORMAL has actual external fault/shock')
    need(n['normal_semantics']['reference_phase_clocks_are_analysis_only'] is True and n['normal_semantics']['actual_recovery_claimed'] is False,'Normal clock semantics differs')
    need(n['inherited_analysis_only_first_event_minute']==s['first_event_minute']==120. and n['inherited_analysis_only_recovery_start_minute']==s['recovery_start_minute']==480.,'Normal inherited analysis slot differs')
    need(digest(s)==n['scene_canonical_metadata_sha256'],'Normal original scene identity differs')
    policies={p['policy_id']:p for p in n['policies']}
    need(set(policies)=={'R_sci_v21','M_sci_v21','F_sci_v21','C_sci_v21','A_sci_v21'} and len(n['policies'])==5,'Complete normal five specifications required')
    need(n['configuration']['configuration_id']=='H0','Normal configuration not H0')
    row=dict(kind='normal',phase='sensitivity',scenario_id=s['id'],policy_id='C_sci_v21',configuration_id='H0',source_block_id=s['source_block_id'],window_minutes=720.,event_start_minute=None,recovery_start_minute=None,analysis_slot_minutes=[120.,480.],fault_target_label=s['fault_target'],fault_target_is_actual_fault=False,initial_reserve_kWh_shaft=None,expected_scenario_sha256=digest(s),expected_policy_sha256=policies['C_sci_v21']['policy_spec_canonical_sha256'],expected_configuration_sha256=n['configuration']['configuration_canonical_sha256'],selection='already fixed first NORMAL; C only for process, allfive remain in source',physical_result_status='PENDING')
    scene=plan['panel']['scenarios'][0]
    need(b['selected_without_outcome_ranking'] is True and b['conditional_analysis_only_after_full256_native_and_physical_and_current_byte_verification'] is True,'Boundary predeclaration scope differs')
    need(b['scenario_id']==scene['id']=='m21_boundary_a_5c16d21d8dff33fef94e','Not fixed067 boundary')
    need(b['configuration_ids']==['H0'] and b['policy_ids']==['C_sci_v21','A_sci_v21'],'Boundary complete C/A H0 axes changed')
    need(b['window_minutes']==scene['duration_steps']*scene['step_minutes']==900. and b['fault_start_minute']==scene['fault_start_minute']==60. and b['recovery_start_minute']==scene['recovery_start_minute']==780.,'Boundary900 clock differs')
    need(b['fault_duration_minutes']==scene['fault_duration_minutes']==720. and b['initial_reserve_kWh_shaft_equivalent']==scene['initial_reserve_kWh']==600.,'Boundary duration/shaft budget differs')
    need(digest(scene['faults'])==digest(dict(GRID=0.,GT_1=0.,GT_2=0.,GT_3=0.)) and scene['fault_target']=='GRID+ALL_GT','Fixed compound physical family differs')
    specs={p['policy_id']:p for p in plan['policy_specs']};need(list(specs)==b['policy_ids'] and set(plan['configurations'])=={'H0'},'Boundary selected specs/config differ')
    result=[row]
    for policy in b['policy_ids']:
        result.append(dict(kind='boundary',phase='boundary',scenario_id=scene['id'],policy_id=policy,configuration_id='H0',source_block_id=scene['source_block_id'],window_minutes=900.,event_start_minute=60.,recovery_start_minute=780.,analysis_slot_minutes=None,fault_target_label='GRID+ALL_GT',fault_target_is_actual_fault=True,initial_reserve_kWh_shaft=600.,expected_scenario_sha256=digest(scene),expected_policy_sha256=digest(specs[policy]),expected_configuration_sha256=digest(plan['configurations']['H0']),selection=b['selection_reason'],physical_result_status='PENDING'))
    return result

def metadata():
    checked(NORMAL,NORMAL_SHA);checked(BOUNDARY,BOUNDARY_SHA);n,b=read(NORMAL),read(BOUNDARY)
    checked(b['plan_path'],b['plan_sha256']);checked(b['jobs_path'],b['jobs_sha256'])
    return select_metadata(n,b,read(b['plan_path'])),n,b

def validate_h0_vectors(vectors):
    required={'ups_discharge_kW','ups_charge_kW','cold_discharge_kW','cold_charge_kW','cold_storage_pump_kW','ups_inventory_kWh','cold_inventory_kWh'}
    values={v['tag']:v for v in vectors['series']}
    need(required<=set(values),'Missing H0 storage field; never substitute zero')
    need(all(values[k]['y'] and all(type(x) in (float,int) and x==0. for x in values[k]['y']) for k in required),'Nonzero/unknown H0 storage cannot be labelled absent')
    return True

def relative_path(root,value):
    root=Path(root).resolve();rel=Path(value)
    need(not rel.is_absolute() and not rel.drive and not rel.root and '..' not in rel.parts and str(rel) not in ('','.'),'Unsafe relative local path')
    p=(root/rel).resolve();need(p.is_relative_to(root),'Resolved local path escapes root');return p

def full_pool(args):
    base=load(BASE_HELPER,'fixed3_native_pool_guard',BASE_HELPER_SHA)
    hw=load(HARDWARE_HELPER,'fixed3_native_vector_guard',HARDWARE_HELPER_SHA)
    context=getattr(args,'_recovery',None);need(isinstance(context,dict),'Recovery begin authority is required')
    config=context['config'];native=config['supported_native_analysis'];jobs=config['supported_jobs']
    for supplied,bound,label in [(args.jobs,jobs['path'],'Supported JOBS'),
        (args.parallel_receipt,native['path'],'Supported native'),
        (args.data_dir,Path(native['path']).parent/'tables','Canonical native tables')]:
        need(Path(supplied).resolve()==Path(bound).resolve(),label+' path differs')
    need(digest(args.jobs_sha)==digest(jobs['sha256']) and digest(args.parallel_sha)==digest(native['sha256']),'Supported known SHA differs')
    verified=base.validate_native(args.parallel_receipt,args.parallel_sha,args.jobs,args.jobs_sha,args.data_dir,252)
    selected,normal,boundary=metadata();indexed={(r['scenario_id'],r['policy_id'],r['configuration_id']):r for r in verified['case']}
    # Preserve and verify allfive first-NORMAL members, not just the chosen C plot.
    for p in normal['policies']:
        r=indexed[(normal['scene_id'],p['policy_id'],'H0')]
        need(r['policy_sha256']==p['policy_spec_canonical_sha256'] and r['scenario_sha256']==normal['scene_canonical_metadata_sha256'] and r['configuration_sha256']==normal['configuration']['configuration_canonical_sha256'],'Normal five-member source identity differs')
    output=[]
    for e in selected:
        r=indexed[(e['scenario_id'],e['policy_id'],'H0')]
        need(r['phase']==e['phase'] and float(r['window_minutes'])==e['window_minutes'] and r['source_block_id']==e['source_block_id'],'Fixed CASE window/source/phase differs')
        for k in ['scenario','policy','configuration']:need(r[k+'_sha256']==e['expected_'+k+'_sha256'],'Fixed CASE typed identity differs')
        output.append((e,r))
    return base,hw,verified,output

def plan(args):
    out=Path(args.output);need(not out.exists(),'Fresh plan output required');rows,n,b=metadata();out.mkdir(parents=True)
    write(out/'FIXED3_PLAN.json',dict(schema='hospital_ies.fixed3_metadata_plan.v1',cases=rows,normal_group_original_members=5,normal_allfive_source_retained=True,normal_protocol_path=str(NORMAL),normal_protocol_sha256=NORMAL_SHA,boundary_protocol_path=str(BOUNDARY),boundary_protocol_sha256=BOUNDARY_SHA,actual_values_read=False,physics=0,MATLAB='NOT_RUN',science_gate=False))
    print(json.dumps(dict(path=str(out/'FIXED3_PLAN.json'),sha256=sha(out/'FIXED3_PLAN.json'),cases=3,results='PENDING')))

def commands(args):
    _,_,_,selected=full_pool(args);checked(EXPORT,EXPORT_SHA);out=Path(args.output);need(not out.exists(),'Fresh command output required');out.mkdir(parents=True);jobs=read(args.jobs);cmd=[]
    for i,(e,r) in enumerate(selected,1):
        destination=out/'exports'/('%02d'%i)
        argv=[PYTHON,'-B',LOGGER,'--run-dir',str(out/'execution'),'--name','export_fixed_%02d'%i,'--',PYTHON,'-B',str(EXPORT),'--root',ROOT,'--result',r['result_path'],'--freeze',jobs['freeze_path'],'--expected-result-sha256',r['result_sha256'],'--expected-freeze-sha256',jobs['freeze_sha256'],'--expected-source-binding',SOURCE,'--output',str(destination)]
        cmd.append(dict(identity=e,command=argv,export_receipt_path=str(destination/'EXPORT_RECEIPT.json'),status='NOT_RUN'))
    write(out/'FIXED3_EXPORT_COMMANDS.json',dict(schema='hospital_ies.fixed3_export_commands.v1',commands=cmd,native_receipt_sha256=args.parallel_sha,jobs_sha256=args.jobs_sha,automatic_execution=False))
    print(json.dumps(dict(path=str(out/'FIXED3_EXPORT_COMMANDS.json'),sha256=sha(out/'FIXED3_EXPORT_COMMANDS.json'),executed=0)))

def command_plan(args,selected):
    p=checked(args.command_plan,args.command_plan_sha);cp=read(p)
    need(cp.get('schema')=='hospital_ies.fixed3_export_commands.v1' and cp.get('native_receipt_sha256')==args.parallel_sha and cp.get('jobs_sha256')==args.jobs_sha,'Fixed command authority differs')
    need(canonical([c['identity'] for c in cp['commands']])==canonical([e for e,_ in selected]),'Fixed command identity/order/clocks differ')
    return p,cp

def completed_index(args):
    _,hw,_,selected=full_pool(args);path,cp=command_plan(args,selected);records=[]
    for i,c in enumerate(cp['commands'],1):
        rp=path.parent/'execution/logs'/('export_fixed_%02d.command.json'%i);erp=Path(c['export_receipt_path']);er=read(erp)
        hw.validate_export_completion(c,read(rp),er)
        records.append(dict(identity=c['identity'],export_receipt_path=str(erp.resolve()),export_receipt_sha256=sha(erp),command_record_path=str(rp.resolve()),command_record_sha256=sha(rp)))
    checked(path,args.command_plan_sha);out=Path(args.output);need(not out.exists(),'Fresh index required');out.mkdir(parents=True)
    write(out/'FIXED3_COMPLETED_EXPORT_INDEX.json',dict(schema='hospital_ies.fixed3_completed_exports.v1',command_plan_sha256=args.command_plan_sha,native_receipt_sha256=args.parallel_sha,exports=records,actual_completed_native_exports=3))
    print(json.dumps(dict(path=str(out/'FIXED3_COMPLETED_EXPORT_INDEX.json'),sha256=sha(out/'FIXED3_COMPLETED_EXPORT_INDEX.json'))))

def make_local_index(manifest,manifest_path):
    root=Path(manifest_path).resolve().parent;result=[]
    for c in manifest['cases']:
        csv=checked(relative_path(root,c['csv_relative_path']),c['csv_sha256']);mat=checked(relative_path(root,c['mat_relative_path']),c['mat_sha256']);erp=checked(relative_path(root,c['export_receipt_relative_path']),c['export_receipt_sha256'])
        result.append(dict(csv_path=str(csv),mat_path=str(mat),export_receipt_path=str(erp),export_receipt_sha256=c['export_receipt_sha256']))
    need(len(result)==3 and len({v['csv_path'] for v in result})==3,'Exactly three distinct local PROCESS mappings required')
    return dict(schema='hospital_ies.explicit_fixed3_process_index.v1',original_receipts_rewritten=False,additional_PROCESS_are_not_original_native_export_members=True,processes=result)

def assemble(args):
    base,hw,verified,selected=full_pool(args);cp_path,cp=command_plan(args,selected);ip=checked(args.export_index,args.export_index_sha);ix=read(ip)
    need(ix.get('schema')=='hospital_ies.fixed3_completed_exports.v1' and ix.get('command_plan_sha256')==args.command_plan_sha and ix.get('native_receipt_sha256')==args.parallel_sha and len(ix.get('exports',[]))==3,'Externally pinned actual export index differs')
    need(canonical([v['identity'] for v in ix['exports']])==canonical([e for e,_ in selected]),'Completed3 export identities differ')
    out=Path(args.output);need(not out.exists(),'Fresh assembled directory required');out.mkdir(parents=True);table_dir=out/'tables'
    mapping=base.copy_members(verified['members'],table_dir);bound={m['original_path']:m['sha256'] for m in mapping};bound.update({m['local_path']:m['sha256'] for m in mapping})
    evidence=out/'evidence';evidence.mkdir()
    for src,name,expected in [(args.parallel_receipt,'ORIGINAL_PARALLEL_ANALYSIS_RECEIPT.json',args.parallel_sha),(args.jobs,'ORIGINAL_JOBS.json',args.jobs_sha)]:
        checked(src,expected);shutil.copyfile(src,evidence/name);bound[str(Path(src).resolve())]=expected;bound[str(evidence/name)]=expected
    cases=[]
    for i,((e,r),c,ei) in enumerate(zip(selected,cp['commands'],ix['exports']),1):
        erp=checked(ei['export_receipt_path'],ei['export_receipt_sha256']);need(erp==Path(c['export_receipt_path']).resolve(),'Export index path differs');er=read(erp)
        rp=checked(ei['command_record_path'],ei['command_record_sha256']);hw.validate_export_completion(c,read(rp),er)
        need(tuple(er.get(k) for k in ['scenario_id','policy_id','configuration_id'])==tuple(e[k] for k in ['scenario_id','policy_id','configuration_id']) and er['result_sha256']==r['result_sha256'] and er['raw_sha256']==r['raw_sha256'],'Actual export CASE binding differs')
        members=[]
        for name in ['PROCESS.csv','PROCESS.mat','PROCESS_RECEIPT.json']:
            rel='native_process/'+name;matches=[v for v in er['artifacts'] if v['path'].replace('\\','/')==rel];need(len(matches)==1,'Missing/ambiguous native PROCESS')
            p=checked(erp.parent/rel,matches[0]['sha256']);members.append(dict(path=str(p),relative=name,sha256=matches[0]['sha256']))
        folder=table_dir/('process_fixed_%02d'%i);copied=base.copy_members(members,folder);shutil.copyfile(erp,folder/'ORIGINAL_EXPORT_RECEIPT.json')
        vectors=hw.process_vectors(folder,e['window_minutes']);validate_h0_vectors(vectors);need(vectors['raw_sha256']==er['raw_sha256'],'Native MAT raw identity differs')
        cases.append({**e,'physical_result_status':'completed','H0_storage_hardware_absent':True,'csv_relative_path':(folder/'PROCESS.csv').relative_to(out).as_posix(),'mat_relative_path':(folder/'PROCESS.mat').relative_to(out).as_posix(),'export_receipt_relative_path':(folder/'ORIGINAL_EXPORT_RECEIPT.json').relative_to(out).as_posix(),'csv_sha256':sha(folder/'PROCESS.csv'),'mat_sha256':sha(folder/'PROCESS.mat'),'export_receipt_sha256':ei['export_receipt_sha256'],'result_path':r['result_path'],'result_sha256':r['result_sha256'],'raw_sha256':r['raw_sha256']})
        for m in copied:bound[m['original_path']]=m['sha256'];bound[m['local_path']]=m['sha256']
        bound[str(erp)]=ei['export_receipt_sha256'];bound[str(folder/'ORIGINAL_EXPORT_RECEIPT.json')]=ei['export_receipt_sha256'];bound[str(rp)]=ei['command_record_sha256']
    manifest=dict(schema='hospital_ies.fixed3_actual_process_manifest.v2',source_binding_sha256=SOURCE,complete_native_CASE_count=252,original_registered_CASE_count=256,known_constructor_failures_retained=4,original256_all_passed=False,recovery_evidence_index_sha256=args.evidence_sha,recovery_master_metadata_fingerprint=args._recovery['master']['master_metadata_fingerprint'],original_native_receipt_sha256=args.parallel_sha,normal_predeclaration_sha256=NORMAL_SHA,boundary_predeclaration_sha256=BOUNDARY_SHA,data_directory_relative='tables',cases=cases,all_native_tables_preserved=True,normal_allfive_retained_in_source=True,additional_PROCESS_not_original_table_export=True,original_receipts_rewritten=False,scientific_gate=False,physics=0,MATLAB='NOT_RUN')
    mp=out/'FIXED3_ACTUAL_MANIFEST.json';write(mp,manifest);write(out/'PROCESS_INDEX.json',make_local_index(manifest,mp));write(out/'TABLE_BYTE_MIRROR.json',dict(members=mapping,scope='complete supported252 native tables; original256 failure ledger remains separate and retained; excluding explicitly additional3 PROCESS'))
    for p,h in bound.items():checked(p,h)
    checked(cp_path,args.command_plan_sha);checked(ip,args.export_index_sha)
    print(json.dumps(dict(manifest=str(mp),manifest_sha256=sha(mp),process_index=str(out/'PROCESS_INDEX.json'),process_index_sha256=sha(out/'PROCESS_INDEX.json'),matlab='NOT_RUN')))

def local_index(args):
    p=checked(args.manifest,args.manifest_sha);m=read(p);need(m.get('schema')=='hospital_ies.fixed3_actual_process_manifest.v2' and digest([m.get(k) for k in ['complete_native_CASE_count','original_registered_CASE_count','known_constructor_failures_retained','original256_all_passed']])==digest([252,256,4,False]),'Wrong explicit supported252 local manifest')
    out=Path(args.output);need(not out.exists(),'Fresh local index output required');write(out,make_local_index(m,p));checked(p,args.manifest_sha)
    print(json.dumps(dict(path=str(out),sha256=sha(out),original_index_modified=False)))

def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='mode',required=True)
    for name in ['plan','commands','completed_index','assemble','local_index']:
        q=sub.add_parser(name);q.add_argument('--output',required=True)
        if name not in ['plan','local_index']:
            for field in ['parallel-receipt','parallel-sha','jobs','jobs-sha','data-dir','evidence','evidence-sha']:q.add_argument('--'+field,required=True)
        if name in ['completed_index','assemble']:q.add_argument('--command-plan',required=True);q.add_argument('--command-plan-sha',required=True)
        if name=='assemble':q.add_argument('--export-index',required=True);q.add_argument('--export-index-sha',required=True)
        if name=='local_index':q.add_argument('--manifest',required=True);q.add_argument('--manifest-sha',required=True)
    a=p.parse_args()
    if a.mode in ['plan','local_index']:globals()[a.mode](a)
    else:run_bound(a,globals()[a.mode])

if __name__=='__main__':main()
