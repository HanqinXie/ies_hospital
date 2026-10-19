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
root=_args.root.resolve();sys.path[:0]=[str(root),str(root/'src')]
from train_nonmpc_baselines import _groups_split
from hospital_ies.training_schema import build_features,FEATURES,ACTIONS
s=pd.read_csv(root/'outputs/v42_coverage_validation/closed_loop_expert_steps.csv')
e=pd.read_csv(root/'outputs/v42_coverage_validation/closed_loop_episode_summary.csv')
sc=json.loads((root/'outputs/v42_coverage_validation/scenario_definitions.json').read_text())
d=s.loc[s.policy.eq('secure_milp')].copy()
good=d.loc[d.sampling_cohort.ne('stress_ood')&d.future_horizon_valid].copy()
tr,te=_groups_split(good,'future_P0_failure_H',20261012)
pred=pd.read_csv(root/'outputs/v42_coverage_train/risk/heldout_predictions.csv')
pred=pred.merge(d[['episode_family','step','p0_failed']],on=['episode_family','step'])
first=d.loc[d.p0_failed.eq(1)].groupby('episode_family').step.min()
pred['first_failure_step']=pred.episode_family.map(first)
pred['alarm']=pred.predicted_probability.ge(.5)
summary={'rows':len(s),'families':s.episode_family.nunique(),'features':len(FEATURES),
 'risk_train_rows':int(tr.sum()),'risk_test_rows':int(te.sum()),
 'risk_train_positive_rows':int(good.loc[tr].future_P0_failure_H.sum()),
 'risk_train_positive_families':int(good.loc[tr].groupby('episode_family').future_P0_failure_H.max().sum()),
 'risk_test_positive_families':int(good.loc[te].groupby('episode_family').future_P0_failure_H.max().sum()),
 'risk_train_positive_ids':good.loc[tr & good.future_P0_failure_H.eq(1)].episode_family.unique().tolist(),
 'risk_test_positive_ids':good.loc[te & good.future_P0_failure_H.eq(1)].episode_family.unique().tolist(),
 'test_positive_predictions':pred.loc[pred.future_P0_failure_H.eq(1)].to_dict('records')}
for p,g in s.groupby('policy'):
 summary[p]={'rows':len(g),'mean_decision_ms':float(g.decision_ms.mean()),'median_decision_ms':float(g.decision_ms.median()),'cost':float(g.cost_USD_step.sum()),'ENS_kWh':float(g[[f'{G}_{k}_unserved_kW' for G in ['P0','P1','P2','P3'] for k in ['e','h','c']]].sum().sum()*.25),'diesel_positive_steps':int(g.diesel_kW.gt(1e-6).sum()),'diesel_positive_L4':int(g.loc[g.stage.eq(4)].diesel_kW.gt(1e-6).sum())}
allpos=d.loc[d.future_horizon_valid & d.future_P0_failure_H.eq(1)].copy()
allpos['first']=allpos.episode_family.map(first)
summary['positive_rows_pre_first_failure']=int((allpos.step<allpos['first']).sum())
summary['positive_rows_at_or_after_first_failure']=int((allpos.step>=allpos['first']).sum())
summary['L4_faults_defined_count']=sum(bool(x.get('faults')) for x in sc if x['stage']==4)
summary['label_eventtiming']=first.to_dict()
main=d.loc[d.sampling_cohort.ne('stress_ood')]
a,b=_groups_split(main,'p0_failed',20261012)
summary['imitation_train_rows']=int(a.sum());summary['imitation_test_rows']=int(b.sum())
summary['split_risk_imitation_same_ids']=set(good.loc[tr].episode_family)==set(main.loc[a].episode_family)
valid=d[d.future_horizon_valid].copy();valid['first']=valid.episode_family.map(first)
summary['all_positive_families']=[{'family':f,'future_positive_rows':int(g.future_P0_failure_H.sum()),'first_failure_step':int(first[f])} for f,g in valid.groupby('episode_family') if g.future_P0_failure_H.any()]
(_args.output_dir/'data_checks.json').write_text(json.dumps(summary,indent=2,ensure_ascii=False))
print(json.dumps(summary,indent=2,ensure_ascii=False))
# Track state transitions in two key examples.
cols=['policy','step','control_delayed','pre_GT_1_kW','pre_GT_2_kW','pre_GT_3_kW','gt_electric_kW_1','gt_electric_kW_2','gt_electric_kW_3','grid_import_kW','diesel_kW','new_trips','P0_e_unserved_kW','P3_e_unserved_kW','demand_shock_e']
for fid in ['coverage_L3_0018_h7767','coverage_L4_0000_h5456','coverage_L4_0009_h2723']:
 print('\nCASE:',fid);print(s.loc[s.episode_family.eq(fid),cols].to_string(index=False))
