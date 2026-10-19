"""Reproducible independent public-function runs and method evidence.

Run only after preregistration. Every model attempt is preserved, including
nonconvergence. This script does not import or invoke the hospital simulator.
"""
import argparse
import json
from pathlib import Path
import time
import traceback
import warnings
from math import comb
import numpy as np
from scipy.stats import qmc,norm
from threadpoolctl import threadpool_limits
from .models import DNIPCModel,DSPCEModel
from .basis import CovarianceBasis,modified_cholesky
from .dmorph import fixed_weight_dmorph,fixed_weight_eq18
from .benchmarks import d22_function,d22_inputs,d24_function,gaussian_copula
from .sensitivity import ancova,monomial_components


def write_json(path,data):
    Path(path).write_text(json.dumps(data,indent=2,allow_nan=False),encoding='utf-8')


def metrics(y,pred):
    error=pred-y
    return {'rMSE':float(np.sum(error**2)/np.sum((y-y.mean())**2)),'RMSE':float(np.sqrt(np.mean(error**2))),'MAE':float(np.mean(np.abs(error))),'reference_mean':float(y.mean()),'prediction_mean':float(pred.mean()),'mean_abs_error':float(abs(y.mean()-pred.mean())),'reference_std':float(y.std()),'prediction_std':float(pred.std()),'std_abs_error':float(abs(y.std()-pred.std())),'mean_relative_error':float(abs(y.mean()-pred.mean())/max(abs(y.mean()),1e-30)),'std_relative_error':float(abs(y.std()-pred.std())/max(y.std(),1e-30)),'quantile_probabilities':[.01,.05,.5,.95,.99],'reference_quantiles':np.quantile(y,[.01,.05,.5,.95,.99]).tolist(),'prediction_quantiles':np.quantile(pred,[.01,.05,.5,.95,.99]).tolist()}


def gram_check(basis,x):
    phi=basis.transform(x);gram=phi.T@phi/len(phi)
    second=(phi*phi).T@(phi*phi)/len(phi)
    se=np.sqrt(np.maximum(0,second-gram*gram)/len(phi))
    error=np.abs(gram-np.eye(gram.shape[0]))
    return {'max_abs_gram_error':float(error.max()),'mean_residual_max':float(np.abs(phi[:,1:].mean(axis=0)).max()),'max_entry_standard_error':float(se.max()),'entries_exceeding_max_005_or_6se':int(np.count_nonzero(error>np.maximum(.05,6*se))),'gram_condition_2':float(np.linalg.cond(gram))}


def run_checks(out):
    rng=np.random.default_rng(4301);a=rng.normal(size=(5,9));y=rng.normal(size=5);w=np.exp(rng.normal(size=9))
    fast,diag=fixed_weight_dmorph(a,y,w);original=fixed_weight_eq18(a,y,w)
    projector=np.eye(9)-np.linalg.pinv(a)@a
    checks={'fixed_weight_dmorph':{'coefficient_relative_difference_original_eq18':float(np.linalg.norm(fast-original)/np.linalg.norm(original)),'constraint_relative_residual':diag['constraint_relative_residual'],'projected_gradient_norm':float(np.linalg.norm(projector@(w*fast)))}}
    x,z=gaussian_copula(65536,-.5,4301)
    mu=np.exp(.5*np.array([.2,.5])**2);cov=mu.prod()*(np.exp(-.5*.2*.5)-1)
    sample_cov=np.mean((x[:,0]-mu[0])*(x[:,1]-mu[1]));cov_se=np.std((x[:,0]-mu[0])*(x[:,1]-mu[1]))/np.sqrt(len(x))
    mean_se=x.std(axis=0)/np.sqrt(len(x))
    checks['gaussian_copula']={'definition':'latent N(0,R), rho=-0.5; exp(0.2Z1), exp(0.5Z2)','n':len(x),'seed':4301,'expected_mean':mu.tolist(),'observed_mean':x.mean(axis=0).tolist(),'mean_standard_error':mean_se.tolist(),'expected_covariance':float(cov),'observed_covariance_about_exact_mean':float(sample_cov),'covariance_standard_error':float(cov_se),'latent_correlation':float(np.corrcoef(z.T)[0,1]),'passed':bool(np.all(np.abs(x.mean(axis=0)-mu)<=np.maximum(.002,6*mean_se)) and abs(sample_cov-cov)<=max(.002,6*cov_se) and abs(np.corrcoef(z.T)[0,1]+.5)<=.02)}
    def normal_sample(n,seed,rho):
        u=qmc.Sobol(2,scramble=True,seed=seed).random_base2(int(np.log2(n)))
        return norm.ppf(u)@np.linalg.cholesky([[1,rho],[rho,1]]).T
    xx=normal_sample(65536,4301,-.5)
    checks['negative_covariance_ancova']=ancova({(0,):xx[:,0],(1,):xx[:,1]},xx.sum(axis=1),2)
    checks['negative_covariance_ancova']['analytic']={'SU':1.,'SC':-.5,'SI':0.,'S':.5,'variance':1.}
    qtrain=normal_sample(128,4302,-.5);qvalid=normal_sample(65536,4303,-.5)
    qmodel=DNIPCModel(2).fit(qtrain,qtrain.sum(axis=1)**2,basis_samples=xx[:32768])
    qpred=qmodel.predict(qvalid)
    checks['correlated_quadratic_moments']={'function':'(X1+X2)^2','rho':-.5,'analytic_mean':1.,'analytic_variance':2.,'predicted_mean':float(qpred.mean()),'predicted_variance':float(qpred.var()),'coefficient_moments':qmodel.coefficient_moments(),'passed':bool(abs(qpred.mean()-1)<.02 and abs(qpred.var()-2)<.02)}
    indep=normal_sample(65536,4302,0.)
    checks['independent_quadratic_interaction']=ancova({(0,):indep[:,0],(1,):2*indep[:,1],(0,1):.5*indep[:,0]*indep[:,1]},indep[:,0]+2*indep[:,1]+.5*indep[:,0]*indep[:,1],2)
    bx=normal_sample(32768,4301,.7);vx=normal_sample(65536,4302,.7)
    checks['degree_stability']=[]
    for degree in [1,2,3,4]:
        basis=CovarianceBasis(degree).fit(bx)
        checks['degree_stability'].append({'degree':degree,'basis':basis.diagnostics_,'independent_gram':gram_check(basis,vx)})
    singular=np.column_stack([bx[:,0],bx[:,0]]);bad=CovarianceBasis(2).fit(singular)
    phi=bad.transform(singular)
    checks['rank_deficient_measure']={'basis':bad.diagnostics_,'gram_max_abs_error':float(np.max(np.abs(phi.T@phi/len(phi)-np.eye(phi.shape[1])))),'expected':'stabilized but NOT orthogonal to singular original measure'}
    write_json(out/'independent_checks.json',checks)
    return checks


def run_d22(out):
    directory=out/'D22';directory.mkdir()
    basis_x=d22_inputs(65536,2201,'sobol');select_x=d22_inputs(4096,2210,'sobol');valid_x=d22_inputs(10000,2203)
    selection_y=d22_function(select_x);valid_y=d22_function(valid_x)
    np.savez_compressed(directory/'measure_selection_validation_data.npz',basis_X=basis_x,selection_X=select_x,selection_y=selection_y,validation_X=valid_x,validation_y=valid_y)
    results=[]
    for p in [2,3,4,5]:
        n=2*comb(p+4,4);x=d22_inputs(n,2202,'halton');y=d22_function(x)
        np.savez_compressed(directory/f'order_{p}_fit_data.npz',X=x,y=y)
        trial={'degree':p,'basis_count':comb(p+4,4),'N':n,'seeds':{'basis':2201,'fit':2202,'selection':2210,'validation':2203},'basis_measure':'independent Table1 marginals; empirical Sobol moments','design_distribution':'scrambled Halton transformed through independent marginals','validation_distribution':'independent pseudorandom Table1 marginals','status':'attempted'}
        print(f'D22 degree={p}, N={n}',flush=True)
        try:
            model=DNIPCModel(p,basis_measure={'source':'D22_Table1','gamma_b_interpretation':'scale'}).fit(x,y,basis_samples=basis_x)
            trial['diagnostics']=model.diagnostics_;trial['selection']=metrics(selection_y,model.predict(select_x));trial['validation']=metrics(valid_y,model.predict(valid_x))
            trial['coefficient_moments']=model.coefficient_moments();trial['independent_gram']=gram_check(model.basis_,valid_x)
            model.save(directory/f'order_{p}_model');reloaded=DNIPCModel.load(directory/f'order_{p}_model')
            trial['serialization_max_abs_error']=float(np.max(np.abs(reloaded.predict(valid_x[:100])-model.predict(valid_x[:100]))))
            trial['status']='completed'
        except Exception as exc:
            trial['status']='failed';trial['error_type']=type(exc).__name__;trial['traceback']=traceback.format_exc()
        write_json(directory/f'order_{p}.json',trial);results.append(trial)
    available=[r for r in results if r['status']=='completed']
    best=min(available,key=lambda r:r['selection']['rMSE']) if available else None
    passed=bool(best and best['validation']['RMSE']<=.003 and best['validation']['mean_abs_error']<=.003 and best['validation']['std_abs_error']<=.003)
    result={'source':'D22 PDF p7 Eq24 and Table1, p8 Table2','formula':'0.1 * sum_i=1..4 ln(x_i+5) * sum_i=1..3 cos(x_(i+1)-x_i)','trials':results,'selected_degree_by_selection_only':best['degree'] if best else None,'passed_registered_public_function_tolerance':passed,'original_table2_reference':{'MCS_mean':.7473,'MCS_std':.0882,'note':'quoted source values for comparison only; not this run'},'scope':'printed public formula numerical replication; not CFD data reproduction'}
    result['source_discrepancy']={'literal_equation_validation_mean':float(valid_y.mean()),'literal_equation_validation_std':float(valid_y.std()),'mean_MC_standard_error':float(valid_y.std()/np.sqrt(len(valid_y))),'diagnostic_only_if_ln_replaced_by_log10_mean':float(valid_y.mean()/np.log(10)),'diagnostic_only_if_ln_replaced_by_log10_std':float(valid_y.std()/np.log(10)),'interpretation':'literal printed ln formula conflicts with Table2; log10 is a possible explanation by numerical scale, not a source-confirmed correction and not used for fitting'}
    write_json(directory/'summary.json',result);return result


def run_d24(out):
    directory=out/'D24';directory.mkdir()
    spec={'kind':'independent_uniform','low':[1.]*20,'high':[2.]*20}
    selection_x=1+qmc.Sobol(20,scramble=True,seed=2402).random_base2(11)
    selection_y=d24_function(selection_x)
    valid_x=1+np.random.default_rng(2403).random((5000,20));valid_y=d24_function(valid_x)
    np.savez_compressed(directory/'selection_validation_data.npz',selection_X=selection_x,selection_y=selection_y,validation_X=valid_x,validation_y=valid_y)
    ref=[]
    for seed in range(2410,2418):
        x=1+qmc.Sobol(20,scramble=True,seed=seed).random_base2(16);y=d24_function(x)
        ref.append({'seed':seed,'N':len(x),'mean':float(y.mean()),'std':float(y.std())})
    reference={'replicates':ref,'mean_of_means':float(np.mean([r['mean'] for r in ref])),'mean_standard_error_across_scrambles':float(np.std([r['mean'] for r in ref],ddof=1)/np.sqrt(len(ref))),'mean_of_stds':float(np.mean([r['std'] for r in ref]))}
    results=[]
    for n in [200,300]:
        # Prefixes of one registered Sobol sequence; both methods see same rows.
        x=1+qmc.Sobol(20,scramble=True,seed=2401).random_base2(9)[:n];y=d24_function(x)
        np.savez_compressed(directory/f'N{n}_fit_data.npz',X=x,y=y)
        for p in [1,2,3]:
            for cls in [DNIPCModel,DSPCEModel]:
                name='OLS_PCE' if cls is DNIPCModel else 'DSPCE'
                trial={'method':name,'degree':p,'basis_count':comb(p+20,20),'N':n,'seed_fit':2401,'basis_measure':'independent U[1,2]^20; exact uniform moments in affine coordinates','design_distribution':'scrambled Sobol prefixes of U[1,2]^20','validation_distribution':'independent pseudorandom U[1,2]^20','status':'attempted'}
                print(f'D24 {name} degree={p}, N={n}, terms={trial["basis_count"]}',flush=True)
                try:
                    with warnings.catch_warnings(record=True) as caught:
                        warnings.simplefilter('always')
                        model=cls(p,basis_measure={'kind':'independent_uniform','low':1,'high':2,'dimension':20}).fit(x,y,moment_spec=spec)
                    trial['warnings']=[str(w.message) for w in caught]
                    trial['diagnostics']=model.diagnostics_;trial['selection']=metrics(selection_y,model.predict(selection_x));trial['validation']=metrics(valid_y,model.predict(valid_x));trial['coefficient_moments']=model.coefficient_moments()
                    model.save(directory/f'{name}_N{n}_p{p}_model')
                    trial['status']='completed' if cls is DNIPCModel or model.diagnostics_['fit']['converged'] else 'nonconverged'
                except Exception as exc:
                    trial['status']='failed';trial['error_type']=type(exc).__name__;trial['traceback']=traceback.format_exc()
                write_json(directory/f'{name}_N{n}_p{p}.json',trial);results.append(trial)
    selected=[]
    for n in [200,300]:
        for name in ['OLS_PCE','DSPCE']:
            available=[r for r in results if r['N']==n and r['method']==name and r['status']=='completed']
            if not available:
                selected.append({'N':n,'method':name,'status':'no_completed_converged_trial'});continue
            best=min(available,key=lambda r:r['selection']['rMSE'])
            selected.append({'N':n,'method':name,'degree':best['degree'],'basis_count':best['basis_count'],'selection_rMSE':best['selection']['rMSE'],'validation':best['validation'],'status':'selected_by_separate_selection_set','passed_registered_tolerance':bool(best['validation']['rMSE']<=.02 and best['validation']['mean_relative_error']<=.02 and best['validation']['std_relative_error']<=.02)})
    result={'source':'D24 PDF p9 Eq28, p10 Eq29/Table2','formula':'3 - (5/d)*sum(i*x_i) + (1/d)*sum(i*x_i^3) + ln(sum(i*(x_i^2+x_i^4))/(3*d)); i=1..d,d=20','trials':results,'selected':selected,'reference_moments':reference,'original_table2_reference':{'MCS_mean':-32.9903,'MCS_std':1.8695,'DSPCE_N200_rMSE':.000543,'DSPCE_N300_rMSE':.000162,'note':'source comparison only, never used as fitted results'},'scope':'public 20D function, not compressor CFD or measurement reproduction'}
    write_json(directory/'summary.json',result);return result


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--out',required=True);args=parser.parse_args()
    out=Path(args.out)
    if out.exists():raise SystemExit('Refusing to overwrite prior run; use a new output directory')
    out.mkdir(parents=True);start=time.perf_counter()
    with threadpool_limits(limits=2):
        checks=run_checks(out);d22=run_d22(out);d24=run_d24(out)
    summary={'runtime_seconds':time.perf_counter()-start,'independent_checks':'independent_checks.json','D22_passed':d22['passed_registered_public_function_tolerance'],'D24_selected':d24['selected'],'D24_nonconverged_trials':sum(r['status']=='nonconverged' for r in d24['trials']),'D24_failed_trials':sum(r['status']=='failed' for r in d24['trials']),'D22_failed_trials':sum(r['status']=='failed' for r in d22['trials'])}
    write_json(out/'summary.json',summary)
    print(json.dumps(summary,indent=2),flush=True)


if __name__=='__main__':main()
