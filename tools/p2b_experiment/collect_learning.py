"""Summarize completed offline trials without fitting or evaluating new responses."""
import json
import sys
from pathlib import Path
from collections import Counter

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from tools.p2b_learning.regression import candidate_registry
from tools.p2b_experiment.audit import write_csv


def main(base):
    base=Path(base);learning=base/'learning'
    reg=json.loads((learning/'regression_attempt1/REGRESSION_REPORT.json').read_text(encoding='utf-8'))
    risk=json.loads((learning/'risk_attempt1/RISK_REPORT.json').read_text(encoding='utf-8'))
    trials=[];selections=[]
    for r in reg['results']:
        for t in r.get('trials',[]):trials.append(dict(task='regression',condition=r['condition'],**t))
    for s in reg['selections']:
        for seed in s.get('selected_seed_evidence',[]):
            met=seed['validation_metrics'];selections.append(dict(condition=s['condition'],selected=s['selected']['config_id'],seed=seed['seed'],**{k:v for k,v in met.items() if isinstance(v,(int,float,str)) or v is None}))
    for r in risk['reports']:
        for t in r.get('trials',[]):trials.append(dict(task='risk',condition=r['policy'],**t))
    configs=candidate_registry();registry=[]
    for c in configs:
        tt=[t for t in trials if t['config_id']==c['config_id']]
        completed=[t for t in tt if t['status']=='completed']
        status='fitted' if completed else 'failed_validation' if not c['eligible'] else 'fit_failed' if any(t['status']=='failed' for t in tt) else 'implemented_unfitted'
        registry.append(dict(**c,implementation_status=status,hospital_trial_counts=dict(Counter(t['status'] for t in tt)),public_math_scope=('D22 public formula; full low-dimensional rank guard' if c['class']=='DNIPC' else 'D24 p2 validated; p3 public trials nonconverged; full rank weighted stage equals OLS' if c['class']=='DSPCE' else 'software synthetic checks plus conditional hospital validation'),no_control_use=True))
    model_records=[]
    for folder in ('regression_attempt1','risk_attempt1'):
        for path in (learning/folder).rglob('provenance.json'):
            p=json.loads(path.read_text(encoding='utf-8'))
            model_records.append(dict(path=path.relative_to(base).as_posix(),config=p.get('config'),seed=p.get('seed'),fit_dataset_ids=p.get('fit_dataset_ids',[]),status=p.get('status','fitted'),manifest_signature=p.get('manifest_signature'),source_provenance=p.get('implementation_provenance')))
    result=dict(methods=registry,fitted_model_records=model_records,regression_status_counts=dict(Counter(r['status'] for r in reg['results'])),trial_status_counts=dict(Counter(t['status'] for t in trials)),risk_statuses=[dict(policy=r['policy'],status=r['status'],gate=r['gate'],selection=r.get('selection')) for r in risk['reports']],scope='P2B offline only; no controller network, RL, MPC, final blind test or real hospital probability')
    (learning/'ALGORITHM_REGISTRY_P2B.json').write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding='utf-8')
    (learning/'all_offline_trials.json').write_text(json.dumps(trials,indent=2,ensure_ascii=False),encoding='utf-8')
    write_csv(learning/'selected_regression_metrics.csv',selections)
    print(json.dumps({k:v for k,v in result.items() if k not in ('fitted_model_records','methods')},indent=2))


if __name__=='__main__':main(sys.argv[1])
