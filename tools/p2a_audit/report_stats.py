"""Small report tables derived only from this run's saved artifacts."""
import json
from pathlib import Path
import sys
from collections import Counter
import pandas as pd
ROOT=Path(__file__).resolve().parents[2];sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from hospital_ies.dataset_export import write_json
from hospital_ies.dataset_manifest import load_checkpoint,checkpoint_path
BASE=ROOT/'outputs/v43_p2a_20261013_120000';RUN=BASE/'diagnostic_attempt1'

def run():
    old=[];decisions=[];sources=[];subtypes=Counter()
    for name in ('research','unsupported_positive_delay'):
        directory=RUN/name;m=json.loads((directory/'manifest_p2a.json').read_text(encoding='utf-8'))
        old.append(pd.read_csv(directory/'closed_loop_expert_steps.csv'))
        decisions+=json.loads((directory/'decision_timeseries.json').read_text(encoding='utf-8'))
        for s in m['scenarios']:
            for policy in m['policies']:
                p=load_checkpoint(checkpoint_path(directory,s['id'],policy),m['signature'])
                if not p['error']:continue
                subtypes[p['error']['underlying_category']]+=1
                d=p['error']['diagnostic'];a=d['executed_action'];loads=d['loads_kW']
                sources.append(dict(id=s['id'],policy=policy,time=d['time_minute'],phase=d['phase'],
                    GT_kW=a['gt_electric_kW'],grid_arrived_target_kW=a['grid_import_kW'],
                    actual_previous_grid_kW=d['state']['last_grid_import_kW'],diesel_kW=a['diesel_kW'],
                    electric_connected_kW=sum(x[0] for x in loads),underlying_category=p['error']['underlying_category']))
    legacy=pd.concat(old,ignore_index=True);valid=legacy.future_horizon_valid.astype(str).str.lower().isin(['true','1'])
    stats=dict(decision_rows=len(decisions),legacy_rows=len(legacy),legacy_horizon_steps=2,
        legacy_valid=int(valid.sum()),legacy_positive=int(legacy.loc[valid,'future_P0_failure_H'].sum()),
        legacy_negative=int((legacy.loc[valid,'future_P0_failure_H']==0).sum()),
        raw_error_categories=dict(subtypes),failure_sources=sources,
        new_labels={h:dict(positive=sum(d['labels'][h]['value']==1 for d in decisions),
            negative=sum(d['labels'][h]['value']==0 for d in decisions),
            invalid=sum(not d['labels'][h]['valid'] for d in decisions),
            full_horizon=sum(d['labels'][h]['full_horizon'] for d in decisions),
            invalid_reasons=dict(Counter(d['labels'][h]['unknown_reason'] for d in decisions if not d['labels'][h]['valid']))) for h in ('15','30','60')})
    write_json(BASE/'report_statistics.json',stats);print(json.dumps(stats,indent=2,ensure_ascii=True))

if __name__=='__main__':run()
