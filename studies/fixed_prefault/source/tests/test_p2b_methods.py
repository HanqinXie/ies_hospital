"""Independent P2B method regression checks; thresholds preregistered."""
import json
import numpy as np
import pytest
from scipy.linalg import solve
from scipy.stats import qmc, norm

from tools.p2b_methods import DNIPCModel, DSPCEModel
from tools.p2b_methods.basis import CovarianceBasis, modified_cholesky, total_degree_indices
from tools.p2b_methods.dmorph import fixed_weight_dmorph, fixed_weight_eq18, iterative_dmorph
from tools.p2b_methods.sensitivity import ancova, monomial_components
from tools.p2b_methods.benchmarks import d22_function, d24_function, gaussian_copula


def normal_inputs(n=4096, seed=4301, rho=-0.5):
    u = qmc.Sobol(2, scramble=True, seed=seed).random_base2(int(np.log2(n)))
    return norm.ppf(u) @ np.linalg.cholesky([[1, rho], [rho, 1]]).T


def test_index_order_and_correlated_polynomial_recovery():
    idx = total_degree_indices(2, 2)
    assert idx.tolist() == [[0, 0], [1, 0], [0, 1], [2, 0], [1, 1], [0, 2]]
    basis_x, train, valid = normal_inputs(), normal_inputs(128, 4302), normal_inputs(512, 4303)
    fn = lambda x: 2 + x[:, 0] - 3*x[:, 1] + .7*x[:, 0]*x[:, 1] + x[:, 0]**2
    model = DNIPCModel(degree=2, basis_measure={'kind': 'correlated_normal', 'rho': -.5})
    model.fit(train, fn(train), basis_samples=basis_x)
    assert np.linalg.norm(model.predict(valid)-fn(valid))/np.linalg.norm(fn(valid)) < 1e-9
    constant = DNIPCModel(degree=2).fit(train, np.full(len(train), 5.), basis_samples=basis_x)
    assert np.max(np.abs(constant.predict(valid)-5)) < 1e-10


def test_modified_cholesky_covariance_identity_and_singular_measure():
    for a in [np.array([[1., .7], [.7, 1.]]), np.array([[1., 1.], [1., 1.]])]:
        l, delta, diag = modified_cholesky(a)
        assert np.all(delta >= 0)
        assert np.all(np.diag(l) > 0)
        assert np.linalg.norm(l@l.T-a-np.diag(delta))/np.linalg.norm(a) < 1e-10
        inv = solve(l, np.eye(2))
        assert np.linalg.norm(inv@a@inv.T-(np.eye(2)-inv@np.diag(delta)@inv.T)) < 1e-8
    assert np.max(delta) > 0


def test_multivariate_independent_gram_and_stabilization_loss():
    train, valid = normal_inputs(32768, 4301, .7), normal_inputs(65536, 4302, .7)
    basis = CovarianceBasis(2).fit(train)
    phi = basis.transform(valid)
    assert np.max(np.abs(phi.T@phi/len(phi)-np.eye(phi.shape[1]))) < .05
    singular = np.column_stack([train[:, 0], train[:, 0]])
    bad = CovarianceBasis(2).fit(singular)
    assert bad.diagnostics_['delta_max'] > 0
    gram = bad.transform(singular).T@bad.transform(singular)/len(singular)
    assert np.max(np.abs(gram-np.eye(len(gram)))) > .2


def test_analytic_uniform_moments_against_quadrature():
    basis = CovarianceBasis(3).fit(None, moment_spec={'kind':'independent_uniform','low':[1.,1.], 'high':[2.,2.]})
    nodes, weights = np.polynomial.legendre.leggauss(5)
    x = np.array([[a,b] for a in nodes for b in nodes])*.5+1.5
    w = np.outer(weights, weights).ravel()/4
    phi = basis.transform(x)
    assert np.max(np.abs(phi.T@(w[:, None]*phi)-np.eye(phi.shape[1]))) < 1e-10


def test_dmorph_original_equation_weighted_solution_and_kkt():
    rng = np.random.default_rng(4301)
    a = rng.normal(size=(5, 9)); y = rng.normal(size=5); w = np.exp(rng.normal(size=9))
    fast, diag = fixed_weight_dmorph(a, y, w)
    original = fixed_weight_eq18(a, y, w)
    kkt = np.block([[np.diag(w), a.T], [a, np.zeros((5, 5))]])
    direct = solve(kkt, np.r_[np.zeros(9), y])[:9]
    assert np.linalg.norm(fast-direct)/np.linalg.norm(direct) < 1e-8
    assert np.linalg.norm(original-direct)/np.linalg.norm(direct) < 1e-8
    assert np.linalg.norm(a@fast-y) < 1e-8
    projector = np.eye(9)-np.linalg.pinv(a)@a
    assert np.linalg.norm(projector@(w*fast)) < 1e-8


def test_dmorph_iterations_and_final_ols_are_real():
    rng = np.random.default_rng(4301)
    a = np.column_stack([np.ones(35), rng.normal(size=(35, 49))])
    truth=np.zeros(50); truth[[0,2,4]]=[1.,2.,-.5]
    coef, diagnostics = iterative_dmorph(a, a@truth, degree=2)
    assert diagnostics['algorithm'] == 'iterative_dmorph_weighted_constraint_final_ols'
    assert len(diagnostics['iterations']) > 1
    assert diagnostics['converged']
    assert diagnostics['retained_terms'] < a.shape[1]
    assert diagnostics['final_ols_rank'] == diagnostics['retained_terms']
    assert np.linalg.norm(a@coef-a@truth)/np.linalg.norm(a@truth) < 1e-8


def test_negative_covariance_ancova_is_preserved():
    x = normal_inputs(65536)
    components={(0,):x[:,0], (1,):x[:,1]}
    out=ancova(components, x[:,0]+x[:,1], n_variables=2)
    for row in out['variables']:
        assert abs(row['SU']-1) < .02
        assert abs(row['SC']+.5) < .02
        assert abs(row['SI']) < 1e-10
        assert abs(row['S']-.5) < .02
    assert out['component_closure_max_abs'] < 1e-10


def test_correlated_quadratic_analytic_moments_and_coefficient_statistics():
    # rho=-0.5 => X1+X2~N(0,1), so E[Y]=1 and Var[Y]=2 for Y=(X1+X2)^2.
    basis_x=normal_inputs(32768,4301,-.5)
    train=normal_inputs(128,4302,-.5);valid=normal_inputs(65536,4303,-.5)
    model=DNIPCModel(2).fit(train,train.sum(axis=1)**2,basis_samples=basis_x)
    predicted=model.predict(valid)
    assert abs(predicted.mean()-1)<.02
    assert abs(predicted.var()-2)<.02
    assert abs(model.coefficient_moments()['mean']-1)<.02
    assert abs(model.coefficient_moments()['variance']-2)<.02


def test_interaction_component_expansion_after_basis_mixing():
    basis_x = normal_inputs(8192, rho=.7)
    x = normal_inputs(1024, 4303, .7)
    fn=lambda a: 2+a[:,0]+3*a[:,1]+.4*a[:,0]*a[:,1]
    model=DNIPCModel(degree=2).fit(x,fn(x),basis_samples=basis_x)
    components, intercept=monomial_components(model, x)
    assert set(components) == {(0,), (1,), (0,1)}
    reconstructed=intercept+sum(components.values())
    assert np.max(np.abs(reconstructed-model.predict(x))) < 1e-10
    out=ancova(components, model.predict(x), n_variables=2)
    assert out['component_closure_max_abs'] < 1e-10
    for row in out['variables']:
        assert abs(row['S']-row['SU']-row['SC']-row['SI']) < 1e-10


def test_public_function_transcriptions():
    x=np.array([[0.,0.,0.,0.],[1.,1.,1.,1.]])
    # D22 PDF p7 Eq24 has TWO summation symbols; second is not product.
    assert np.allclose(d22_function(x), [1.2*np.log(5), 1.2*np.log(6)], atol=1e-14)
    assert np.allclose(d24_function(np.ones((1,20))), [3-4*10.5+np.log(7)], atol=1e-13)
    with pytest.raises(ValueError): d22_function(np.array([[0.,-6.,0.,0.]]))


def test_gaussian_copula_known_marginal_and_covariance():
    x,z=gaussian_copula(65536, -.5, 4301, scales=(.2,.5))
    mu=np.exp(.5*np.array([.2,.5])**2)
    expected_cov=mu.prod()*(np.exp(-.5*.2*.5)-1)
    assert np.max(np.abs(x.mean(axis=0)-mu)) < .01
    assert abs(np.cov(x.T,ddof=0)[0,1]-expected_cov) < .005
    assert abs(np.corrcoef(z.T)[0,1]+.5) < .02


@pytest.mark.parametrize('cls',[DNIPCModel,DSPCEModel])
def test_serialization_integrity_and_input_contract(tmp_path,cls):
    x=normal_inputs(128)
    model=cls(degree=1,basis_measure={'kind':'normal'},input_names=['x1','x2'],input_units=['1','1'])
    model.fit(x,1+x[:,0]-x[:,1],basis_samples=normal_inputs(4096,4302))
    model.save(tmp_path/'model')
    reload=cls.load(tmp_path/'model')
    assert np.max(np.abs(reload.predict(x)-model.predict(x))) < 1e-12
    with pytest.raises(ValueError): reload.predict(x,input_names=['x2','x1'])
    with pytest.raises(ValueError): reload.predict(x,input_units=['kW','1'])
    meta=tmp_path/'model'/'metadata.json'
    obj=json.loads(meta.read_text());obj['input_names'][0]='changed';meta.write_text(json.dumps(obj))
    with pytest.raises(ValueError,match='hash'): cls.load(tmp_path/'model')


def test_reject_unknown_response_and_no_silent_basis_measure():
    x=normal_inputs(128)
    with pytest.raises(ValueError): DNIPCModel(2).fit(x,np.full(len(x),np.nan),basis_samples=x)
    with pytest.raises(ValueError): DNIPCModel(2).fit(x,x[:,0])
