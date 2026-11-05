"""Three fixed RF fits on actual paired difference arrays with exact reload."""
from __future__ import annotations
import hashlib
import importlib.metadata
import json
from pathlib import Path
import time
import traceback
import joblib
import numpy as np
from sklearn.ensemble import RandomForestRegressor
from .ranking import TARGET_NAMES

SEEDS=(4101,4102,4103)
CONFIG={'recipe':'execution_aware_RF_rank_projection0','class':'RF','n_estimators':200,'max_depth':6,'min_samples_leaf':2,'n_jobs':1,
        'loss':'multioutput_squared_error_on_train_only_standardized_delta_targets'}


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def write(path,value):Path(path).write_text(json.dumps(value,indent=2,ensure_ascii=False,allow_nan=False),encoding='utf-8')


class RankPredictor:
    def __init__(self,model,metadata):self.model=model;self.metadata=metadata
    def predict(self,x):
        a=np.asarray(x,float)
        if a.ndim!=2 or a.shape[1]!=self.metadata['input_dimension'] or not np.all(np.isfinite(a)):raise ValueError('Unavailable current candidate inputs')
        return self.model.predict(a)*np.asarray(self.metadata['training_target_scale'],float)


def load_rank_model(directory):
    directory=Path(directory);m=json.loads((directory/'metadata.json').read_text(encoding='utf-8'))
    for name,value in m['artifacts'].items():
        if sha(directory/name)!=value:raise ValueError('Rank model artifact byte hash changed')
    return RankPredictor(joblib.load(directory/'model.joblib'),m)


def fit_rank_models(train,validation,scale,out,*,source_binding):
    if not train or not validation or any(r['role']!='train' for r in train) or any(r['role']!='validation' for r in validation):raise ValueError('Explicit train/validation roles required')
    if {r['group_id'] for r in train}&{r['group_id'] for r in validation}:raise ValueError('Train/validation group overlap')
    ids=[r['row_id'] for r in train+validation]
    if len(ids)!=len(set(ids)):raise ValueError('Duplicated rank fit row IDs')
    # Unknowns are documented by the caller and not accepted as made-up zeros.
    x=np.asarray([r['x'] for r in train],float);vx=np.asarray([r['x'] for r in validation],float)
    y=np.asarray([r['delta_y'] for r in train],float);vy=np.asarray([r['delta_y'] for r in validation],float)
    if x.ndim!=2 or vx.shape[1]!=x.shape[1] or y.shape!=(len(x),len(TARGET_NAMES)) or vy.shape!=(len(vx),len(TARGET_NAMES)) or not all(np.all(np.isfinite(a)) for a in (x,vx,y,vy)):
        raise ValueError('Unknown or malformed paired difference inputs/targets')
    out=Path(out)
    if out.exists():raise FileExistsError('Retain every rank fit attempt; fresh output required')
    out.mkdir(parents=True);write(out/'bound_fit_rows.json',{'train':train,'validation':validation,'source_binding':source_binding})
    target_scale=np.maximum(1.,np.std(y,axis=0))
    np.savez_compressed(out/'fit_inputs.npz',x=x,y=y,vx=vx,vy=vy,scale=np.asarray(scale,float),training_target_scale=target_scale,
                        normalized_training_delta_targets=y/target_scale)
    results=[]
    for seed in SEEDS:
        d=out/f'RF_seed{seed}';d.mkdir();start=time.perf_counter()
        write(d/'fit_started.json',{'seed':seed,'config':CONFIG,'source_binding':source_binding,
            'fit_input_file_sha256':sha(out/'fit_inputs.npz'),'row_bindings_sha256':sha(out/'bound_fit_rows.json'),
            'libraries':{k:importlib.metadata.version(k) for k in ('numpy','scikit-learn','joblib')}})
        try:
            model=RandomForestRegressor(**{k:CONFIG[k] for k in ('n_estimators','max_depth','min_samples_leaf','n_jobs')},random_state=seed)
            model.fit(x,y/target_scale);joblib.dump(model,d/'model.joblib',compress=3);pred=model.predict(vx)*target_scale
            np.save(d/'validation_predictions.npy',pred)
            metadata={'schema':'control_improvement.rank_RF.v1','seed':seed,'config':CONFIG,'input_dimension':x.shape[1],
                'targets':list(TARGET_NAMES),'scale_kWh':np.asarray(scale,float).tolist(),'source_binding':source_binding,
                'loss':CONFIG['loss'],'training_target_scale':target_scale.tolist(),
                'target_scale_rule':'max(1 original target unit, population std of training paired delta); no validation/heldout fitting',
                'future_input':False,'predictions_clipped':False,
                'artifacts':{p.name:sha(p) for p in d.iterdir() if p.is_file()}}
            write(d/'metadata.json',metadata);reloaded=load_rank_model(d).predict(vx)
            if not np.array_equal(pred,reloaded):raise AssertionError('Rank model reload differs from actual saved predictions')
            result={'directory':d.name,'seed':seed,'fit_seconds':time.perf_counter()-start,'reload_max_abs_diff':0.,
                'validation_difference_MAE_by_target':np.mean(np.abs(vy-pred),axis=0).tolist(),
                'zero_effect_validation_MAE_by_target':np.mean(np.abs(vy),axis=0).tolist(),
                'metadata_sha256':sha(d/'metadata.json')}
            write(d/'result.json',result);results.append(result)
        except Exception as e:
            write(d/'fit_error.json',{'error_type':type(e).__name__,'message':str(e),'traceback':traceback.format_exc()});raise
    report={'actual_fits':len(results),'models':results,'config':CONFIG,'train_rows':len(train),'validation_rows':len(validation),
        'train_groups':len({r['group_id'] for r in train}),'validation_groups':len({r['group_id'] for r in validation}),
        'source_binding':source_binding,'frozen_development_used_for_selection':False}
    write(out/'FIT_REPORT.json',report);return report
