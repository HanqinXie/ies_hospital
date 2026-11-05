"""D22 Eq11-21 / D24 Eq7-11 multivariate covariance polynomial basis."""
from itertools import combinations_with_replacement
import numpy as np
from scipy.linalg import cholesky, solve_triangular
from scipy.linalg.lapack import dpocon


def total_degree_indices(dimension, degree):
    if dimension < 1 or degree < 0:
        raise ValueError('positive dimension and nonnegative degree required')
    rows = [tuple([0]*dimension)]
    for order in range(1, degree+1):
        block=[]
        for positions in combinations_with_replacement(range(dimension), order):
            row=np.bincount(positions, minlength=dimension)
            block.append(tuple(row))
        rows.extend(sorted(block, key=lambda a: tuple(reversed(a))))
    return np.asarray(rows, dtype=int)


def monomials(x, indices):
    x=np.asarray(x,dtype=float)
    values=np.ones((len(x),len(indices)),dtype=float)
    for j in range(x.shape[1]):
        for power in np.unique(indices[:,j]):
            if power:
                use=np.flatnonzero(indices[:,j]==power)
                values[:,use] *= (x[:,j]**power)[:,None]
    return values


def modified_cholesky(matrix):
    """Zero-shift Cholesky or a nonpivoted Gill/King-style modified LDL.

    Return L, diag(Delta), diagnostic; L L.T = matrix + diag(Delta).
    No claim that a nonzero modification preserves true orthogonality.
    """
    a=np.asarray(matrix,dtype=float)
    if a.ndim!=2 or a.shape[0]!=a.shape[1] or not np.all(np.isfinite(a)):
        raise ValueError('finite square covariance required')
    if not np.allclose(a,a.T,rtol=1e-12,atol=1e-14):
        raise ValueError('covariance must be symmetric')
    a=(a+a.T)/2
    n=len(a)
    if not n:
        return a.copy(),np.zeros(0),{'method':'empty','delta_max':0.}
    floor=np.sqrt(np.finfo(float).eps)*max(1.,np.linalg.norm(a,np.inf))
    try:
        l=cholesky(a,lower=True,check_finite=False)
        if np.min(np.diag(l)**2)>floor:
            return l,np.zeros(n),{'method':'zero_modification_cholesky','delta_max':0.}
    except np.linalg.LinAlgError:
        pass
    gamma=np.max(np.abs(np.diag(a)))
    off=a-np.diag(np.diag(a))
    xi=np.max(np.abs(off))
    beta2=max(gamma,xi/max(1.,np.sqrt(n*n-1)),floor)
    work=a.copy(); unit=np.eye(n); d=np.zeros(n); delta=np.zeros(n)
    for j in range(n):
        theta=np.max(np.abs(work[j+1:,j])) if j+1<n else 0.
        d[j]=max(abs(work[j,j]),theta*theta/beta2,floor)
        delta[j]=d[j]-work[j,j]
        if j+1<n:
            unit[j+1:,j]=work[j+1:,j]/d[j]
            work[j+1:,j+1:] -= d[j]*np.outer(unit[j+1:,j],unit[j+1:,j])
    l=unit*np.sqrt(d)[None,:]
    return l,delta,{'method':'nonpivoted_modified_ldl','delta_max':float(delta.max()),'pivot_floor':float(floor)}


class CovarianceBasis:
    def __init__(self,degree=2):
        self.degree=int(degree)

    def fit(self,samples,moment_spec=None):
        if moment_spec is None and samples is None:
            raise ValueError('independent basis samples or an analytic moment_spec is required')
        if moment_spec is not None:
            if moment_spec['kind']!='independent_uniform':
                raise ValueError('only exact independent uniform moment_spec is supported')
            low=np.asarray(moment_spec['low'],float);high=np.asarray(moment_spec['high'],float)
            if low.shape!=high.shape or np.any(high<=low):raise ValueError('invalid uniform bounds')
            self.center_=(low+high)/2;self.scale_=(high-low)/2
            self.indices_=total_degree_indices(len(low),self.degree)
            idx=self.indices_[1:]
            self.mean_=np.prod(np.where(idx%2==0,1/(idx+1),0.),axis=1)
            second=np.ones((len(idx),len(idx)))
            for j in range(len(low)):
                powers=idx[:,j,None]+idx[None,:,j]
                second*=np.where(powers%2==0,1/(powers+1),0.)
            covariance=second-np.outer(self.mean_,self.mean_)
            moment_source='analytic_independent_uniform'
            sample_count=0
        else:
            samples=np.asarray(samples,float)
            if samples.ndim!=2 or len(samples)<2 or not np.all(np.isfinite(samples)):
                raise ValueError('basis samples must be a finite 2D matrix with >=2 rows')
            self.center_=samples.mean(axis=0);self.scale_=samples.std(axis=0)
            if np.any(self.scale_<=0):raise ValueError('constant input column cannot define polynomial basis')
            self.indices_=total_degree_indices(samples.shape[1],self.degree)
            mono=monomials((samples-self.center_)/self.scale_,self.indices_[1:])
            self.mean_=mono.mean(axis=0)
            centered=mono-self.mean_
            covariance=centered.T@centered/len(samples)
            moment_source='separate_empirical_joint_measure'
            sample_count=len(samples)
        self.factor_,self.delta_,diag=modified_cholesky(covariance)
        if len(covariance):
            rc,info=dpocon(np.asfortranarray(self.factor_),np.linalg.norm(covariance+np.diag(self.delta_),1),uplo='L')
            cond=float(1/rc) if info==0 and rc>0 else float('inf')
            factor_error=float(np.linalg.norm(self.factor_@self.factor_.T-covariance-np.diag(self.delta_))/max(np.linalg.norm(covariance),1e-300))
        else:cond=1.;factor_error=0.
        self.diagnostics_={**diag,'degree':self.degree,'basis_count':len(self.indices_), 'moment_source':moment_source,'basis_sample_count':sample_count,'stabilized_cov_condition_1':cond,'factor_relative_error':factor_error,'delta_sum':float(self.delta_.sum())}
        return self

    def transform(self,x):
        x=np.asarray(x,float)
        if x.ndim!=2 or x.shape[1]!=len(self.center_) or not np.all(np.isfinite(x)):
            raise ValueError('finite input matrix with registered column count required')
        mono=monomials((x-self.center_)/self.scale_,self.indices_[1:])
        phi=solve_triangular(self.factor_,(mono-self.mean_).T,lower=True,check_finite=False).T
        return np.column_stack([np.ones(len(x)),phi])
