"""Read-only diagnostic probes: no edits to original project files or outputs."""
from pathlib import Path
import argparse
_parser = argparse.ArgumentParser(description="Read-only diagnostics for the supplied Hospital IES v4.2 project")
_parser.add_argument("--root", type=Path, default=Path.cwd(), help="Extracted original project root")
_parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parent)
_args = _parser.parse_args()
_args.output_dir.mkdir(parents=True, exist_ok=True)
if not (_args.root / "src/hospital_ies/resilience.py").is_file():
    _parser.error("--root must contain src/hospital_ies/resilience.py")
import sys,copy,json,time
import numpy as np
ROOT=_args.root.resolve(); sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from hospital_ies.control_env import HospitalIESEnv
from hospital_ies.resilience import RuntimeState,empty_faults,pre_control_cascade,validate_scenario,update_thermal
out={};env=HospitalIESEnv(ROOT)
sc=json.loads((ROOT/'outputs/v42_coverage_validation/scenario_definitions.json').read_text())
# 1. A response-delay flag does not constrain actual single-step MILP dispatch.
b={'id':'delay_probe','stage':2,'start_hour':5456,'duration_steps':6,'step_minutes':15,'enable_propagation':False,'shock':{'e':1.,'c':.5}}
runs=[]
for delay in [0,3]:
 env.reset(scenario=dict(b,response_delay_steps=delay)); entries=[]
 for _ in range(6):
  _,_,_,_,info=env.step({}); a=info['executed_action']
  entries.append([*a['gt_electric_kW'],a['grid_import_kW'],a['diesel_kW'],*info['unserved_kW'].ravel()])
 runs.append(np.asarray(entries))
out['delay_only_probe_no_propagation']={'delay_steps':[0,3],'max_abs_dispatch_and_ENS_difference':float(np.max(abs(runs[0]-runs[1])))}
# 2. Stress proxy clears its timer whenever controlled=True.
loads=np.zeros((4,3));loads[0,0]=1800
cfg=dict(env.config,pre_event_electric_demand_kW=600.,pre_event_cooling_demand_kW=0.,precontrol_load_shed_enabled=False)
for ctrl in [False,True]:
 state=RuntimeState(np.array([300.,300.,0.]),2400.,set(),np.array([10.,10.,0.]),{})
 trips,stress,debug=pre_control_cascade(state,empty_faults(),loads,env.plant,cfg,15,ctrl,0.,0.,False)
 out['controlled_'+str(ctrl)]={'trips':trips,'stress':stress,'overload_minutes_after':state.overload_minutes.tolist()}
# 3. Already rebalanced permanent load is added again versus pre-event reference.
state=RuntimeState(np.array([300.,300.,0.]),2400.,set(),np.zeros(3),{})
steady=np.zeros((4,3));steady[0,0]=600.
cfg=dict(cfg,pre_event_electric_demand_kW=300.)
trips,stress,debug=pre_control_cascade(state,empty_faults(),steady,env.plant,cfg,15,False,0.,0.,False)
out['recount_surged_load_probe']={'prior_GT_output_kW':600.,'current_electric_demand_kW':600.,'frozen_pre_event_demand_kW':300.,'estimated_transfer_kW':debug['precontrol_GT_transfer_kW'],'trips':trips,'stress':stress}
# 4. The current L4 validator rejects a shock+initial equipment failure.
s=copy.deepcopy(next(x for x in sc if x['stage']==4));s['faults']={'GT_1':0.}
try:validate_scenario(s);out['L4_joint_fault_validation']='accepted'
except ValueError as exc:out['L4_joint_fault_validation']=str(exc)
# 5. Trace a failing L4 case; enabling on-grid diesel is diagnostic ONLY.
s=next(x for x in sc if x['id']=='coverage_L4_0000_h5456')
for allowed in [False,True]:
 trial=copy.deepcopy(s)
 if allowed:trial['diesel_on_grid_allowed']=True
 env.reset(scenario=trial); gttrips=[]
 for _ in range(trial['duration_steps']):
  _,_,_,_,inf=env.step({});gttrips+=inf['new_trips']
 t=env.traces()[0]
 out['L4_diesel_allowed_'+str(allowed)]={'P0_failed_any':bool(t.p0_failed.any()),'P0_e_ENS_kWh':float(t.P0_e_unserved_kW.sum()*.25),'diesel_positive_steps':int(t.diesel_kW.gt(1e-6).sum()),'diesel_energy_kWh':float(t.diesel_kW.sum()*.25),'secondary_trips':gttrips}
# 6. Temperature rise at equal percent unmet does not change with shock multiplier.
params,byhour=env.thermal_cache
s0=np.zeros((4,3));s0[:,2]=100.; loss=.5*s0
results=[]
for factor in [1.,2.]:
 state=RuntimeState(np.zeros(3),2400.,set(),np.zeros(3),{})
 temp,viol=update_thermal(state,params,byhour[420],loss*factor,s0*factor,.25,2.)
 results.append(state.thermal_delta)
out['thermal_equal_fraction_scaling_probe']={'load_and_shortfall_scale':[1,2],'max_deltaT_difference_C':max(abs(results[0][k]-results[1][k]) for k in results[0])}
(_args.output_dir/'logic_probes.json').write_text(json.dumps(out,indent=2,ensure_ascii=False))
print(json.dumps(out,indent=2,ensure_ascii=False))
