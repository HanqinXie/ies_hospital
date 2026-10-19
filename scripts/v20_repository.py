"""V20 closure verification, explicit compact staging and one real replay."""
from __future__ import annotations
import argparse
from datetime import datetime,timezone
import hashlib
import json
import math
import os
from pathlib import Path,PurePosixPath
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
RUN_REL='outputs/v20_load_accounting_20261019_run01'
RUN=ROOT/RUN_REL
FREEZE='MODEL_FREEZE_ACCOUNTING01.json'
TOLERANCES=dict(energy=(1e-5,1e-8),time=(1e-6,0.),fee=(1e-5,1e-8))

def read(p):return json.loads(Path(p).read_text(encoding='utf-8-sig'))
def sha(p):
    h=hashlib.sha256()
    with Path(p).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()
def safe(root,name):
    if not isinstance(name,str) or ':' in name or '\\' in name:raise ValueError('Unsafe relative path')
    p=PurePosixPath(name)
    if p.is_absolute() or any(x in ('.','..') for x in p.parts):raise ValueError('Unsafe relative path')
    q=(root/name).resolve()
    if not q.is_relative_to(root.resolve()):raise ValueError('Path escapes root')
    return q
def write_new(p,v):
    p=Path(p);p.parent.mkdir(parents=True,exist_ok=True)
    with p.open('x',encoding='utf-8',newline='\n') as f:json.dump(v,f,ensure_ascii=False,indent=2,allow_nan=False);f.write('\n')

def verify_rows(root,rows):
    errors=[]
    for row in rows:
        p=safe(root,row['path'])
        if not p.is_file() or p.stat().st_size!=row['bytes'] or sha(p)!=row['sha256']:errors.append(row['path'])
    return errors

def verify():
    sys.path[:0]=[str(ROOT),str(ROOT/'src')]
    from tools.load_accounting import runner as r,data_design as d
    from tools.load_accounting.gate import test_binding
    f=read(RUN/FREEZE);source=r.source_binding();tests=test_binding();errors=verify_rows(ROOT,f['runtime_inputs'])
    if source!=f['source_sha256']:errors.append('production source differs from final M20 freeze')
    if tests!=f['source_bound_test_gate']['test_files_sha256']:errors.append('test files differ from final M20 freeze')
    r.validate_final_freeze(f,source,r.MODEL_ID,f['model_config'],list(r.policy_specs().values()),runtime_inputs=d.runtime_input_inventory(ROOT))
    for spec in r.policy_specs().values():r.factory(spec)
    for name,module in list(sys.modules.items()):
        if name=='tools' or name.startswith(('tools.','hospital_ies')):
            if getattr(module,'__file__',None) and not Path(module.__file__).resolve().is_relative_to(ROOT):
                errors.append('project import escaped current root: '+name)
    selection=ROOT/'publication/V20_FILE_SELECTION.json'
    if selection.is_file():errors.extend(verify_rows(ROOT,read(selection)['files']))
    result=dict(status='passed' if not errors else 'failed',model_id=r.MODEL_ID,
        production_files=len(source),test_files=len(tests),runtime_input_files=len(f['runtime_inputs']),
        errors=errors,third_party_literature_required_for_dispatch=False,
        solver_license_is_external=True,project_import_root=str(ROOT))
    print(json.dumps(result,ensure_ascii=True,indent=2));return result

def compare_metrics(actual,expected):
    if set(actual)!=set(expected):raise ValueError('Reproduction metric fields differ')
    rows=[]
    for name,target in expected.items():
        value=actual[name];group=name.split('.',1)[0];absolute,relative=TOLERANCES[group]
        if target is None or value is None:passed=value is target;difference=budget=None
        else:
            if not all(isinstance(x,(float,int)) and math.isfinite(x) for x in (value,target)):
                raise ValueError('Nonfinite reproduction metric')
            difference=abs(value-target);budget=absolute+relative*abs(target);passed=difference<=budget
        rows.append(dict(metric=name,actual=value,reference=target,difference=difference,budget=budget,passed=passed))
    return rows

def metric_snapshot(record):
    if (record.get('status')!='completed' or record.get('error') is not None
            or record.get('analysis_error') is not None or record.get('model_id')!='M20_load_accounting'):
        raise ValueError('Failed, unknown or differently versioned reference/replay')
    a=record['analysis'];window=a['v18']['windows']['registered_full']
    if (a.get('execution_model_id')!='M20_load_accounting' or not window['complete']
            or a['v20']['episode_audit']['status']!='passed' or a['v20']['episode_audit'].get('issues')!=[]):
        raise ValueError('Incomplete, mismatched or unaudited reference/replay')
    metrics={};service=window['all_registered']['carrier_service']
    ledger=a['v20']['service_ledgers']['registered_full']['all_registered']
    for p in ('P0','P1','P2','P3'):
        for c in ('e','h','c'):
            for key in ('requested_kWh','delivered_kWh','ENS_kWh','disconnected_ENS_kWh','connected_unserved_ENS_kWh'):
                metrics['energy.'+p+'.'+c+'.'+key]=ledger[p][c][key]
            for key in ('material_minutes','longest_material_minutes'):
                metrics['time.'+p+'.'+c+'.'+key]=service[p][c][key]
    economics=window['all_registered']['economics']
    for key in ('GT_gas_MMBtu','boiler_gas_MMBtu','grid_purchase_kWh','DG_electric_work_kWh','DG_shaft_work_kWh','DG_fuel_budget_debit_kWh_equivalent'):
        metrics['energy.'+key]=economics[key]
    for key in ('actual_energy_fee_USD','grid_fee_USD','GT_gas_fee_USD','boiler_gas_fee_USD'):
        metrics['fee.'+key]=economics[key]
    return metrics

def reproduce(name):
    if not name or not name.replace('_','').isalnum():raise ValueError('Use a fresh simple replay name')
    if os.name=='nt' and ROOT.drive.lower() not in ('d:','e:'):raise ValueError('Run scientific outputs on D/E')
    if verify()['status']!='passed':raise ValueError('Repository closure verification failed')
    from tools.load_accounting import runner as r,data_design as d
    frozen=read(RUN/FREEZE);reference=RUN/'experiments/v20_main_000'
    manifest=read(reference/'manifest.json');scene=manifest['scenarios'][0];spec=r.policy_specs()['E']
    ref=read(reference/'pairs'/(scene['id']+'__'+spec['policy_id'])/'RESULT.json')
    expected=metric_snapshot(ref)
    target=RUN/'experiments'/('reproduce_'+name)
    if target.exists():raise FileExistsError('Replay evidence already exists: '+str(target))
    proposal=d.panel('reproduce_'+name,[scene],
        bindings={reference.relative_to(ROOT).as_posix()+'/manifest.json':dict(sha256=sha(reference/'manifest.json'))},
        freeze_signature=frozen['signature'])
    review=RUN/'review'/('reproduce_'+name);review.mkdir(parents=True,exist_ok=False)
    write_new(review/'PREREGISTRATION.json',dict(scenario_id=scene['id'],policy_id=spec['policy_id'],
        reference_result_sha256=sha(reference/'pairs'/(scene['id']+'__'+spec['policy_id'])/'RESULT.json'),
        predefined_metrics=expected,predeclared_tolerances=TOLERANCES,actual_attempts_started=0))
    r.register(proposal,target,[spec],model_id=r.MODEL_ID,model_config=frozen['model_config'],
        phase='independent_repository_reproduction',freeze_path=FREEZE)
    command=[sys.executable,'-B','-m','tools.load_accounting.runner','run','--directory',target.relative_to(ROOT).as_posix()]
    temporary=review/'temp';temporary.mkdir();env=os.environ.copy()
    env.update(PYTHONDONTWRITEBYTECODE='1',PYTHONIOENCODING='utf-8',TEMP=str(temporary),TMP=str(temporary),PYTHONPATH=str(ROOT/'src')+os.pathsep+str(ROOT))
    with (review/'runner.log').open('xb') as log:done=subprocess.run(command,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,check=False)
    rows=[];error=None
    try:
        actual=read(target/'pairs'/(scene['id']+'__'+spec['policy_id'])/'RESULT.json')
        rows=compare_metrics(metric_snapshot(actual),expected)
        passed=done.returncode==0 and all(x['passed'] for x in rows)
    except Exception as exc:passed=False;error=dict(type=type(exc).__name__,message=str(exc))
    result=dict(status='passed' if passed else 'failed_unknown',command=command,exit_code=done.returncode,
        current_root=str(ROOT),reference_root=str(reference),actual_registration_directory=str(target),
        metric_count=len(rows),comparisons=rows,error=error,original_working_tree_imports_allowed=False,
        registered_duration_minutes=scene['duration_steps']*scene.get('step_minutes',15.),
        all_failed_and_unknown_records_retained=True,original_final12_unopened=True)
    write_new(review/'REPRODUCTION_PROOF.json',result)
    print(json.dumps(dict(status=result['status'],metric_count=len(rows),duration_minutes=result['registered_duration_minutes'])))
    return 0 if passed else 2

def selected_paths():
    f=read(RUN/FREEZE);names=set(f['source_sha256']);names.update(x['path'] for x in f['runtime_inputs'])
    skip={'__pycache__','.pytest_cache','temp','test_tmp','source_snapshots','delivery_builds'}
    for folder in ('tests','docs/v20','inputs/optional'):
        names.update(p.relative_to(ROOT).as_posix() for p in (ROOT/folder).rglob('*') if p.is_file() and not set(p.parts)&skip)
    for name in ('README.md','AGENTS.md','requirements.txt','pytest.ini','.gitattributes'):
        if (ROOT/name).is_file():names.add(name)
    # Curated inherited numerical fixtures/configuration; never historical raw.
    for old in ('v11_autonomous_dispatch_20261016_run01','v13_executed_service_20261017_run01',
        'v15_engineering_operation_20261017_run01','v17_state_coordinated_20261018_run01',
        'v18_execution_recovery_20261018_run01','v43_p2b_20261013_run01'):
        for p in (ROOT/'outputs'/old).rglob('*'):
            if p.is_file() and not set(p.parts)&(skip|{'experiments','raw'}) and p.suffix.lower() in ('.json','.npz','.py'):
                names.add(p.relative_to(ROOT).as_posix())
    for name in ('M19_MODEL_CONFIG.json','references/EMISSIONS_FACTOR_REGISTRY.json',
        'review/c19_255_failure/replay01/layers/layer_20.npz','review/c19_255_failure/replay01/layers/layer_20.json',
        'design/MAIN_FINAL_R1_PROPOSAL.json','design/CONFIRMATION_FINAL_R1_PROPOSAL.json'):
        p=ROOT/'outputs/v19_model_closeout_20261018_run01'/name
        if p.is_file():names.add(p.relative_to(ROOT).as_posix())
    for p in RUN.rglob('*'):
        if not p.is_file() or set(p.parts)&skip:continue
        if p.name.endswith(('.gz','.zip','.pdf','.xlsx','.sqlite','.sql','.png')):continue
        relative=p.relative_to(RUN)
        # Full interval CSVs remain local; compact per-pair RESULT and ledgers stay.
        if relative.parts[0]=='experiments' and p.suffix=='.csv':continue
        if p.suffix.lower() in ('.json','.jsonl','.md','.txt','.csv','.log','.py','.npz','.diff'):
            names.add(p.relative_to(ROOT).as_posix())
    return sorted(names)

def stage(destination):
    target=Path(destination)
    if not target.is_absolute() or target.exists():raise ValueError('Use a new absolute publication destination')
    target=target.resolve()
    if target.is_relative_to(ROOT) or ROOT.is_relative_to(target):raise ValueError('Publication copy must be outside original worktree')
    if os.name=='nt' and target.drive.lower() not in ('d:','e:'):raise ValueError('Publication staging uses D/E')
    if verify()['status']!='passed':raise ValueError('Cannot stage a changed source closure')
    names=selected_paths();rows=[]
    for name in names:
        p=safe(ROOT,name)
        if not p.is_file() or p.is_symlink():raise ValueError('Missing/linked selected source: '+name)
        rows.append(dict(path=name,bytes=p.stat().st_size,sha256=sha(p)))
    total=sum(x['bytes'] for x in rows)
    if shutil.disk_usage(target.parent).free<total+1024**3:raise ValueError('Insufficient publication space')
    target.mkdir()
    for row in rows:
        src=safe(ROOT,row['path']);dst=safe(target,row['path']);dst.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(src,dst)
        if sha(dst)!=row['sha256']:raise ValueError('Publication copy changed bytes')
    write_new(target/'publication/V20_FILE_SELECTION.json',dict(schema='hospital_ies.v20.repository_selection.v1',
        files=rows,selected_bytes=total,raw_trajectories_included=False,original_workspace_modified=False))
    ancestors=set()
    for name in names:
        if not name.startswith('outputs/'):continue
        p=PurePosixPath(name).parent
        while str(p)!='outputs':ancestors.add(str(p)+'/');p=p.parent
    ignores=['__pycache__/','.pytest_cache/','.venv/','*.pyc','*.zip','*.gz','*.7z','*.pt','*.pth','*.ckpt',
        '*.pkl','*.sqlite','*.sql','.env','.env.*','gurobi.lic','/inputs/literature/','/publication_checks/','/outputs/**']
    ignores+=['!/'+n for n in sorted(ancestors,key=lambda n:(n.count('/'),n))]
    ignores+=['!/'+n for n in names if n.startswith('outputs/')]
    (target/'.gitignore').write_text('\n'.join(ignores)+'\n',encoding='utf-8')
    print(json.dumps(dict(destination=str(target),files=len(rows),selected_bytes=total)))
    return 0

def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='mode',required=True)
    sub.add_parser('verify');s=sub.add_parser('stage');s.add_argument('--destination',required=True)
    q=sub.add_parser('reproduce');q.add_argument('--name',required=True)
    a=p.parse_args()
    if a.mode=='verify':return 0 if verify()['status']=='passed' else 1
    if a.mode=='stage':return stage(a.destination)
    return reproduce(a.name)

if __name__=='__main__':raise SystemExit(main())
