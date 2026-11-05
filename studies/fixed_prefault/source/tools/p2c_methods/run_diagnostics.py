"""Bounded, preregistered P2C mathematical runs and read-only old-model audits."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import shutil
import time
import traceback
import numpy as np
from scipy.linalg import lstsq, svd, svdvals
from scipy.stats import qmc
from threadpoolctl import threadpool_limits
from tools.p2b_methods.models import DNIPCModel, DSPCEModel
from tools.p2b_methods.basis import CovarianceBasis
from tools.p2b_methods.benchmarks import d22_inputs, d24_function
from tools.p2b_methods.validate import metrics
from tools.p2b_learning.regression import sample_measure
from .diagnostics import gram_diagnostics, moment_diagnostics, iterative_diagnostics


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_bundle(directory, data):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    arrays = {}
    def walk(value, name='root'):
        if isinstance(value, np.ndarray):
            arrays[name] = value
            return {'array': name, 'file': 'raw.npz', 'shape': list(value.shape), 'dtype': str(value.dtype)}
        if isinstance(value, dict):
            return {str(k): walk(v, name + '__' + str(k)) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [walk(v, name + '__' + str(i)) for i, v in enumerate(value)]
        if isinstance(value, np.generic):
            return value.item()
        return value
    payload = walk(data)
    np.savez_compressed(directory / 'raw.npz', **arrays)
    (directory / 'diagnostics.json').write_text(json.dumps(payload, indent=2, allow_nan=False), encoding='utf-8')
    return payload


def matrix_diagnostics(a, weights, beta):
    _, s, vh = svd(a, full_matrices=False, check_finite=False)
    rank = int(np.count_nonzero(s > s[0] * 1e-12)); row = vh[:rank]
    scaled = weights / weights.max()
    weighted = a / np.sqrt(scaled)[None, :]
    ws = svdvals(weighted, check_finite=False)
    gradient = scaled * beta
    projected = gradient - row.T @ (row @ gradient)
    return {
        'shape': list(a.shape), 'rank_cutoff_relative': 1e-12, 'rank': rank,
        'singular_values': s, 'condition_row_space': float(s[0] / s[-1]),
        'row_space_basis': row,
        'row_space_orthogonality_frobenius_error': float(np.linalg.norm(row @ row.T - np.eye(rank))),
        'projector_action_on_design_relative_error': float(np.linalg.norm(a - (a @ row.T) @ row) / np.linalg.norm(a)),
        'projected_weighted_gradient_relative_norm': float(np.linalg.norm(projected) / max(np.linalg.norm(gradient), 1e-300)),
        'weight_ratio': float(weights.max() / weights.min()),
        'weighted_singular_values': ws,
        'weighted_design_condition': float(ws[0] / ws[-1]),
        'weighted_design_rank_at_registered_cutoff': int(np.count_nonzero(ws > ws[0] * 1e-12)),
        'normal_equation_condition_lower_bound_from_design': float((ws[0] / ws[-1]) ** 2),
    }


def evaluate_trial(a, y, av, yv, degree, exponent):
    result = iterative_diagnostics(a, y, degree, exponent)
    beta = result['last_irls_coefficients']
    minimum_norm = result['final_ols']['diagnostic_minimum_norm_coefficients']
    result['initial_matrix'] = matrix_diagnostics(a, result['initial_weights'], result['iteration_coefficients'][0])
    result['last_matrix'] = matrix_diagnostics(a, result['last_weights'], beta)
    result['pre_final_ols_predictions'] = av @ beta
    result['diagnostic_post_ols_predictions'] = av @ minimum_norm
    result['pre_final_ols_validation'] = metrics(yv, av @ beta)
    result['diagnostic_post_ols_validation'] = metrics(yv, av @ minimum_norm)
    accepted = result['accepted_final_coefficients']
    result['accepted_validation'] = metrics(yv, av @ accepted) if accepted is not None else None
    result['passed_original_public_function_gate'] = bool(accepted is not None and
        result['accepted_validation']['rMSE'] <= .02 and
        result['accepted_validation']['mean_relative_error'] <= .02 and
        result['accepted_validation']['std_relative_error'] <= .02)
    return result


def run_d24(source, out, dependencies):
    old = source / 'methods/validation_02/D24'
    saved_validation = np.load(old / 'selection_validation_data.npz')
    models = {p: DSPCEModel.load(old / f'DSPCE_N200_p{p}_model') for p in [2, 3]}
    spec = {'kind': 'independent_uniform', 'low': [1.] * 20, 'high': [2.] * 20}
    bases = {1: CovarianceBasis(1).fit(None, moment_spec=spec), 2: models[2].basis_, 3: models[3].basis_}
    for name in ['selection_validation_data.npz', 'N200_fit_data.npz', 'N300_fit_data.npz']:
        dependencies.add(old / name)
    for n in [200, 300]:
        dependencies.update((old / f'DSPCE_N{n}_p3_model').glob('*'))
    dependencies.update((old / 'DSPCE_N200_p2_model').glob('*'))
    summary = {'historical_replays': [], 'public_learning_curve': [], 'constructed_sparse': [], 'hospital_permission': False}
    for n in [200, 300]:
        tr = np.load(old / f'N{n}_fit_data.npz'); x = tr['X']; y = tr['y']
        a = bases[3].transform(x); av = bases[3].transform(saved_validation['validation_X'])
        result = evaluate_trial(a, y, av, saved_validation['validation_y'], 3, 9)
        original = DSPCEModel.load(old / f'DSPCE_N{n}_p3_model')
        result.update({'X': x, 'y': y, 'A': a, 'validation_role': 'historical_seen_P2B_validation',
            'old_model_max_coefficient_difference': float(np.max(np.abs(result['final_ols']['diagnostic_minimum_norm_coefficients'] - original.coef_)))})
        write_bundle(out / f'historical_N{n}_p3', result)
        summary['historical_replays'].append({'N': n, 'status': result['status'],
            'old_coefficient_max_difference': result['old_model_max_coefficient_difference'],
            'pre_ols_rMSE': result['pre_final_ols_validation']['rMSE'], 'post_ols_rMSE': result['diagnostic_post_ols_validation']['rMSE'],
            'retained_terms': result['final_ols']['retained_terms'], 'retained_rank': result['final_ols']['retained_rank'],
            'initial_weighted_design_condition': result['initial_matrix']['weighted_design_condition'],
            'initial_constraint_relative_residual': result['iterations'][0]['constraint_relative_residual'],
            'last_relative_change': result['iterations'][-1]['relative_change']})
        print('D24 historical', summary['historical_replays'][-1], flush=True)
    vx = 1 + np.random.default_rng(2460).random((5000, 20)); vy = d24_function(vx)
    np.savez_compressed(out / 'new_independent_validation.npz', X=vx, y=vy, seed=np.array([2460]))
    va = {p: bases[p].transform(vx) for p in [1, 2, 3]}
    for seed in [2401, 2451, 2452]:
        full = 1 + qmc.Sobol(20, scramble=True, seed=seed).random_base2(9)
        for n in [200, 300, 500]:
            x = full[:n]; y = d24_function(x); a = bases[3].transform(x)
            start = time.perf_counter(); result = evaluate_trial(a, y, va[3], vy, 3, 3)
            baselines = []
            for p in [1, 2, 3]:
                ap = bases[p].transform(x); coef, _, rank, singular = lstsq(ap, y, cond=1e-12)
                identified = int(rank) == ap.shape[1]
                baselines.append({'degree': p, 'name': f'OLS_p{p}' if identified else f'minimum_norm_p{p}',
                    'full_column_rank': identified, 'rank': int(rank), 'basis_count': ap.shape[1],
                    'singular_values': singular, 'coefficients': coef, 'predictions': va[p] @ coef, 'validation': metrics(vy, va[p] @ coef)})
            result.update({'seed': seed, 'N': n, 'X': x, 'y': y, 'A': a, 'same_input_baselines': baselines, 'seconds': time.perf_counter() - start})
            write_bundle(out / f'kp_p3_N{n}_seed{seed}', result)
            summary['public_learning_curve'].append({'seed': seed, 'N': n, 'status': result['status'],
                'converged': result['converged'], 'iterations': len(result['iterations']) - 1,
                'last_relative_change': result['iterations'][-1]['relative_change'],
                'retained_terms': result['final_ols']['retained_terms'], 'retained_rank': result['final_ols']['retained_rank'],
                'pre_ols_rMSE': result['pre_final_ols_validation']['rMSE'], 'diagnostic_post_ols_rMSE': result['diagnostic_post_ols_validation']['rMSE'],
                'accepted_rMSE': result['accepted_validation']['rMSE'] if result['accepted_validation'] else None,
                'passed_original_gate': result['passed_original_public_function_gate'],
                'baselines': [{k: b[k] for k in ['degree', 'name', 'basis_count', 'rank']} | {'rMSE': b['validation']['rMSE']} for b in baselines]})
            print('D24 curve', summary['public_learning_curve'][-1], flush=True)
    sparse_spec = {'kind': 'independent_uniform', 'low': [-1.] * 8, 'high': [1.] * 8}
    sparse_bases = {p: CovarianceBasis(p).fit(None, moment_spec=sparse_spec) for p in [2, 3]}
    sxv = 2 * qmc.Sobol(8, scramble=True, seed=2473).random_base2(12) - 1
    fn = lambda x: 2 + 1.2 * x[:, 0] - .7 * x[:, 1] * x[:, 2] + .4 * x[:, 3] ** 3
    syv = fn(sxv); sav = sparse_bases[3].transform(sxv)
    np.savez_compressed(out / 'constructed_sparse_validation.npz', X=sxv, y=syv)
    for seed in [2471, 2472]:
        x = 2 * qmc.Sobol(8, scramble=True, seed=seed).random_base2(7)[:80] - 1; y = fn(x)
        result = evaluate_trial(sparse_bases[3].transform(x), y, sav, syv, 3, 3)
        result.update({'seed': seed, 'N': 80, 'X': x, 'y': y, 'constructed_formula': '2+1.2*x1-0.7*x2*x3+0.4*x4^3', 'same_input_baselines': []})
        for p in [2, 3]:
            a = sparse_bases[p].transform(x); coef, _, rank, _ = lstsq(a, y, cond=1e-12)
            pred = sparse_bases[p].transform(sxv) @ coef
            result['same_input_baselines'].append({'degree': p, 'name': f'OLS_p{p}' if rank == a.shape[1] else f'minimum_norm_p{p}', 'rank': int(rank), 'coefficients': coef, 'predictions': pred, 'validation': metrics(syv, pred)})
        rel = (np.linalg.norm(sav @ result['accepted_final_coefficients'] - syv) / np.linalg.norm(syv)) if result['accepted_final_coefficients'] is not None else None
        result['accepted_relative_RMSE'] = float(rel) if rel is not None else None
        result['passed_original_exact_polynomial_gate'] = bool(rel is not None and rel <= 1e-9)
        write_bundle(out / f'sparse_N80_seed{seed}', result)
        summary['constructed_sparse'].append({'seed': seed, 'status': result['status'], 'retained_terms': result['final_ols']['retained_terms'],
            'retained_rank': result['final_ols']['retained_rank'], 'accepted_relative_RMSE': result['accepted_relative_RMSE'],
            'passed_original_exact_polynomial_gate': result['passed_original_exact_polynomial_gate'],
            'pre_ols_rMSE': result['pre_final_ols_validation']['rMSE']})
        print('D24 sparse', summary['constructed_sparse'][-1], flush=True)
    summary['degree3_ready_for_hospital'] = False
    return summary


def run_d22(source, out, dependencies):
    old = source / 'methods/validation_02/D22'
    datafile = old / 'measure_selection_validation_data.npz'; data = np.load(datafile); dependencies.add(datafile)
    models = {}
    for p in [2, 3, 4, 5]:
        path = old / f'order_{p}_model'; models[p] = DNIPCModel.load(path); dependencies.update(path.glob('*'))
    inputs = {seed: d22_inputs(10000, seed) for seed in range(2251, 2259)}
    np.savez_compressed(out / 'fresh_iid_input_replicates.npz', **{f'seed{seed}': x for seed, x in inputs.items()})
    summary = []
    for p, model in models.items():
        historical = gram_diagnostics(model.basis_.transform(data['validation_X']))
        basis = gram_diagnostics(model.basis_.transform(data['basis_X']))
        replicas = []
        for seed, x in inputs.items():
            phi = model.basis_.transform(x)
            replicas.append({'seed': seed, 'gram': gram_diagnostics(phi), 'moments': moment_diagnostics(phi, model.coef_)})
        result = {'degree': p, 'old_basis_sample_count': 65536, 'old_basis_seed': 2201,
            'historical_iid_validation_seed': 2203, 'historical_gram': historical, 'empirical_basis_gram': basis,
            'historical_moments': moment_diagnostics(model.basis_.transform(data['validation_X']), model.coef_),
            'fresh_iid_replicates': replicas, 'basis_indices': model.basis_.indices_, 'basis_diagnostics': model.basis_.diagnostics_}
        worst = historical['worst_entry_indices']; result['worst_entry_original_affine_monomials'] = model.basis_.indices_[worst]
        write_bundle(out / f'order{p}', result)
        row = {'degree': p, 'basis_dimension': historical['basis_dimension'], 'historical_sample_count': 10000,
            'historical_max_entry': historical['max_absolute_entry_error'], 'historical_frobenius': historical['frobenius_error'],
            'historical_frobenius_per_P': historical['frobenius_per_basis_dimension'], 'historical_spectral': historical['spectral_error'],
            'historical_worst_entry': historical['worst_entry_indices'], 'historical_worst_entry_SE': historical['worst_entry_standard_error'],
            'historical_exceedance_count': historical['entries_exceeding_max_005_or_6se'],
            'replicate_max_entry_range': [float(min(r['gram']['max_absolute_entry_error'] for r in replicas)), float(max(r['gram']['max_absolute_entry_error'] for r in replicas))],
            'general_direct_moment_max_relative_difference': max(max(r['moments']['general_direct_mean_relative_difference'], r['moments']['general_direct_variance_relative_difference']) for r in replicas),
            'historical_moments': result['historical_moments']}
        summary.append(row); print('D22 gram', row, flush=True)
    return summary


def run_p_study(source, out, dependencies):
    old = source / 'learning/regression_attempt1'; reportfile = old / 'REGRESSION_REPORT.json'
    dependencies.add(reportfile); report = json.loads(reportfile.read_text(encoding='utf-8'))
    summaries = []; input_cache = {}
    for selected in report['selections']:
        config = selected.get('selected', {}).get('config_id', '')
        if not config.startswith(('DNIPC', 'DSPCE')):
            continue
        condition = selected['condition']; measure = selected['selected_seed_evidence'][0]['p_study_surrogate_prediction_statistics']['measure']
        key = hashlib.sha256(json.dumps(measure, sort_keys=True).encode()).hexdigest()[:12]
        if key not in input_cache:
            input_cache[key] = {seed: sample_measure(measure, 32768, seed) for seed in [3211, 3251, 3252, 3253]}
            np.savez_compressed(out / f'measure_{key}_inputs.npz', **{f'seed{seed}': x for seed, x in input_cache[key].items()})
        for seed in [3201, 3202, 3203]:
            path = old / condition / f'{config}__seed{seed}' / 'model/polynomial'
            cls = DNIPCModel if config.startswith('DNIPC') else DSPCEModel; model = cls.load(path)
            dependencies.update(path.glob('*'))
            records = []
            for sample_seed, x in input_cache[key].items():
                phi = model.basis_.transform(x)
                records.append({'sample_seed': sample_seed, 'moments': moment_diagnostics(phi, model.coef_), 'gram': gram_diagnostics(phi)})
            original_stats = next(e for e in selected['selected_seed_evidence'] if e['seed'] == seed)['p_study_surrogate_prediction_statistics']
            historical = records[0]['moments']
            result = {'condition': condition, 'configuration': config, 'fit_seed': seed, 'measure': measure,
                'input_cache': f'measure_{key}_inputs.npz', 'input_names': model.input_names, 'input_units': model.input_units,
                'scope': 'frozen surrogate moments only; no hospital truth, repair validation or real hospital probability',
                'records': records, 'original_same_sample_mean_difference': historical['direct_mean'] - original_stats['mean'],
                'original_same_sample_std_difference': float(np.sqrt(historical['direct_variance']) - original_stats['std'])}
            write_bundle(out / f'{condition}__{config}__seed{seed}', result)
            summaries.append({'condition': condition, 'configuration': config, 'fit_seed': seed, 'N': 32768,
                'basis_dimension': records[0]['gram']['basis_dimension'], 'original_same_sample_mean_difference': result['original_same_sample_mean_difference'],
                'original_same_sample_std_difference': result['original_same_sample_std_difference'], **historical,
                'replicate_direct_mean_range': [float(min(r['moments']['direct_mean'] for r in records[1:])), float(max(r['moments']['direct_mean'] for r in records[1:]))],
                'replicate_direct_variance_range': [float(min(r['moments']['direct_variance'] for r in records[1:])), float(max(r['moments']['direct_variance'] for r in records[1:]))]})
    print('p_study read-only selected polynomial models', len(summaries), flush=True)
    return summaries


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--source-p2b', default='outputs/v43_p2b_20261013_run01'); parser.add_argument('--out', required=True)
    args = parser.parse_args(); source = Path(args.source_p2b); out = Path(args.out)
    if out.exists():
        raise SystemExit('Refusing to overwrite existing mathematical evidence; use a new run directory')
    out.mkdir(parents=True); dependencies = set(); start = time.perf_counter()
    try:
        with threadpool_limits(limits=2):
            for name in ['D22', 'D24', 'p_study']:
                (out / name).mkdir()
            summary = {'algorithm_version': 'P2C_math_diagnostics_v1', 'D22': run_d22(source, out / 'D22', dependencies),
                'D24': run_d24(source, out / 'D24', dependencies), 'p_study': run_p_study(source, out / 'p_study', dependencies)}
        for pdf in ['D22_original.pdf', 'D24_original.pdf']:
            dependencies.add(source / 'methods/sources' / pdf)
        dependency_manifest = []
        for path in sorted(dependencies):
            relative = path.relative_to(source); destination = out / 'dependencies' / relative
            destination.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(path, destination)
            dependency_manifest.append({'original': str(path), 'copied_relative': str(destination.relative_to(out)), 'bytes': path.stat().st_size, 'sha256': digest(path), 'copied_sha256': digest(destination)})
        summary['dependency_manifest'] = dependency_manifest; summary['seconds'] = time.perf_counter() - start
        summary['no_hospital_simulation_or_fit_calls'] = True
        (out / 'summary.json').write_text(json.dumps(summary, indent=2, allow_nan=False), encoding='utf-8')
        manifest = [{'relative_path': str(p.relative_to(out)), 'bytes': p.stat().st_size, 'sha256': digest(p)} for p in sorted(out.rglob('*')) if p.is_file()]
        (out / 'artifact_manifest.json').write_text(json.dumps(manifest, indent=2, allow_nan=False), encoding='utf-8')
        print(json.dumps({'seconds': summary['seconds'], 'D24_variants': len(summary['D24']['public_learning_curve']),
            'D24_public_gate_passes': sum(r['passed_original_gate'] for r in summary['D24']['public_learning_curve']),
            'D24_degree3_ready_for_hospital': False, 'p_study_polynomial_models': len(summary['p_study']), 'artifacts': len(manifest)}, indent=2), flush=True)
    except Exception:
        (out / 'exception.txt').write_text(traceback.format_exc(), encoding='utf-8')
        raise


if __name__ == '__main__':
    main()
