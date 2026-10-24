"""Plan HW18/native exports and materialize recorded PROCESS; never launch it."""
import argparse
import csv
from datetime import datetime
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import shutil
import numpy as np
from scipy.io import loadmat

HERE=Path(__file__).resolve().parent
BASE=Path('E:/hospital_ies_v21_20261023')
PROTOCOL=BASE/'reviews/main02_analysis_reporting01/RAW_REPRESENTATIVE_PREDECLARATION03.json'
PROTOCOL_SHA='6390b96a0cd01d27129591a51340e9489db5d57e88e606c9828b4dfb0d2cf1fb'
PANEL=BASE/'staging_panels/candidate04/HARDWARE_PROPOSAL.json'
PANEL_SHA='345de5259ed5dd1b1218432d4ea2ff36b3ddc8de2441738f1a0564e5dbb6e339'
SOURCE='4dfe62ae105d2696b7782dc36c83ec63c6551e2cded601d6f15c0d4083058add'
NATIVE_EXPORT=BASE/'reviews/event_timeline_export01/export_event_timeline.py'
NATIVE_EXPORT_SHA='cec0770e6a21a97f49d6e0a12a5c403188711ca2e5c4a2202877148606d2a0cb'
CORE_ROOT='D:/hospital_ies_v21_20261023'
PYTHON='C:/Users/HanqinXie/AppData/Local/Programs/Python/Python310/python.exe'
LOGGER=CORE_ROOT+'/tools/p0_audit/run_logged.py'

def sha(p):
    h=hashlib.sha256()
    with Path(p).open('rb') as f:
        for b in iter(lambda:f.read(1048576),b''):h.update(b)
    return h.hexdigest()

def checked(p,h):
    p=Path(p).resolve()
    if sha(p)!=h:raise ValueError('SHA mismatch: '+str(p))
    return p

def read(p):return json.loads(Path(p).read_text(encoding='utf-8-sig'))
def write(p,v):Path(p).write_text(json.dumps(v,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')

def protocol_entries(protocol,panel):
    scenes={s['id']:s for s in panel['scenarios']};out=[]
    groups=[g for g in protocol['groups'] if g['kind']=='hardware']
    if len(groups)!=2:raise ValueError('Exactly two predeclared hardware groups required')
    for g in groups:
        if len(g['scene_ids'])!=1 or set(g['policy_ids'])!={'C_sci_v21','A_sci_v21','M_sci_v21'} or set(g['configuration_ids'])!={'H0','H1','H2'} or g['expected_complete_members']!=9:raise ValueError('Predeclared hardware member axes changed')
        sid=g['scene_ids'][0];s=scenes[sid]
        if g['window_minutes']!=360 or s['duration_steps']*s['step_minutes']!=360 or s['fault_start_minute']!=60 or s['recovery_start_minute']!=180 or g['fault_target']!=s['fault_target'] or g['source_block_id']!=s['source_block_id']:raise ValueError('Predeclared hardware window/physical identity differs')
        for policy in g['policy_ids']:
            for config in g['configuration_ids']:
                out.append(dict(group_id=g['group_id'],scenario_id=sid,policy_id=policy,configuration_id=config,source_block_id=s['source_block_id'],fault_target=s['fault_target'],window_minutes=360.,event_start_minute=60.,event_end_minute=180.,selection_rule=g['selection_rule'],physical_result_status='PENDING'))
    if len(out)!=18 or len({(r['scenario_id'],r['policy_id'],r['configuration_id']) for r in out})!=18:raise ValueError('HW18 identity collision')
    return out

def entries():
    checked(PROTOCOL,PROTOCOL_SHA);checked(PANEL,PANEL_SHA)
    return protocol_entries(read(PROTOCOL),read(PANEL))

def process_vectors(directory,horizon):
    directory=Path(directory);mat=loadmat(directory/'PROCESS.mat')
    with (directory/'PROCESS.csv').open(encoding='utf-8-sig',newline='') as f:rows=list(csv.DictReader(f))
    if not rows:raise ValueError('Empty native PROCESS')
    columns=[str(np.asarray(v).reshape(-1)[0]) for v in mat['columns'].reshape(-1)]
    matrix=np.array([[float(row[c]) if row.get(c) not in ('',None) else np.nan for c in columns] for row in rows])
    values=mat['values']
    if matrix.shape!=values.shape or not np.array_equal(np.isnan(matrix),np.isnan(values)) or not np.allclose(matrix,values,atol=1e-12,rtol=0,equal_nan=True):raise ValueError('Native CSV/MAT numeric or unknown-mask mismatch')
    def field(key):
        if key not in columns:raise ValueError('Missing native field: '+key)
        v=matrix[:,columns.index(key)]
        if not np.isfinite(v).all():raise ValueError('Missing/nonfinite native field: '+key)
        return v
    s,e=field('start_minute'),field('end_minute')
    if abs(s[0])>1e-7 or abs(e[-1]-horizon)>1e-7 or np.any(e<=s) or not np.allclose(s[1:],e[:-1],atol=1e-7,rtol=0):raise ValueError('Native horizon/gap invalid')
    x=np.column_stack([s,e]).reshape(-1).tolist();series=[]
    def add(tag,tile,v,kind='native_half_open_interval',xx=None):
        series.append(dict(tag=tag,tile=tile,kind=kind,x=x if xx is None else xx,y=np.repeat(v,2).tolist() if xx is None else np.asarray(v).tolist()))
    for tile,c in enumerate('ehc',1):
        request=field('P0_'+c+'_requested_kW')+field('P1_'+c+'_requested_kW')
        delivered=field('P0_'+c+'_delivered_kW')+field('P1_'+c+'_delivered_kW')
        if np.any(delivered < -1e-7) or np.any(delivered>request+1e-7):raise ValueError('Native service ledger invalid')
        add('critical_'+c+'_requested',tile,request);add('critical_'+c+'_delivered',tile,delivered)
    for tag,v in [('grid_import_kW',field('grid_import_kW')),('actual_GT_total_kW',sum(field('gt_electric_kW_'+str(k)) for k in range(1,4))),('diesel_kW',field('diesel_kW')),('ups_source_discharge_kW',field('ups_discharge_kW'))]:add(tag,4,v)
    for key in ['ups_discharge_kW','ups_charge_kW']:add(key,5,field(key))
    for key in ['cold_discharge_kW','cold_charge_kW','cold_storage_pump_kW']:add(key,6,field(key))
    for tile,prefix,tag in [(7,'ups','ups_inventory_kWh'),(8,'cold_store','cold_inventory_kWh'),(9,'diesel','dg_shaft_inventory_kWh')]:
        before=field('pre_diesel_energy_remaining_kWh' if prefix=='diesel' else prefix+'_initial_kWh')
        after=field('post_diesel_energy_remaining_kWh' if prefix=='diesel' else prefix+'_final_kWh')
        if not np.allclose(before[1:],after[:-1],atol=1e-7,rtol=0):raise ValueError('Native inventory discontinuity')
        add(tag,tile,np.r_[before[0],after],kind='native_endpoint_points',xx=np.r_[s[0],e].tolist())
    return dict(native_intervals=len(rows),raw_sha256=str(np.asarray(mat['raw_sha256']).reshape(-1)[0]),series=series,CSV_MAT_unknown_mask_equal=True,interpolated=False,zero_filled=False)

def load_base():
    path=HERE.parent/'prepare_remaining.py';checked(path,'4db5ef1e37674799a5b4e947920488271fb3d32a3da0fd7a1bda298c552b0505')
    spec=importlib.util.spec_from_file_location('bound_native_mirror_guard',path);mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    return mod

def full_pool(args):
    base=load_base();v=base.validate_native(args.parallel_receipt,args.parallel_sha,args.jobs,args.jobs_sha,args.data_dir,126);base.check_hardware_rows(v['case'])
    cases={(r['scenario_id'],r['policy_id'],r['configuration_id']):r for r in v['case']}
    selected=[]
    for e in entries():
        r=cases[(e['scenario_id'],e['policy_id'],e['configuration_id'])]
        if r['phase']!='hardware' or float(r['window_minutes'])!=360 or r['source_block_id']!=e['source_block_id'] or r['fault_target']!=e['fault_target']:raise ValueError('Actual hardware CASE/protocol mismatch')
        selected.append((e,r))
    return base,v,selected

def validate_export_completion(command,record,export):
    argv=command['command'];expected=argv[argv.index('--')+1:]
    if not isinstance(record.get('command'),list) or not all(type(v) is str for v in record['command']) or record['command']!=expected:raise ValueError('Actual native export command differs')
    if type(record.get('exit_code')) is not int or record['exit_code']!=0:raise ValueError('Actual export failed/unknown/noncanonical exit')
    finished=record.get('finished_utc')
    if not isinstance(finished,str) or not finished:raise ValueError('Actual export has no terminal time')
    stamp=datetime.fromisoformat(finished.replace('Z','+00:00'))
    if stamp.tzinfo is None:raise ValueError('Actual export terminal time is timezone-naive')
    if export.get('status')!='completed' or export.get('source_binding_sha256')!=SOURCE or export.get('source_and_inputs_unchanged_after_export') is not True:raise ValueError('Actual native export identity/status differs')
    for field,value in [('source_files_checked',643),('runtime_inputs_checked',28)]:
        if type(export.get(field)) is not int or export[field]!=value:raise ValueError('Actual native export source/input count differs')
    return True

def validate_command_identities(command_plan,planned):
    actual=[c.get('identity') for c in command_plan.get('commands',[])]
    canonical=lambda v:json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
    if len(actual)!=18 or canonical(actual)!=canonical(planned):raise ValueError('Actual command identity/order/clock differs from protocol03')
    return True

def plan(args):
    out=Path(args.output)
    if out.exists():raise FileExistsError('Fresh metadata output required')
    out.mkdir(parents=True)
    result=dict(schema='hospital_ies.hardware18_metadata_plan.v1',protocol_path=str(PROTOCOL),protocol_sha256=PROTOCOL_SHA,panel_path=str(PANEL),panel_sha256=PANEL_SHA,entries=entries(),physical_attempts_started=0,matlab_execution='NOT_RUN',formal_HARDWARE_results_read=False,scientific_acceptance=False)
    write(out/'HW18_PLAN.json',result)
    print(json.dumps(dict(path=str(out/'HW18_PLAN.json'),sha256=sha(out/'HW18_PLAN.json'),entries=18,results='PENDING')))

def commands(args):
    _,_,selected=full_pool(args);checked(NATIVE_EXPORT,NATIVE_EXPORT_SHA)
    out=Path(args.output)
    if out.exists():raise FileExistsError('Fresh command output required')
    out.mkdir(parents=True);jobs=read(args.jobs);result=[]
    for i,(e,r) in enumerate(selected,1):
        destination=out/'exports'/('%02d'%i)
        argv=[PYTHON,'-B',LOGGER,'--run-dir',str(out/'execution'),'--name','export_hw_%02d'%i,'--',PYTHON,'-B',str(NATIVE_EXPORT),'--root',CORE_ROOT,'--result',r['result_path'],'--freeze',jobs['freeze_path'],'--expected-result-sha256',r['result_sha256'],'--expected-freeze-sha256',jobs['freeze_sha256'],'--expected-source-binding',SOURCE,'--output',str(destination)]
        result.append(dict(identity=e,command=argv,export_receipt_path=str(destination/'EXPORT_RECEIPT.json'),status='NOT_RUN'))
    write(out/'HW18_EXPORT_COMMANDS.json',dict(schema='hospital_ies.hardware18_export_commands.v1',commands=result,native_receipt_path=str(Path(args.parallel_receipt).resolve()),native_receipt_sha256=args.parallel_sha,jobs_path=str(Path(args.jobs).resolve()),jobs_sha256=args.jobs_sha,export_helper_path=str(NATIVE_EXPORT),export_helper_sha256=NATIVE_EXPORT_SHA,automatic_execution=False,scientific_gate=False))
    print(json.dumps(dict(commands=18,path=str(out/'HW18_EXPORT_COMMANDS.json'),sha256=sha(out/'HW18_EXPORT_COMMANDS.json'),executed=0)))

def completed_index(args):
    _,_,selected=full_pool(args);checked(NATIVE_EXPORT,NATIVE_EXPORT_SHA)
    command_path=checked(args.command_plan,args.command_plan_sha);cp=read(command_path)
    if cp['native_receipt_sha256']!=args.parallel_sha or cp['jobs_sha256']!=args.jobs_sha or len(cp['commands'])!=18:raise ValueError('Command/native authority differs')
    validate_command_identities(cp,[e for e,_ in selected])
    result=[]
    for i,c in enumerate(cp['commands'],1):
        record_path=command_path.parent/'execution/logs'/('export_hw_%02d.command.json'%i)
        record=read(record_path);erp=Path(c['export_receipt_path']);er=read(erp)
        validate_export_completion(c,record,er)
        result.append(dict(identity=c['identity'],export_receipt_path=str(erp.resolve()),export_receipt_sha256=sha(erp),command_record_path=str(record_path.resolve()),command_record_sha256=sha(record_path)))
    checked(command_path,args.command_plan_sha);checked(args.parallel_receipt,args.parallel_sha);checked(args.jobs,args.jobs_sha)
    out=Path(args.output)
    if out.exists():raise FileExistsError('Fresh completed-export index output required')
    out.mkdir(parents=True)
    write(out/'HW18_COMPLETED_EXPORT_INDEX.json',dict(schema='hospital_ies.hardware18_completed_exports.v1',command_plan_sha256=args.command_plan_sha,native_receipt_sha256=args.parallel_sha,exports=result,all_native_export_commands_terminal_zero=True,scientific_acceptance=False))
    print(json.dumps(dict(path=str(out/'HW18_COMPLETED_EXPORT_INDEX.json'),sha256=sha(out/'HW18_COMPLETED_EXPORT_INDEX.json'),actual_native_exports=18,physics=0)))

def assemble(args):
    base,_,selected=full_pool(args)
    command_path=checked(args.command_plan,args.command_plan_sha);cp=read(command_path)
    if cp['native_receipt_sha256']!=args.parallel_sha or cp['jobs_sha256']!=args.jobs_sha or len(cp['commands'])!=18:raise ValueError('Command/native authority differs')
    expected_ids={(e['scenario_id'],e['policy_id'],e['configuration_id']) for e,_ in selected}
    listed={(c['identity']['scenario_id'],c['identity']['policy_id'],c['identity']['configuration_id']) for c in cp['commands']}
    if listed!=expected_ids:raise ValueError('18 original command identities differ')
    validate_command_identities(cp,[e for e,_ in selected])
    index_path=checked(args.export_index,args.export_index_sha);index=read(index_path)
    if index.get('schema')!='hospital_ies.hardware18_completed_exports.v1' or index.get('command_plan_sha256')!=args.command_plan_sha or index.get('native_receipt_sha256')!=args.parallel_sha or index.get('all_native_export_commands_terminal_zero') is not True or len(index.get('exports',[]))!=18:raise ValueError('Externally pinned actual export index differs')
    by_export={tuple(v['identity'][k] for k in ('scenario_id','policy_id','configuration_id')):v for v in index['exports']}
    if len(by_export)!=18 or set(by_export)!=expected_ids:raise ValueError('Completed export identity index differs')
    out=Path(args.output)
    if out.exists():raise FileExistsError('Fresh assembled data output required')
    out.mkdir(parents=True);cases=[];bound={};by_id={(r['scenario_id'],r['policy_id'],r['configuration_id']):r for _,r in selected}
    for i,c in enumerate(cp['commands'],1):
        meta=c['identity'];key=tuple(meta[k] for k in ('scenario_id','policy_id','configuration_id'));r=by_id[key]
        ei=by_export[key];erp=checked(ei['export_receipt_path'],ei['export_receipt_sha256']);er=read(erp)
        if erp!=Path(c['export_receipt_path']).resolve():raise ValueError('Actual export index path differs')
        record_path=checked(ei['command_record_path'],ei['command_record_sha256']);validate_export_completion(c,read(record_path),er)
        bound[str(erp)]=ei['export_receipt_sha256'];bound[str(record_path)]=ei['command_record_sha256']
        if tuple(er.get(k) for k in ('scenario_id','policy_id','configuration_id'))!=key or er.get('result_sha256')!=r['result_sha256'] or er.get('raw_sha256')!=r['raw_sha256']:raise ValueError('Actual export CASE identity differs')
        members=[]
        for name in ['PROCESS.csv','PROCESS.mat','PROCESS_RECEIPT.json']:
            rel='native_process/'+name;found=[a for a in er['artifacts'] if a['path'].replace('\\','/')==rel]
            if len(found)!=1:raise ValueError('Missing/ambiguous native PROCESS artifact')
            p=checked(erp.parent/rel,found[0]['sha256']);members.append(dict(path=str(p),relative=name,sha256=found[0]['sha256']))
            bound[str(p)]=found[0]['sha256']
        folder=out/'data'/('%02d'%i);mapping=base.copy_members(members,folder)
        shutil.copyfile(erp,folder/'ORIGINAL_EXPORT_RECEIPT.json')
        for m in mapping:bound[m['local_path']]=m['sha256']
        bound[str(folder/'ORIGINAL_EXPORT_RECEIPT.json')]=ei['export_receipt_sha256']
        vectors=process_vectors(folder,360.)
        if vectors['raw_sha256']!=er['raw_sha256']:raise ValueError('Native MAT raw identity differs')
        actual={**meta,'physical_result_status':'completed','folder':folder.relative_to(out).as_posix(),'raw_sha256':er['raw_sha256'],'result_path':er['result_path'],'result_sha256':er['result_sha256'],'export_receipt_original_path':str(erp.resolve()),'export_receipt_sha256':sha(erp),'csv_sha256':sha(folder/'PROCESS.csv'),'mat_sha256':sha(folder/'PROCESS.mat'),'expected_vectors':vectors,'output_name':'HW18_%02d_%s_%s'%(i,meta['policy_id'],meta['configuration_id'])}
        cases.append(actual)
    checked(args.parallel_receipt,args.parallel_sha);checked(args.jobs,args.jobs_sha);checked(command_path,args.command_plan_sha);checked(index_path,args.export_index_sha)
    for p,h in bound.items():checked(p,h)
    manifest=dict(schema='hospital_ies.hardware18_actual_process_manifest.v1',source_binding_sha256=SOURCE,protocol_sha256=PROTOCOL_SHA,native_full126_receipt_sha256=args.parallel_sha,whole_hardware_pool_complete=True,source_CASE_count=126,cases=cases,formal_hardware_comparison=True,old_functional_graphs_reused_as_results=False,science_gate=False,physics_attempts_started=0,matlab_execution='NOT_RUN')
    write(out/'HW18_ACTUAL_MANIFEST.json',manifest)
    print(json.dumps(dict(path=str(out/'HW18_ACTUAL_MANIFEST.json'),sha256=sha(out/'HW18_ACTUAL_MANIFEST.json'),cases=18,matlab='NOT_RUN')))

def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='mode',required=True)
    for name in ['plan','commands','completed_index','assemble']:
        q=sub.add_parser(name);q.add_argument('--output',required=True)
        if name!='plan':
            for field in ['parallel-receipt','parallel-sha','jobs','jobs-sha','data-dir']:q.add_argument('--'+field,required=True)
        if name in ['completed_index','assemble']:q.add_argument('--command-plan',required=True);q.add_argument('--command-plan-sha',required=True)
        if name=='assemble':q.add_argument('--export-index',required=True);q.add_argument('--export-index-sha',required=True)
    a=p.parse_args();globals()[a.mode](a)

if __name__=='__main__':main()
