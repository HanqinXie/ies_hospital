"""Serializable offline DNIPC/DSPCE models; never simulation controllers."""
import hashlib
import json
from pathlib import Path
import time
import numpy as np
from scipy.linalg import lstsq
from .basis import CovarianceBasis
from .dmorph import iterative_dmorph


def _hash_json(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


class DNIPCModel:
    method='D22_DNIPC'

    def __init__(self,degree=2,basis_measure=None,input_names=None,input_units=None,**fit_options):
        self.degree=int(degree);self.basis_measure=basis_measure or {'kind':'unspecified_research_measure'}
        self.input_names=input_names;self.input_units=input_units;self.fit_options=fit_options

    def fit(self,x,y,basis_samples=None,moment_spec=None):
        start=time.perf_counter()
        x=np.asarray(x,float);y=np.asarray(y,float)
        if x.ndim!=2 or y.shape!=(len(x),) or not np.all(np.isfinite(x)) or not np.all(np.isfinite(y)):
            raise ValueError('finite scalar observed responses and finite 2D inputs required')
        self.input_names=self.input_names or [f'x{i+1}' for i in range(x.shape[1])]
        self.input_units=self.input_units or ['1']*x.shape[1]
        if len(self.input_names)!=x.shape[1] or len(self.input_units)!=x.shape[1]:raise ValueError('column metadata length mismatch')
        self.basis_=CovarianceBasis(self.degree).fit(basis_samples,moment_spec)
        a=self.basis_.transform(x)
        if self.method=='D24_DSPCE':
            self.coef_,fit_diag=iterative_dmorph(a,y,self.degree,**self.fit_options)
            fit_diag['design_rank']=int(np.linalg.matrix_rank(a))
        else:
            self.coef_,_,rank,singular=lstsq(a,y,cond=1e-12,check_finite=False)
            fit_diag={'algorithm':'ordinary_least_squares','rank':int(rank),'retained_terms':int(np.count_nonzero(np.abs(self.coef_)>1e-10)),'min_singular':float(singular[-1]) if len(singular) else 0.}
        self.diagnostics_={'method':self.method,'sample_count':len(x),'basis':self.basis_.diagnostics_,'fit':fit_diag,'fit_seconds':time.perf_counter()-start}
        self.moment_spec_=moment_spec
        return self

    def predict(self,x,input_names=None,input_units=None):
        if input_names is not None and list(input_names)!=self.input_names:raise ValueError('input names/order mismatch')
        if input_units is not None and list(input_units)!=self.input_units:raise ValueError('input units mismatch')
        return self.basis_.transform(x)@self.coef_

    def coefficient_moments(self):
        return {'mean':float(self.coef_[0]),'variance':float(np.dot(self.coef_[1:],self.coef_[1:])),'assumption':'unit Gram under declared basis measure; verify with independent predictions'}

    def save(self,path):
        path=Path(path);path.mkdir(parents=True,exist_ok=True)
        np.savez_compressed(path/'arrays.npz',coef=self.coef_,center=self.basis_.center_,scale=self.basis_.scale_,indices=self.basis_.indices_,mean=self.basis_.mean_,factor=self.basis_.factor_,delta=self.basis_.delta_)
        metadata={'format':'p2b_polynomial_v1','method':self.method,'degree':self.degree,'basis_measure':self.basis_measure,'moment_spec':self.moment_spec_,'input_names':self.input_names,'input_units':self.input_units,'fit_options':self.fit_options,'diagnostics':self.diagnostics_,'arrays_sha256':hashlib.sha256((path/'arrays.npz').read_bytes()).hexdigest()}
        metadata['configuration_sha256']=_hash_json({k:metadata[k] for k in ['method','degree','basis_measure','moment_spec','input_names','input_units','fit_options']})
        metadata['metadata_sha256']=_hash_json(metadata)
        (path/'metadata.json').write_text(json.dumps(metadata,indent=2,allow_nan=False),encoding='utf-8')

    @classmethod
    def load(cls,path):
        path=Path(path);meta=json.loads((path/'metadata.json').read_text(encoding='utf-8'))
        digest=meta.pop('metadata_sha256')
        if _hash_json(meta)!=digest:raise ValueError('metadata hash mismatch')
        if hashlib.sha256((path/'arrays.npz').read_bytes()).hexdigest()!=meta['arrays_sha256']:raise ValueError('array hash mismatch')
        if meta['method']!=cls.method:raise ValueError('model method mismatch')
        model=cls(meta['degree'],meta['basis_measure'],meta['input_names'],meta['input_units'],**meta['fit_options'])
        arr=np.load(path/'arrays.npz',allow_pickle=False)
        model.basis_=CovarianceBasis(model.degree)
        for name in ['center','scale','indices','mean','factor','delta']:setattr(model.basis_,name+'_',arr[name])
        model.basis_.diagnostics_=meta['diagnostics']['basis'];model.coef_=arr['coef']
        model.diagnostics_=meta['diagnostics'];model.moment_spec_=meta['moment_spec']
        return model


class DSPCEModel(DNIPCModel):
    method='D24_DSPCE'
