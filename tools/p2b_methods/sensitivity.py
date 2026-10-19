"""D24 Eq23-27 covariance indices with an explicit component convention."""
import numpy as np
from scipy.linalg import solve_triangular
from .basis import monomials


def monomial_components(model,x,tolerance=1e-10):
    """Expand mixed basis first, then group actual affine-coordinate support.

    This is an explicit algebraic convention, not unique dependent ANOVA.
    Components are centered on the supplied target-measure evaluation points.
    """
    basis=model.basis_
    raw=solve_triangular(basis.factor_.T,model.coef_[1:],lower=False)
    intercept=float(model.coef_[0]-np.dot(basis.mean_,raw))
    values=monomials((np.asarray(x)-basis.center_)/basis.scale_,basis.indices_[1:])
    components={}
    for coef,idx,value in zip(raw,basis.indices_[1:],values.T):
        if abs(coef)<=tolerance:continue
        support=tuple(np.flatnonzero(idx))
        components[support]=components.get(support,np.zeros(len(x)))+coef*value
    for support in components:
        mean=float(components[support].mean());intercept+=mean;components[support]-=mean
    return components,intercept


def ancova(components,response,n_variables):
    y=np.asarray(response,float);yc=y-y.mean();var=float(np.mean(yc*yc))
    if var<=0:raise ValueError('ANCOVA undefined for constant response')
    centered={tuple(k):np.asarray(v,float)-np.mean(v) for k,v in components.items()}
    reconstructed=sum(centered.values(),np.zeros(len(y)))
    result=[]
    for i in range(n_variables):
        mi=centered.get((i,),np.zeros(len(y)))
        other=sum((v for k,v in centered.items() if i not in k),np.zeros(len(y)))
        interaction=sum((v for k,v in centered.items() if i in k and len(k)>1),np.zeros(len(y)))
        result.append({'variable':i,'SU':float(np.mean(mi*mi)/var),'SC':float(np.mean(mi*other)/var),'SI':float(np.mean(mi*interaction)/var),'S':float(np.mean(mi*yc)/var)})
    return {'variables':result,'variance':var,'component_closure_max_abs':float(np.max(np.abs(yc-reconstructed))),'convention':'centered actual monomial supports in registered affine input coordinates; not causal or unique dependent ANOVA'}
