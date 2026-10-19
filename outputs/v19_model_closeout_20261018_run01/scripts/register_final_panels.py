"""Register a uniform repaired-source replay; no physical episodes here."""
from pathlib import Path
import copy
import json
import subprocess
import sys

B=Path(__file__).resolve().parent.parent
ROOT=B.parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from tools.model_closeout import runner as r
from tools.model_closeout import data_design as d

def write(path,value):
    with Path(path).open('x',encoding='utf8') as stream:json.dump(value,stream,ensure_ascii=False,indent=2,allow_nan=False)

def preregister_clocks(freeze):
    specs=[r.policy_specs()[key] for key in ('E','K1','C19')]
    output=[]
    for name,key,value,design,indices in (
        ('open1','grid_open_transfer_minutes',1.,'main',[3,67]),
        ('contact025','local_contact_restore_delay_minutes',.25,'long',[5])):
        config=copy.deepcopy(freeze['model_config']);config['execution_recovery'][key]=value
        record=dict(schema='hospital_ies.v19.assumption_preregistration.v1',source_freeze_signature=freeze['signature'],
            source_sha256=r.source_binding(),model_id=freeze['model_id'],model_config=config,policy_specs=specs,
            reason='Repeat the predeclared finite clock sensitivity on frozen M19; original physical/service/queue rules unchanged.',
            design=design,indices=indices,comparison_scope='Seen-case mechanism sensitivity, no unseen or policy-search claim')
        record['signature']=d.digest(record)
        r.validate_final_freeze(freeze,r.source_binding(),freeze['model_id'],config,specs,sensitivity=record)
        cfg='SENSITIVITY_'+name.upper()+'_R1_CONFIG.json';reg='SENSITIVITY_'+name.upper()+'_R1_PREREGISTRATION.json'
        write(B/cfg,config);write(B/reg,record)
        output.append(dict(name=name,config=cfg,registration=reg,signature=record['signature']))
    write(B/'SENSITIVITY_R1_PLAN.json',output)

def main():
    frozen='MODEL_FREEZE_CLOSEOUT01.json';freeze=r.read(B/frozen)
    r.validate_final_freeze(freeze,r.source_binding(),freeze['model_id'],freeze['model_config'],
        [r.policy_specs()[key] for key in ('E','K1','C19')])
    preregister_clocks(freeze)
    jobs=[]
    for name,design in (('long_final_r1','long'),('main_final_r1','main'),('propagation_final_r1','propagation')):
        jobs.append((name,design,'E,K1,C19',[]))
    # Keep each long normal episode in its own process. This changes only
    # registration grouping; original windows, source/config and reset seeds
    # remain identical and are checked from actual cross-panel evidence.
    for policy in ('E','K1','C19'):
        jobs.append(('normal_'+policy+'_final_r1','normal',policy,[]))
    for name,design,indices in (('open1','main','3,67'),('contact025','long','5')):
        jobs.append(('sensitivity_'+name+'_r1',design,'E,K1,C19',['--indices',indices,
            '--config','SENSITIVITY_'+name.upper()+'_R1_CONFIG.json',
            '--sensitivity-registration','SENSITIVITY_'+name.upper()+'_R1_PREREGISTRATION.json']))
    jobs.append(('compat_final_r1','main','C18_M19',['--indices','1,3,7,43,67']))
    jobs.append(('compat_long_final_r1','long','C18_M19',['--indices','0,5']))
    actual=[]
    for name,design,policies,extra in jobs:
        inner=[sys.executable,'-B','-m','tools.model_closeout.campaign','register','--name',name,
            '--design',design,'--policies',policies,'--freeze',frozen,*extra]
        command=[sys.executable,'-B',str(ROOT/'tools/p0_audit/run_logged.py'),'--run-dir',str(B),'--name','register_'+name,'--',*inner]
        result=subprocess.run(command,cwd=ROOT,check=False)
        actual.append(dict(name=name,command=command,exit_code=result.returncode))
        if result.returncode:break
    accepted=len(actual)==len(jobs) and all(row['exit_code']==0 for row in actual)
    write(B/'FINAL_R1_REGISTRATION_SEQUENCE.json',dict(jobs=actual,all_succeeded=accepted,
        comparison_planned_pairs=303,compatibility_planned_pairs=7,no_physical_episodes_started=True,
        normal_resource_execution='12 separate single-policy processes, globally serial',
        normal_comparison='Explicit cross-panel matching of actual input/reset/exogenous hashes',
        freeze_path=str(B/frozen),freeze_sha256=r.sha(B/frozen)))
    return 0 if accepted else 1

if __name__=='__main__':raise SystemExit(main())
