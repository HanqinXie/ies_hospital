"""Immutable seen-case development registrations; no simulator is started."""
import argparse,copy,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from tools.model_closeout import runner as r,data_design as d
B=r.RUN;OLD=ROOT/'outputs/v18_execution_recovery_20261018_run01'

def main():
    p=argparse.ArgumentParser();p.add_argument('--kind',choices=['propagation','contact025','open1'],required=True)
    p.add_argument('--name',required=True);p.add_argument('--policies',default='C19,C18_M19');a=p.parse_args()
    filename={'propagation':'PROPAGATION_FINAL_R1_PROPOSAL.json','contact025':'LONG_FINAL_R1_PROPOSAL.json','open1':'MAIN_FINAL_R1_PROPOSAL.json'}[a.kind]
    source=OLD/'design'/filename;proposal=r.read(source);scenes=copy.deepcopy(proposal['scenarios'])
    if a.kind=='contact025':scenes=[scenes[5]]
    elif a.kind=='open1':scenes=[scenes[3],scenes[67]]
    for s in scenes:
        s.update(experiment_role='development',selection_allowed=True,requires_policy_freeze=False,
            v19_seen_regression=True,v19_source_scene_id=s['id'])
    config=r.read(B/'M19_MODEL_CONFIG.json')
    if a.kind=='contact025':config['execution_recovery']['local_contact_restore_delay_minutes']=.25
    elif a.kind=='open1':config['execution_recovery']['grid_open_transfer_minutes']=1.
    specs=[r.policy_specs()[k] for k in a.policies.split(',')]
    panel=d.panel(a.name,scenes,bindings={source.relative_to(ROOT).as_posix():{'sha256':r.sha(source)}},evaluation_role='development')
    r.write(B/'design'/(a.name.upper()+'_PROPOSAL.json'),panel)
    registration=[]
    for i,scene in enumerate(scenes):
        directory=B/'experiments'/(a.name+'_'+str(i).zfill(3))
        plan=r.register(panel,directory,specs,model_id=r.MODEL_ID,model_config=config,
            scenario_ids=[scene['id']],phase=a.name)
        registration.append(dict(directory=str(directory),scenario_id=scene['id'],registered_pairs=plan['registered_pairs']))
    r.write(B/(a.name.upper()+'_REGISTRATION.json'),registration)
    print(json.dumps(dict(name=a.name,scenes=len(scenes),pairs=sum(x['registered_pairs'] for x in registration))))

if __name__=='__main__':main()
