"""D24 Eq12-20. True D-MORPH weighted constraint problem and IRLS."""
import numpy as np
from scipy.linalg import lstsq, solve, svd


def fixed_weight_eq18(a,y,weights):
    """Literal small-system Eq18/19 SVD expression; verification reference."""
    a=np.asarray(a,float);y=np.asarray(y,float);w=np.asarray(weights,float)
    pinv=np.linalg.pinv(a)
    projector=np.eye(a.shape[1])-pinv@a
    u,s,vh=svd(projector*w[None,:],full_matrices=True)
    # Mathematically rank(PW)=rank(P)=p-rank(A). Floating projector noise
    # must not invent additional singular values in the exact null space.
    r=a.shape[1]-np.linalg.matrix_rank(a)
    u0=u[:,r:];v0=vh.T[:,r:]
    return v0@solve(u0.T@v0,u0.T@(pinv@y))


def fixed_weight_dmorph(a,y,weights):
    """Weighted equality minimizer, equivalent to the Eq18 infinite-time limit.

    Solve min 0.5 beta.T W beta, A beta = projection_range(A)(y).
    For full row rank use W^-1 A.T (A W^-1 A.T)^-1 y;
    a rank-deficient/inconsistent design uses weighted SVD least squares
    and explicitly records the residual. No L1/LAR/LASSO solver is used.
    """
    a=np.asarray(a,float);y=np.asarray(y,float);w=np.asarray(weights,float)
    if a.ndim!=2 or y.shape!=(a.shape[0],) or w.shape!=(a.shape[1],) or np.any(w<=0) or not np.all(np.isfinite(w)):
        raise ValueError('invalid weighted constraint inputs')
    scaled=w/np.max(w)
    invw=1/scaled
    if a.shape[0] < a.shape[1]:
        system=(a*invw[None,:])@a.T
        try:
            dual=solve(system,y,assume_a='pos',check_finite=False)
            beta=invw*(a.T@dual)
            if np.linalg.norm(a@beta-y)>1e-7*max(1.,np.linalg.norm(y)):
                raise np.linalg.LinAlgError('weighted constraint residual requires SVD')
            backend='weighted_dual_spd'
        except np.linalg.LinAlgError:
            scaled_a=a/np.sqrt(scaled)[None,:]
            z,_,_,_=lstsq(scaled_a,y,cond=1e-12,check_finite=False)
            beta=z/np.sqrt(scaled);backend='weighted_svd_fallback'
    else:
        scaled_a=a/np.sqrt(scaled)[None,:]
        z,_,_,_=lstsq(scaled_a,y,cond=1e-12,check_finite=False)
        beta=z/np.sqrt(scaled);backend='weighted_svd_overdetermined'
    return beta,{'backend':backend,'constraint_relative_residual':float(np.linalg.norm(a@beta-y)/max(1.,np.linalg.norm(y)))}


def iterative_dmorph(a,y,degree,max_iter=100,tolerance=1e-6,epsilon=1e-8,prune_relative=1e-4):
    a=np.asarray(a,float);y=np.asarray(y,float)
    p=a.shape[1]
    weights=(2*np.arange(1,p+1,dtype=float))**(3*degree)
    beta,initial=fixed_weight_dmorph(a,y,weights)
    history=[{'iteration':0,'relative_change':None,'objective':float(.5*np.dot(beta*weights/weights.max(),beta)),**initial}]
    converged=False
    for iteration in range(1,max_iter+1):
        weights=1/(np.abs(beta)+epsilon)
        nxt,diag=fixed_weight_dmorph(a,y,weights)
        change=float(np.linalg.norm(nxt-beta)/max(np.linalg.norm(beta),epsilon))
        history.append({'iteration':iteration,'relative_change':change,'objective':float(.5*np.dot(nxt*weights,nxt)),**diag})
        beta=nxt
        if change<=tolerance:
            converged=True;break
    threshold=max(1e-9,prune_relative*(np.max(np.abs(beta[1:])) if p>1 else 0.))
    keep=np.abs(beta)>threshold;keep[0]=True
    final=np.zeros(p)
    final[keep],_,rank,singular=lstsq(a[:,keep],y,cond=1e-12,check_finite=False)
    return final,{'algorithm':'iterative_dmorph_weighted_constraint_final_ols','iterations':history,'converged':converged,'stop_reason':'coefficient_relative_change' if converged else 'max_iter','max_iter':max_iter,'tolerance':tolerance,'epsilon':epsilon,'prune_relative':prune_relative,'prune_absolute_threshold':float(threshold),'retained_terms':int(keep.sum()),'retained_indices':np.flatnonzero(keep).tolist(),'final_ols_rank':int(rank),'final_relative_residual':float(np.linalg.norm(a@final-y)/max(1.,np.linalg.norm(y))),'final_ols_min_singular':float(singular[-1]) if len(singular) else 0.}
