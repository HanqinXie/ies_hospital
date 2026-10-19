"""Finite immutable V19 campaigns; specification selection contains no policy logic."""
from __future__ import annotations
import argparse
import copy
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
B = ROOT / 'outputs/v19_model_closeout_20261018_run01'
OLD = ROOT / 'outputs/v18_execution_recovery_20261018_run01'
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]

def read(path):
    return json.loads(Path(path).read_text(encoding='utf8'))

def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf8') as f:
        json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)

def proposal(kind, *, freeze_path=None):
    from . import data_design as d
    sources = dict(main='MAIN_FINAL_R1_PROPOSAL.json', normal='NORMAL_FINAL_R1_PROPOSAL.json',
        long='LONG_FINAL_R1_PROPOSAL.json', propagation='PROPAGATION_FINAL_R1_PROPOSAL.json',
        development='KEY04_PROPOSAL.json', compatibility='COMPAT_FINAL_R1_PROPOSAL.json')
    if kind == 'key':
        main = read(OLD / 'design/MAIN_FINAL_R1_PROPOSAL.json')['scenarios']
        long = read(OLD / 'design/LONG_FINAL_R1_PROPOSAL.json')['scenarios']
        # Four electrical handoffs, actual heat replacement, day100 retained
        # GT, day40 all-GT endurance, common tower, ABS, HRSG and EC positives.
        scenes = [main[i] for i in (1, 3, 7, 43, 67, 11, 15, 19, 23)]
        scenes += [long[i] for i in (0, 5)]
        from .runner import sha
        bindings = {(OLD/'design'/n).relative_to(ROOT).as_posix():{'sha256':sha(OLD/'design'/n)}
                    for n in ('MAIN_FINAL_R1_PROPOSAL.json','LONG_FINAL_R1_PROPOSAL.json')}
    else:
        original = read(OLD / 'design' / sources[kind])
        scenes = original['scenarios']
        from .runner import sha
        bindings = {(OLD/'design'/sources[kind]).relative_to(ROOT).as_posix():
            {'sha256':sha(OLD/'design'/sources[kind]),'signature':original['signature']}}
    scenes = copy.deepcopy(scenes)
    from .runner import freeze_file
    selected_freeze=freeze_file(freeze_path)
    freeze = read(selected_freeze)['signature'] if selected_freeze.exists() else None
    for scene in scenes:
        scene.update(experiment_role='development' if kind in ('key','development') else 'confirmation',
            selection_allowed=kind in ('key','development'),
            requires_policy_freeze=kind not in ('key','development'),
            v19_source_scene_id=scene['id'], v19_seen_regression=True,
            source_outcomes_used_for_selection=kind=='key')
    return d.panel('v19_'+kind, scenes, bindings=bindings, freeze_signature=freeze,
        evaluation_role='development' if kind in ('key','development') else 'seen_regression')

def register(a):
    from . import runner as r
    from . import data_design as d
    selected_freeze=r.freeze_file(getattr(a,'freeze',None))
    if a.design=='confirmation':
        if not getattr(a,'proposal',None):raise ValueError('Confirmation needs an already selected signed proposal')
        p=r.read(a.proposal)
        if (p.get('signature')!=d.digest({k:v for k,v in p.items() if k!='signature'})
                or p.get('evaluation_role')!='independent_confirmation'):
            raise ValueError('Invalid independent confirmation proposal')
        p=d.panel(p['panel'],p['scenarios'],bindings=p.get('source_metadata_bindings'),
            freeze_signature=p.get('freeze_signature'),evaluation_role='independent_confirmation',
            isolation_registration=p.get('confirmation_isolation_registration'))
    else:
        p = proposal(a.design,freeze_path=selected_freeze)
    if a.design not in ('key','development') and not selected_freeze.exists():
        raise ValueError('Final comparison requires source/model/policy freeze')
    if a.indices:
        p=subset_proposal(p,[int(i) for i in a.indices.split(',')])
    specs = [r.policy_specs()[k] for k in a.policies.split(',')]
    config = read(B / (a.config or 'M19_MODEL_CONFIG.json'))
    if a.sensitivity_registration:
        p.pop('signature',None)
        p['sensitivity_registration']=read(B/a.sensitivity_registration)
        p['signature']=d.digest(p)
    if a.design not in ('key','development'):
        _,resolved=r.model_contract('M19_model_closeout',config)
        r.validate_final_freeze(read(selected_freeze),r.source_binding(),
            'M19_model_closeout',resolved,specs,sensitivity=p.get('sensitivity_registration'))
    write(B / 'design' / (a.name.upper()+'_PROPOSAL.json'), p)
    registrations = []
    for i, scene in enumerate(p['scenarios']):
        directory = B / 'experiments' / (a.name+'_'+str(i).zfill(3))
        plan = r.register(p, directory, specs, model_id='M19_model_closeout',
            model_config=config, scenario_ids=[scene['id']], phase=a.name,freeze_path=selected_freeze)
        registrations.append(dict(directory=str(directory), scenario_id=scene['id'], registered_pairs=plan['registered_pairs']))
    write(B / (a.name.upper()+'_REGISTRATION.json'), registrations)
    print(json.dumps(dict(name=a.name, scenes=len(registrations), trajectories=sum(x['registered_pairs'] for x in registrations))))


def subset_proposal(proposal, indices):
    from . import data_design as d
    if proposal.get('evaluation_role')=='independent_confirmation':
        raise ValueError('Do not subset the complete four-class confirmation panel')
    indices=list(indices)
    if not indices or len(indices)!=len(set(indices)) or any(i<0 or i>=len(proposal['scenarios']) for i in indices):
        raise ValueError('Use distinct valid proposal indices')
    return d.panel(proposal['panel'],[proposal['scenarios'][i] for i in indices],
        bindings=proposal.get('source_metadata_bindings'),freeze_signature=proposal.get('freeze_signature'),
        evaluation_role=proposal.get('evaluation_role','seen_regression'),
        isolation_registration=proposal.get('confirmation_isolation_registration'))

def run(a):
    if not 1<=a.workers<=2:raise ValueError('Use one physical worker initially, at most two after measured admission')
    rows = read(B / (a.name.upper()+'_REGISTRATION.json'))
    if a.indices:
        rows = [rows[int(i)] for i in a.indices.split(',')]
    normal=any(any(float(s['duration_steps'])*float(s.get('step_minutes',15))>=1440.
        for s in read(Path(row['directory'])/'manifest.json')['scenarios']) for row in rows)
    if normal and a.workers!=1:raise ValueError('24/48/72-hour normal windows require serial execution')
    def one(row):
        path = Path(row['directory'])
        stop_file = B / ('STOP_'+a.name.upper()+'.json')
        if stop_file.exists():
            return dict(directory=str(path), exit_code=125, not_started_cancelled=True,
                reason='Explicit campaign stop file; no physical attempt launched', stop_file=str(stop_file))
        command = [sys.executable, '-B', str(ROOT/'tools/p0_audit/run_logged.py'), '--run-dir', str(B),
            '--name', 'run_'+path.name, '--', sys.executable, '-B', '-m', 'tools.model_closeout.runner',
            'run', '--directory', str(path)]
        done = subprocess.run(command, cwd=ROOT, capture_output=True, check=False)
        return dict(directory=str(path), exit_code=done.returncode,
            launcher_stdout=done.stdout.decode('utf8', errors='replace'),
            launcher_stderr=done.stderr.decode('utf8', errors='replace'))
    results = []
    with ThreadPoolExecutor(max_workers=a.workers) as pool:
        for future in as_completed([pool.submit(one, row) for row in rows]):
            value = future.result(); results.append(value)
            print(json.dumps(dict(finished=len(results), total=len(rows), directory=value['directory'], exit_code=value['exit_code'])), flush=True)
    write(B / (a.name.upper()+'_PROCESS_RESULTS'+('_'+a.indices.replace(',','_') if a.indices else '')+'.json'), results)
    return int(any(x['exit_code'] != 0 for x in results))

def main():
    p = argparse.ArgumentParser()
    p.add_argument('command', choices=('register','run'))
    p.add_argument('--name', required=True)
    p.add_argument('--design', choices=('key','development','main','normal','long','propagation','compatibility','confirmation'), default='key')
    p.add_argument('--policies', default='E,K1,C19')
    p.add_argument('--proposal',type=Path,help='Already frozen/selected small confirmation metadata only')
    p.add_argument('--config')
    p.add_argument('--freeze',help='Fresh immutable freeze JSON in the current V19 run; defaults to MODEL_FREEZE.json')
    p.add_argument('--sensitivity-registration')
    p.add_argument('--indices')
    p.add_argument('--workers', type=int, default=1)
    a = p.parse_args()
    return register(a) if a.command=='register' else run(a)

if __name__ == '__main__':
    raise SystemExit(main())
