"""Compute review statistics from actual current-run JSON; no simulations."""
import json,csv,hashlib
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[2];RUN=ROOT/'outputs/v43_p1b_20261012_235940'
def read(path):return json.loads(path.read_text(encoding='utf-8'))
def write(name,x):(RUN/name).write_text(json.dumps(x,ensure_ascii=False,indent=2),encoding='utf-8')
initials=read(RUN/'frozen_p1a_initials.json');rows=[];comparisons=[];failure_sources=[]
for p in sorted((RUN/'delivery_replay').glob('*__*.json')):
    d=read(p);segs=d['segments'];sid=d['scenario']['id'];ini=initials[sid]
    same=d['scenario']==ini['scenario'] and all(d['initial_state'][k]==v for k,v in ini['state'].items())
    # Original P1A smoke scenarios are compared to the actual fresh baseline replay.
    old=RUN/'p1a_smoke_replay'/p.name
    original_scenario=(read(old)['scenario']==d['scenario']) if old.exists() else read(RUN/'p1a_minimal_replay/legacy_L4_retained.json')['scenario']==d['scenario']
    comparisons.append(dict(id=sid,policy=d['policy'],same_frozen_initial=same,same_original_scenario=original_scenario))
    energy=np.sum([np.array(s['unserved_kW'])*(s['end_minute']-s['start_minute'])/60 for s in segs],axis=0)
    row=dict(id=sid,policy=d['policy'],steps=d['completed_steps'],segments=len(segs),error=d['error'],
        p0_failure=any(s['classification']['p0_guarantee_failure'] for s in segs),
        ENS_kWh=energy.tolist(),energy_cost_USD=sum(s['cost_USD_step'] for s in segs),
        balance_kW=max(max(map(abs,s['energy_balance_residuals'])) for s in segs),
        steam_lbph=max(abs(s['steam_balance_residual_lbph']) for s in segs),
        trips=[e for e in d['events'] if e['kind'] in ('no_export_interlock','minimum_output_interlock','protection_trip')],
        solver_calls=len(d['solver_calls']),solver_seconds=sum(x['elapsed_seconds'] for x in d['solver_calls']),
        economic_skips=sum(bool(x.get('optimizer_warning')) for x in d['solver_calls']))
    rows.append(row)
for p in [RUN/'p1a_minimal_replay/legacy_L4_retained.json']+sorted((RUN/'p1a_smoke_replay').glob('smoketest_L3_0000_h3255__*.json')):
    d=read(p);err=d['error'];a=err['executed_action'];load=np.array(err['loads_kW'])
    state=err['state'];disc=np.array(state['disconnected_loads'])
    aux=(a['absorption_cooling_kW']*err['plant']['abs_electric_kW_per_rt']+sum(a['electric_chiller_cooling_kW'])*err['plant']['ec_electric_kW_per_rt'])/3.51685284
    # exact conversion constant used by the model
    import sys
    sys.path.insert(0,str(ROOT/'src'))
    from hospital_ies.model import RT_TO_KW
    aux=(a['absorption_cooling_kW']*err['plant']['abs_electric_kW_per_rt']+sum(a['electric_chiller_cooling_kW'])*err['plant']['ec_electric_kW_per_rt'])/RT_TO_KW
    failure_sources.append(dict(id=d['scenario']['id'],policy=d.get('policy','priority_milp'),time=err['time_minute'],
        GT1=a['gt_electric_kW'][0],GT2=a['gt_electric_kW'][1],GT3=a['gt_electric_kW'][2],grid=a['grid_import_kW'],diesel=a['diesel_kW'],
        connected_e=load[:,0].sum(),actual_disconnected_e=disc[:,0].sum(),held_aux_upper=aux,
        minimum_surplus_vs_held_aux=sum(a['gt_electric_kW'])+a['grid_import_kW']+a['diesel_kW']-load[:,0].sum()-aux,
        physical_solution_exists=False))
write('paired_initial_and_scenario_checks.json',comparisons);write('review_statistics.json',rows)
with (RUN/'baseline_failure_sources.csv').open('w',newline='',encoding='utf-8') as f:
    w=csv.DictWriter(f,fieldnames=list(failure_sources[0]));w.writeheader();w.writerows(failure_sources)
summary=dict(trajectories=len(rows),complete=sum(r['error'] is None for r in rows),steps=sum(r['steps'] for r in rows),
    segments=sum(r['segments'] for r in rows),all_initials_and_scenarios_equal=all(c['same_frozen_initial'] and c['same_original_scenario'] for c in comparisons),
    maximum_balance_kW=max(r['balance_kW'] for r in rows),maximum_steam_lbph=max(r['steam_lbph'] for r in rows),
    economic_skips=sum(r['economic_skips'] for r in rows),solver_calls=sum(r['solver_calls'] for r in rows),
    baseline_failure_sources=failure_sources,
    difficult_cases=[r for r in rows if r['id'] in ('testL4','smoketest_L3_0000_h3255')])
write('summary_for_report.json',summary);print(json.dumps(summary,indent=2))
assert summary['complete']==18 and summary['steps']==100 and summary['all_initials_and_scenarios_equal']
assert summary['maximum_balance_kW']<=1e-4 and summary['maximum_steam_lbph']<=1e-4
