"""Export exact nonsealed archived PLAN input JSON; never freeze/register/run."""
from __future__ import annotations
import argparse
import hashlib
import importlib
import json
import math
from pathlib import Path, PureWindowsPath
import re
import sys

sys.dont_write_bytecode=True
SOURCE='4dfe62ae105d2696b7782dc36c83ec63c6551e2cded601d6f15c0d4083058add'
INPUTS='659ecc488bda87c9338afde283cbb74135ca2ebdf2769ce463d0e61f0c6288d8'
FIELDS={'panel':'PANEL.json','policy_specs':'POLICY_SPECS.json','configurations':'CONFIGURATIONS.json'}


def need(condition,message):
    if not condition:raise ValueError(message)


def sha(path):
    value=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):value.update(chunk)
    return value.hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,
        separators=(',',':'),allow_nan=False).encode('utf8')).hexdigest()


def unique(pairs):
    value={}
    for key,item in pairs:
        need(key not in value,'Duplicate JSON key is ambiguous')
        value[key]=item
    return value


def decoder():
    def invalid(value):raise ValueError('Nonfinite JSON constant: '+value)
    return json.JSONDecoder(object_pairs_hook=unique,parse_constant=invalid)


def extract_fields(body):
    text=body.decode('utf-8-sig');decode=decoder();value=decode.decode(text)
    need(isinstance(value,dict),'PLAN must be an object')
    def whitespace(i):
        while i<len(text) and text[i].isspace():i+=1
        return i
    i=whitespace(0);need(text[i]=='{','PLAN object required');i=whitespace(i+1);result={}
    while text[i]!='}':
        key,end=decode.raw_decode(text,i);i=whitespace(end)
        need(text[i]==':','PLAN field separator missing');start=whitespace(i+1)
        _,end=decode.raw_decode(text,start)
        if key in FIELDS:result[key]=text[start:end].encode('utf8')
        i=whitespace(end)
        if text[i]=='}':break
        need(text[i]==',','PLAN member separator missing');i=whitespace(i+1)
    need(set(result)==set(FIELDS),'Three actual PLAN input fields required')
    return result


def signed(value,label):
    need(isinstance(value,dict) and value.get('signature')==digest({k:v for k,v in value.items() if k!='signature'}),label+' signature differs')


def validate_relative_paths(mapping):
    need(isinstance(mapping,dict) and bool(mapping),'Nonempty frozen file mapping required')
    for name in mapping:
        need(isinstance(name,str) and bool(name),'Frozen path must be a nonempty string')
        win=PureWindowsPath(name);local=Path(name)
        need(not win.drive and not win.root and not win.is_absolute() and not local.is_absolute()
            and '..' not in win.parts and '..' not in local.parts,'Frozen mapping path escape')


def validate_archive(plan,freeze,expected_freeze_sha,source_support,sealed_days):
    signed(plan,'PLAN');signed(freeze,'FREEZE')
    need(plan.get('schema')=='hospital_ies.sci_runner.v21.v1.plan'
        and freeze.get('schema')=='hospital_ies.sci_runner.v21.v1.freeze','Original native M21 metadata required')
    need(plan.get('model_id')==freeze.get('model_id')=='M21_sci_engineering','Model identity differs')
    for key in ('source_sha256','runtime_inputs'):validate_relative_paths(freeze[key])
    need(len(freeze['source_sha256'])==643 and digest(freeze['source_sha256'])==SOURCE,'Only archived source4dfe643 is supported')
    need(len(freeze['runtime_inputs'])==28 and digest(freeze['runtime_inputs'])==INPUTS,'Only archived input65928 is supported')
    need(plan.get('freeze_sha256')==expected_freeze_sha and plan.get('freeze_signature')==freeze['signature'],'Explicit FREEZE does not match PLAN')
    need(plan.get('phase') in ('pilot','development','formal','mechanism','boundary','hardware','sensitivity','reproduction'),
        'Final-blind or unsupported PLAN cannot be exported for reproduction')
    need(plan.get('final_release_grant_signature') is None,'Final release cannot be transferred to reproduction')
    panel=plan['panel'];signed(panel,'PANEL')
    scenes=panel.get('scenarios');need(isinstance(scenes,list) and bool(scenes),'Original nonempty panel required')
    need(len({s['id'] for s in scenes})==len(scenes),'Duplicate scene identity')
    for scene in scenes:
        need(scene.get('is_final_blind',False) is False and scene.get('original_final12_evaluated',False) is False,
            'Final-blind scene or final outcome cannot be replayed by this exporter')
        need(all(scene.get(k) not in ('final','final_blind') for k in ('role','experiment_role','purpose')),'Final role refused')
        need(type(scene.get('start_hour')) is int and 1<=scene['start_hour']<=8760,'Source hour must be a real source-year integer')
        need(type(scene.get('duration_steps')) is int and scene['duration_steps']>0,'Positive integer duration required')
        need(type(scene.get('step_minutes',15.)) in (int,float) and math.isfinite(scene.get('step_minutes',15.))
            and scene.get('step_minutes',15.)>0,'Invalid registered step')
        need(all(type(h) is int and 1<=h<=8760 for h in scene.get('initialization_source_hours',[])),'Initialization source hours malformed')
        support=source_support(scene)
        need(not any(support['raw_start_minute']<(day+1)*1440 and day*1440<support['raw_end_minute'] for day in sealed_days),
            'Native complete source support intersects old sealed12; grant cannot be bypassed')
    specs=plan.get('policy_specs');configs=plan.get('configurations')
    need(isinstance(specs,list) and bool(specs) and isinstance(configs,dict) and bool(configs),'Actual spec/config sets required')
    pool={p['policy_id']:p for p in freeze['policy_specs']}
    need(len(pool)==len(freeze['policy_specs']) and len({p['policy_id'] for p in specs})==len(specs),'Duplicate policy identities')
    for spec in specs:
        need(spec['policy_id'] in pool and digest(spec)==digest(pool[spec['policy_id']]),'Policy specification changed from original FREEZE')
    for name,config in configs.items():
        need(name in freeze['configurations'] and digest(config)==digest(freeze['configurations'][name]),'Configuration changed from original FREEZE')
    need(all(re.fullmatch(r'[A-Za-z0-9_.-]+',s['policy_id']) for s in specs)
        and all(re.fullmatch(r'[A-Za-z0-9_.-]+',name) for name in configs),'Identifiers cannot be safely represented in native CLI lists')
    pairs=len(scenes)*len(specs)*len(configs)
    need(type(plan.get('registered_pairs')) is int and plan['registered_pairs']==pairs,'Original Cartesian pair count differs')
    return pairs


def native(root,freeze):
    for relative in ('tools/sci_engineering/design.py','tools/sci_engineering/runner.py','tools/sci_engineering/contracts.py'):
        need(sha(root/relative)==freeze['source_sha256'][relative],'Native metadata helper differs before import')
    sys.path[:0]=[str(root),str(root/'src')]
    runner=importlib.import_module('tools.sci_engineering.runner')
    design=importlib.import_module('tools.sci_engineering.design')
    need(Path(runner.__file__).resolve()==root/'tools/sci_engineering/runner.py'
        and Path(design.__file__).resolve()==root/'tools/sci_engineering/design.py','Native import came from another root')
    return runner,design


def export(root,plan_path,plan_sha,freeze_path,freeze_sha,output,future_root,wall_hours,attempt_limit=None):
    root=Path(root).resolve();plan_path=Path(plan_path).resolve();freeze_path=Path(freeze_path).resolve()
    output=Path(output).resolve();future_root=Path(future_root).resolve()
    if output.exists():raise FileExistsError('Create-only metadata output; retain old files')
    need(type(wall_hours) in (int,float) and math.isfinite(wall_hours) and wall_hours>0,'Future wall budget must be explicit, finite and positive')
    need(sha(plan_path)==plan_sha and sha(freeze_path)==freeze_sha,'External known PLAN/FREEZE SHA differs')
    plan_body=plan_path.read_bytes();freeze_body=freeze_path.read_bytes()
    plan=decoder().decode(plan_body.decode('utf-8-sig'));freeze=decoder().decode(freeze_body.decode('utf-8-sig'))
    signed(plan,'PLAN');signed(freeze,'FREEZE')
    for key in ('source_sha256','runtime_inputs'):validate_relative_paths(freeze[key])
    need(digest(freeze['source_sha256'])==SOURCE and digest(freeze['runtime_inputs'])==INPUTS,'Archived source/input identity differs')
    runner,design=native(root,freeze)
    pairs=validate_archive(plan,freeze,freeze_sha,design.source_support,design.SEALED_DAYS)
    need(attempt_limit is None or (type(attempt_limit) is int and attempt_limit>=pairs),'Future attempt budget must cover the exact original panel')
    limit=pairs if attempt_limit is None else attempt_limit
    old_paths=[root,plan_path.parent,freeze_path.parent]
    for name in ('raw_root','budget_scope'):
        path=Path(plan.get(name,''))
        if path.is_absolute():old_paths.append(path.resolve())
    need(not any(output.is_relative_to(path) for path in old_paths),'Output would modify original repository/archive/campaign')
    need(not future_root.exists() and not any(future_root.is_relative_to(path) for path in old_paths),'Future run root exists or reuses original source/archive/budget/raw directory')
    need(not output.is_relative_to(future_root) and not future_root.is_relative_to(output),'Input export and future execution roots must be disjoint')
    runner.verify_freeze(root,freeze)  # hash/metadata only: no clock reservation, new freeze, registration or episode
    need(digest(runner.normalize_panel(plan['panel']))==digest(plan['panel']),'Original panel is not idempotent under the unchanged native registration adapter')
    pieces=extract_fields(plan_body)
    for name,body in pieces.items():need(digest(decoder().decode(body.decode('utf8')))==digest(plan[name]),'Exact field slice changed its value')
    need(sha(plan_path)==plan_sha and sha(freeze_path)==freeze_sha,'Archive changed while read')
    output.mkdir(parents=True,exist_ok=False)
    for name,file in FIELDS.items():
        with (output/file).open('xb') as stream:stream.write(pieces[name])
    policy_ids=','.join(s['policy_id'] for s in plan['policy_specs']);config_ids=','.join(plan['configurations'])
    commands=[['python','scripts/sci_campaign.py','freeze','--output',str(future_root/'NEW_FREEZE.json'),
        '--policy-specs',str(output/'POLICY_SPECS.json'),'--configurations',str(output/'CONFIGURATIONS.json'),
        '--policies',policy_ids,'--attempt-limit',str(limit),'--wall-hours',str(wall_hours)],
        ['python','scripts/sci_campaign.py','register','--panel',str(output/'PANEL.json'),'--freeze',str(future_root/'NEW_FREEZE.json'),
         '--directory',str(future_root/'experiment'),'--raw-root',str(future_root/'raw'),'--policies',policy_ids,
         '--configurations',config_ids,'--phase','reproduction'],
        ['python','tools/p0_audit/run_logged.py','--run-dir',str(future_root/'audit'),'--name','reproduction01','--',
         'python','scripts/sci_campaign.py','run','--directory',str(future_root/'experiment')]]
    values=dict(schema='hospital_ies.reproduction_input_export.v1',status='inputs_exported_only',original_plan_path=str(plan_path),
        original_plan_sha256=plan_sha,original_freeze_path=str(freeze_path),original_freeze_sha256=freeze_sha,
        recorded_original_freeze_path_not_dereferenced=plan['freeze_path'],original_phase=plan['phase'],future_phase='reproduction',
        source_binding_sha256=SOURCE,input_binding_sha256=INPUTS,source_files_checked=643,runtime_inputs_checked=28,
        panel_scenarios=len(plan['panel']['scenarios']),original_registered_pairs=pairs,policy_ids=policy_ids,configuration_ids=config_ids,
        exact_original_JSON_field_bytes_preserved=True,native_panel_normalization_idempotent=True,native_sealed_days=list(design.SEALED_DAYS),
        future_run_root=str(future_root),future_budget_arguments_only=dict(max_actual_attempts=limit,max_experiment_wall_hours=wall_hours),
        original_budget_not_reused=True,current_study_attempt_or_budget_added=0,new_freezes=0,registrations=0,physics_attempts=0,
        independent_validation_claim=False,final_release_requested=False,
        exported_fields=[dict(field=name,path=file,bytes=len(pieces[name]),sha256=sha(output/file),canonical_value_sha256=digest(plan[name])) for name,file in FIELDS.items()])
    with (output/'SOURCE_MANIFEST.json').open('x',encoding='utf8') as stream:json.dump(values,stream,indent=2,ensure_ascii=True,allow_nan=False)
    preflight=[dict(path=str(output/'SOURCE_MANIFEST.json'),sha256=sha(output/'SOURCE_MANIFEST.json'))]
    preflight += [dict(path=str(output/row['path']),sha256=row['sha256']) for row in values['exported_fields']]
    execution_contract=dict(future_root_must_not_exist=str(future_root),required_current_file_sha256=preflight,
        stop_on_first_nonzero_exit=True,check_exit_after_each_native_command=True,
        retry_or_adopt_existing_campaign=False)
    with (output/'FUTURE_COMMANDS.json').open('x',encoding='utf8') as stream:json.dump(dict(cwd=str(root),not_executed=True,
        execution_contract=execution_contract,argv=commands),stream,indent=2)
    quote=lambda s:"'"+s.replace("'","''")+"'"
    with (output/'FUTURE_COMMANDS.ps1.txt').open('x',encoding='utf8') as stream:
        stream.write('# NOT EXECUTED. Fresh future reproduction, not a current-study attempt or independent validation.\n')
        stream.write("$ErrorActionPreference = 'Stop'\n")
        stream.write('if (Test-Path -LiteralPath '+quote(str(future_root))+
            ") { throw 'Future reproduction root already exists; no retry or adoption is permitted.' }\n")
        for row in preflight:
            stream.write('if ((Get-FileHash -LiteralPath '+quote(row['path'])+" -Algorithm SHA256).Hash -ne "+quote(row['sha256'])+
                ") { throw 'Exported reproduction input or manifest SHA drift; no native command started.' }\n")
        stream.write('Set-Location -LiteralPath '+quote(str(root))+'\n')
        for index,command in enumerate(commands,1):
            stream.write('& '+' '.join(quote(s) for s in command)+'\n')
            stream.write('$nativeExit = $LASTEXITCODE\n')
            stream.write('if ($nativeExit -ne 0) { throw "Native reproduction step '+str(index)+
                ' failed (exit $nativeExit); later steps are not started." }\n')
    need(sha(plan_path)==plan_sha and sha(freeze_path)==freeze_sha,'Archive drift after export; retain output as rejected evidence')
    return values


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('root','plan','freeze','output','future-run-root'):parser.add_argument('--'+name,type=Path,required=True)
    for name in ('plan-sha256','freeze-sha256'):parser.add_argument('--'+name,required=True)
    parser.add_argument('--future-wall-hours',type=float,required=True);parser.add_argument('--future-attempt-limit',type=int)
    args=parser.parse_args()
    value=export(args.root,args.plan,args.plan_sha256,args.freeze,args.freeze_sha256,args.output,args.future_run_root,args.future_wall_hours,args.future_attempt_limit)
    print(json.dumps({k:value[k] for k in ('status','panel_scenarios','original_registered_pairs','configuration_ids','new_freezes','registrations','physics_attempts')}))


if __name__=='__main__':main()
