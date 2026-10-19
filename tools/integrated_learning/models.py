"""Finite BC recipes with exact fit inputs, weights and reload checks."""
from __future__ import annotations
import copy
import hashlib
import importlib.metadata
import json
from pathlib import Path
import time
import traceback
import joblib
import numpy as np
from sklearn.ensemble import RandomForestRegressor
from tools.p2b_learning.risk import SequencePreprocessor
from .bc import port_metrics, sequence_record

SEEDS=(4101,4102,4103)
REGISTRY=(
    {'id':'BC_constant','class':'constant'},
    {'id':'BC_RF','class':'RF','n_estimators':200,'max_depth':6,'min_samples_leaf':2},
    {'id':'BC_GRU','class':'GRU','hidden_size':16,'epochs':30,'patience':5,
     'head':'softplus_six_ports','loss':'normalized_L1','lr':.001,'batch_size':64},
)


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def write(path,value):Path(path).write_text(json.dumps(value,indent=2,ensure_ascii=False,allow_nan=False),encoding='utf-8')
def array_sha(*arrays):
    h=hashlib.sha256()
    for a in arrays:
        a=np.ascontiguousarray(a);h.update(str(a.dtype).encode());h.update(str(a.shape).encode());h.update(a.tobytes())
    return h.hexdigest()


def make_gru(size,hidden):
    import torch
    class SixPortGRU(torch.nn.Module):
        def __init__(self):
            super().__init__();self.gru=torch.nn.GRU(size,hidden,batch_first=True)
            self.output=torch.nn.Linear(hidden,6)
        def forward(self,x):
            sequence,_=self.gru(x)
            return torch.nn.functional.softplus(self.output(sequence[:,-1]))
    return SixPortGRU()


def _require_cuda_device():
    import torch
    if not torch.cuda.is_available():raise RuntimeError('CUDA is required for existing BC GRU training and inference')
    return torch.device('cuda:0')


def fit_candidate(config,seed,x,y,vx,vy,directory,preprocessor,scale):
    """Private recipe fit; hospital callers must pass the cohort gate first."""
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    if (directory/'fit_started.json').exists():raise FileExistsError('Retain every prior fit attempt; use fresh output')
    x=np.asarray(x,np.float32);vx=np.asarray(vx,np.float32);y=np.asarray(y,float);vy=np.asarray(vy,float);scale=np.asarray(scale,float)
    if x.ndim!=3 or vx.shape[1:]!=x.shape[1:] or y.shape!=(len(x),6) or vy.shape!=(len(vx),6):
        raise ValueError('Shared four-frame six-port fit input shape mismatch')
    if np.any(y<0) or np.any(vy<0) or not all(np.all(np.isfinite(v)) for v in (x,y,vx,vy,scale)):
        raise ValueError('Missing teacher values are unavailable, never zero imputed')
    np.savez_compressed(directory/'fit_inputs.npz',x=x,y_kW=y,vx=vx,vy_kW=vy,training_scale_kW=scale)
    write(directory/'preprocessor.json',preprocessor)
    write(directory/'fit_started.json',{'config':config,'seed':seed,'started_unix':time.time(),
        'input_array_sha256':array_sha(x,y,vx,vy),'actual_input_file_sha256':sha(directory/'fit_inputs.npz'),
        'requested_device':'cuda:0' if config['class']=='GRU' else 'cpu',
        'libraries':{k:importlib.metadata.version(k) for k in ('numpy','scikit-learn','torch','joblib')},
        'scope':'actual arrays supplied by caller; synthetic tests are not hospital validation'})
    start=time.perf_counter();training=[];fit_device='cpu'
    try:
        if config['class']=='constant':
            model=np.mean(y/scale,axis=0);joblib.dump(model,directory/'model.joblib',compress=3)
            pred=np.tile(model,(len(vx),1))*scale
        elif config['class']=='RF':
            model=RandomForestRegressor(n_estimators=config['n_estimators'],max_depth=config['max_depth'],
                min_samples_leaf=config['min_samples_leaf'],random_state=seed,n_jobs=1)
            model.fit(x.reshape(len(x),-1),y/scale);joblib.dump(model,directory/'model.joblib',compress=3)
            pred=model.predict(vx.reshape(len(vx),-1))*scale
        elif config['class']=='GRU':
            import torch
            device=_require_cuda_device();torch.manual_seed(seed);torch.cuda.manual_seed_all(seed)
            model=make_gru(x.shape[-1],config['hidden_size']).to(device)
            xt=torch.tensor(x,device=device);yt=torch.tensor(y/scale,dtype=torch.float32,device=device)
            vxt=torch.tensor(vx,device=device);vyt=torch.tensor(vy/scale,dtype=torch.float32,device=device)
            observed_devices={'model_parameters':tuple(sorted({str(p.device) for p in model.parameters()})),
                'training_inputs':str(xt.device),'training_targets':str(yt.device),
                'validation_inputs':str(vxt.device),'validation_targets':str(vyt.device)}
            if observed_devices['model_parameters']!=('cuda:0',) or any(
                    actual!='cuda:0' for role,actual in observed_devices.items() if role!='model_parameters'):
                raise RuntimeError('BC GRU model and training/validation tensors must all be on cuda:0; '
                    f'observed {observed_devices}')
            fit_device=observed_devices['model_parameters'][0]
            optimizer=torch.optim.Adam(model.parameters(),lr=.001)
            generator=torch.Generator().manual_seed(seed);best=float('inf');best_state=None;stale=0
            for epoch in range(config['epochs']):
                model.train();permutation=torch.randperm(len(xt),generator=generator);loss_sum=0.
                for begin in range(0,len(xt),64):
                    idx=permutation[begin:begin+64].to(device);optimizer.zero_grad()
                    loss=torch.mean(torch.abs(model(xt[idx])-yt[idx]));loss.backward();optimizer.step()
                    loss_sum+=float(loss.detach())*len(idx)
                model.eval()
                with torch.no_grad():validation_loss=float(torch.mean(torch.abs(model(vxt)-vyt)))
                training.append({'epoch':epoch+1,'train_normalized_six_port_MAE':loss_sum/len(xt),
                                 'validation_normalized_six_port_MAE':validation_loss})
                if validation_loss<best:best=validation_loss;best_state=copy.deepcopy(model.state_dict());stale=0
                else:stale+=1
                if stale>=config['patience']:break
            model.load_state_dict(best_state);model.eval();torch.save(best_state,directory/'state_dict.pt')
            with torch.no_grad():pred=model(vxt).cpu().numpy()*scale
        else:raise ValueError('Unregistered BC model class')
        write(directory/'training_curve.json',training)
        artifacts={p.name:sha(p) for p in directory.iterdir() if p.is_file()}
        metadata={'schema':'integrated.bc_model.v1','config':config,'seed':seed,'input_size':x.shape[-1],
            'training_scale_kW':scale.tolist(),'artifacts':artifacts,
            'training_device':fit_device,
            'output_semantics':'six current requested kW targets, subject to unchanged MILP projection and arrival queue',
            'numeric_support':'No calibrated numeric OOD envelope; current schema and categorical support only',
            'future_policy_input':False}
        write(directory/'model_metadata.json',metadata)
        restored=load_predictor(directory);reload_pred=restored.predict_encoded(vx)
        diff=float(np.max(np.abs(pred-reload_pred)))
        if diff!=0:raise AssertionError('BC weights reload does not reproduce predictions exactly')
        np.save(directory/'validation_predictions_kW.npy',pred)
        metrics=port_metrics(vy,pred,scale)
        result=dict(metrics,model_id=config['id'],seed=seed,fit_seconds=time.perf_counter()-start,
            input_array_sha256=array_sha(x,y,vx,vy),reload_max_abs_diff_kW=diff,
            fit_device=fit_device)
        write(directory/'result.json',result);return result
    except Exception as exc:
        write(directory/'fit_error.json',{'type':type(exc).__name__,'message':str(exc),'traceback':traceback.format_exc()})
        raise


class BCPredictor:
    def __init__(self,directory,metadata,preprocessor,model):
        self.directory=Path(directory);self.metadata=metadata;self.preprocessor=preprocessor;self.model=model
        self.scale=np.asarray(metadata['training_scale_kW'],float)

    def in_support(self,history):
        sequence=sequence_record(history)
        for row,mask in zip(sequence['sequence'],sequence['padding_mask']):
            if not mask:continue
            if set(row)!=set(self.preprocessor.keys):return False
            for key,words in self.preprocessor.categorical.items():
                word='__MISSING__' if row[key] is None else str(row[key])
                if word not in words:return False
        return True

    def predict_encoded(self,x):
        kind=self.metadata['config']['class']
        if kind=='constant':return np.tile(self.model,(len(x),1))*self.scale
        if kind=='RF':return self.model.predict(x.reshape(len(x),-1))*self.scale
        import torch
        device=_require_cuda_device()
        with torch.no_grad():return self.model(torch.tensor(x,device=device)).cpu().numpy()*self.scale

    def predict_targets(self,history):
        encoded=self.preprocessor.transform([sequence_record(history)])
        return self.predict_encoded(encoded)[0]


def load_predictor(directory):
    directory=Path(directory);metadata=json.loads((directory/'model_metadata.json').read_text(encoding='utf-8'))
    if metadata['schema']!='integrated.bc_model.v1':raise ValueError('BC model schema mismatch')
    for name,expected in metadata['artifacts'].items():
        if sha(directory/name)!=expected:raise ValueError('BC artifact hash mismatch: '+name)
    pre=SequencePreprocessor.from_dict(json.loads((directory/'preprocessor.json').read_text(encoding='utf-8')))
    if metadata['config']['class']=='GRU':
        import torch
        device=_require_cuda_device()
        model=make_gru(metadata['input_size'],metadata['config']['hidden_size']).to(device)
        model.load_state_dict(torch.load(directory/'state_dict.pt',map_location='cpu',weights_only=True));model.eval()
    else:model=joblib.load(directory/'model.joblib')
    return BCPredictor(directory,metadata,pre,model)
