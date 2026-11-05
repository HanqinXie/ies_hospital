"""Public analytic functions transcribed from visually inspected PDFs."""
import numpy as np
from scipy.stats import norm,beta,gamma,qmc


def d22_function(x):
    x=np.asarray(x,float)
    if x.ndim!=2 or x.shape[1]!=4 or np.any(x+5<=0):raise ValueError('D22 Eq24 requires 4 inputs and x_i > -5')
    return .1*np.log(x+5).sum(axis=1)*np.cos(x[:,1:]-x[:,:-1]).sum(axis=1)


def d22_inputs(n,seed,design='random'):
    if design=='halton':u=qmc.Halton(4,scramble=True,seed=seed).random(n)
    elif design=='sobol':u=qmc.Sobol(4,scramble=True,seed=seed).random_base2(int(np.log2(n)))
    elif design=='random':u=np.random.default_rng(seed).random((n,4))
    else:raise ValueError('unsupported design')
    return np.column_stack([2*u[:,0]-1,norm.ppf(u[:,1],scale=.33),beta.ppf(u[:,2],1.5,3),gamma.ppf(u[:,3],2,scale=.1)])


def d24_function(x):
    x=np.asarray(x,float)
    if x.ndim!=2 or x.shape[1]!=20:raise ValueError('D24 Eq28 requires d=20')
    d=x.shape[1];i=np.arange(1,d+1)
    return 3-5/d*(x@i)+1/d*(x**3@i)+np.log((x*x+x**4)@i/(3*d))


def gaussian_copula(n,rho,seed,scales=(.2,.5)):
    if abs(rho)>=1:raise ValueError('strictly positive definite copula required')
    z=np.random.default_rng(seed).normal(size=(n,2))@np.linalg.cholesky([[1,rho],[rho,1]]).T
    # F_j^-1(Phi(z_j)) = exp(scale_j*z_j) for these lognormal marginals.
    return np.exp(z*np.asarray(scales)[None,:]),z
