from pathlib import Path
import argparse
_parser = argparse.ArgumentParser(description="Read-only diagnostics for the supplied Hospital IES v4.2 project")
_parser.add_argument("--root", type=Path, default=Path.cwd(), help="Extracted original project root")
_parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parent)
_args = _parser.parse_args()
_args.output_dir.mkdir(parents=True, exist_ok=True)
if not (_args.root / "src/hospital_ies/resilience.py").is_file():
    _parser.error("--root must contain src/hospital_ies/resilience.py")
import sys,json
import numpy as np,pandas as pd
ROOT=_args.root.resolve();sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from hospital_ies.model import load_plant,gt_level_properties
from hospital_ies.pipeline import read_data
from hospital_ies.resilience import _solve_milp,empty_faults,RuntimeState
from scenario_sampler import sample_scenarios
plant=load_plant(ROOT/'configs/plant_configs.json','doe_capacity_matched_exploratory')
h=read_data(ROOT);row=next(h.iloc[[5455]].itertuples(index=False))
conf=json.loads((ROOT/'configs/resilience_assumptions.json').read_text())
rated=float(gt_level_properties(plant,float(row.outdoor_temperature_C))[0][-1])
state=RuntimeState(np.array([rated,0.,0.]),2400.,set(),np.zeros(3),{})
loads=np.zeros((4,3));loads[0,0]=100.
out={'derating_test_input':{'pre_GT1_kW':rated,'down_ramp_lower_bound_kW':rated-225,'remaining_availability':.1,'derated_cap_kW':rated*.1}}
f=empty_faults();f['GT_1']=.1
try:
 sol=_solve_milp(row,plant,loads,f,state,.25,850.,False,900.,config=conf)
 out['derating_result']='unexpected_success'
except RuntimeError as ex:out['derating_result']=str(ex)
ss=sample_scenarios(h,100,12,20261012)
rows=[]
for x in ss:
 if x['stage']==2:rows.append({'mode':x['shock_mode'],**x['shock']})
d=pd.DataFrame(rows)
out['L2_100_samples_ranges']=d.groupby('mode')[['e','h','c']].agg(['min','max']).to_json()
# Each L3 device stays tied to a fixed severity across 100 samples (20-device cycle, i%4 severity).
r=[]
for x in ss:
 if x['stage']==3:r.append({'device':x['initiating_device'],'availability':x['faults'][x['initiating_device']]})
g=pd.DataFrame(r).groupby('device').availability.agg(lambda v:sorted(v.unique()))
out['L3_100_severities_per_device']=g.to_dict()
# The report attributed a local ENS difference to a later cascade. Localize exact discrepancy.
x=pd.read_csv(ROOT/'outputs/v42_coverage_validation/closed_loop_expert_steps.csv')
a=x[x.episode_family.eq('coverage_L4_0009_h2723')].pivot(index='step',columns='policy',values='P3_e_unserved_kW')
diff=a.secure_milp-a.priority_milp
out['extra_ENS_case_stepwise_difference_kWh']={int(k):float(v*.25) for k,v in diff.items() if abs(v)>1e-5}
sc=json.loads((ROOT/'outputs/v42_coverage_validation/scenario_definitions.json').read_text())
s=next(x for x in sc if x['id']=='coverage_L4_0000_h5456')
out['multiplicative_P0_shock_example']={'global_electric_shock':s['shock']['e'],'extra_P0_electric_shock':s['shock_by_priority']['P0']['e'],'effective_P0_load_multiplier':(1+s['shock']['e'])*(1+s['shock_by_priority']['P0']['e'])}
from scipy.stats import qmc
u=qmc.Sobol(d=8,scramble=True,seed=20261012+7919).random_base2(10)
ii=np.arange(1024)
out['L2_Sobol_index_alias_1024']={'N':1024,
    'electric_active_global_shock_min':float((1.8*u[np.isin(ii%4,[0,3]),0]).min()),
    'electric_active_global_shock_max':float((1.8*u[np.isin(ii%4,[0,3]),0]).max()),
    'heat_active_global_shock_max':float(u[np.isin(ii%4,[1,3]),1].max())}
(_args.output_dir/'range_probes.json').write_text(json.dumps(out,indent=2,ensure_ascii=False))
print(json.dumps(out,indent=2,ensure_ascii=False))
