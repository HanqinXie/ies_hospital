"""Fresh-process read-only checks of P2C numerical artifacts and dependencies."""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import numpy as np
import scipy
from threadpoolctl import threadpool_limits
from tools.p2b_methods.models import DNIPCModel, DSPCEModel


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_bundle(directory):
    directory = Path(directory)
    raw = np.load(directory / 'raw.npz', allow_pickle=False)
    def decode(value):
        if isinstance(value, dict) and set(value) == {'array', 'file', 'shape', 'dtype'}:
            array = raw[value['array']]
            assert list(array.shape) == value['shape'] and str(array.dtype) == value['dtype']
            return array
        if isinstance(value, dict):
            return {k: decode(v) for k, v in value.items()}
        if isinstance(value, list):
            return [decode(v) for v in value]
        return value
    return decode(json.loads((directory / 'diagnostics.json').read_text(encoding='utf-8')))


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--root', required=True); parser.add_argument('--out', required=True)
    args = parser.parse_args(); root = Path(args.root); output = Path(args.out)
    if output.exists():
        raise SystemExit('Refusing to overwrite prior verification')
    manifest = json.loads((root / 'artifact_manifest.json').read_text(encoding='utf-8'))
    for row in manifest:
        path = root / row['relative_path']
        assert path.stat().st_size == row['bytes'] and digest(path) == row['sha256'], path
    summary = json.loads((root / 'summary.json').read_text(encoding='utf-8'))
    for row in summary['dependency_manifest']:
        assert digest(root / row['copied_relative']) == row['sha256'] == row['copied_sha256']
        assert digest(row['original']) == row['sha256'], row['original']
    max_moment_diff = 0.; max_prediction_diff = 0.; statuses = {}; checked_steps = 0
    with threadpool_limits(2):
        for directory in sorted((root / 'D24').iterdir()):
            if not directory.is_dir():
                continue
            trial = load_bundle(directory)
            coefficients = trial['iteration_coefficients']; y = trial['y']
            if 'A' in trial:
                a = trial['A']
                for step, beta in zip(trial['iterations'], coefficients):
                    residual = float(np.linalg.norm(a @ beta - y) / max(1., np.linalg.norm(y)))
                    assert abs(residual - step['constraint_relative_residual']) < 1e-12, (directory, step)
                    checked_steps += 1
                keep = trial['final_ols']['retained_indices']
                rank = np.linalg.matrix_rank(a[:, keep], tol=1e-12 * np.linalg.svd(a[:, keep], compute_uv=False)[0])
                assert rank == trial['final_ols']['retained_rank']
            if directory.name.startswith('kp_'):
                model = DSPCEModel.load(root / 'dependencies/methods/validation_02/D24/DSPCE_N200_p3_model')
                data = np.load(root / 'D24/new_independent_validation.npz'); av = model.basis_.transform(data['X'])
            elif directory.name.startswith('historical'):
                model = DSPCEModel.load(root / 'dependencies/methods/validation_02/D24/DSPCE_N200_p3_model')
                data = np.load(root / 'dependencies/methods/validation_02/D24/selection_validation_data.npz')
                av = model.basis_.transform(data['validation_X'])
            else:
                from tools.p2b_methods.basis import CovarianceBasis
                basis = CovarianceBasis(3).fit(None, moment_spec={'kind': 'independent_uniform', 'low': [-1.] * 8, 'high': [1.] * 8})
                data = np.load(root / 'D24/constructed_sparse_validation.npz'); av = basis.transform(data['X'])
            for beta, key in [(coefficients[-1], 'pre_final_ols_predictions'), (trial['final_ols']['diagnostic_minimum_norm_coefficients'], 'diagnostic_post_ols_predictions')]:
                max_prediction_diff = max(max_prediction_diff, float(np.max(np.abs(av @ beta - trial[key]))))
            eligible = trial['converged'] and trial['final_ols']['eligible_final_ols']
            assert (trial['accepted_final_coefficients'] is not None) == eligible
            if trial['accepted_final_coefficients'] is not None:
                assert trial['iterations'][-1]['relative_change'] <= 1e-6
                assert trial['iterations'][0]['constraint_relative_residual'] <= 1e-8
            if directory.name.startswith('historical'):
                n = 200 if 'N200' in directory.name else 300
                old = DSPCEModel.load(root / f'dependencies/methods/validation_02/D24/DSPCE_N{n}_p3_model')
                assert np.max(np.abs(trial['final_ols']['diagnostic_minimum_norm_coefficients'] - old.coef_)) == 0.
            statuses[trial['status']] = statuses.get(trial['status'], 0) + 1
        for row in summary['p_study']:
            directory = root / 'p_study' / f"{row['condition']}__{row['configuration']}__seed{row['fit_seed']}"
            result = load_bundle(directory)
            model = DNIPCModel.load(root / 'dependencies/learning/regression_attempt1' / row['condition'] / f"{row['configuration']}__seed{row['fit_seed']}" / 'model/polynomial')
            inputs = np.load(root / 'p_study' / result['input_cache'])
            for record in result['records']:
                x = inputs[f"seed{record['sample_seed']}"]
                predicted = model.predict(x)
                phi = model.basis_.transform(x); mu = phi.mean(axis=0); G = phi.T @ phi / len(phi)
                mean = float(mu @ model.coef_); variance = float(model.coef_ @ (G - np.outer(mu, mu)) @ model.coef_)
                observed = record['moments']
                assert abs(predicted.mean() - observed['direct_mean']) <= 1e-10 * max(1., abs(observed['direct_mean']))
                assert abs(predicted.var() - observed['direct_variance']) <= 1e-10 * max(1., abs(observed['direct_variance']))
                max_moment_diff = max(max_moment_diff, abs(mean - predicted.mean()) / max(1., abs(predicted.mean())), abs(variance - predicted.var()) / max(1., abs(predicted.var())))
    assert max_prediction_diff <= 1e-12
    assert max_moment_diff <= 1e-10
    runtime = {'python': platform.python_version(), 'numpy': np.__version__, 'scipy': scipy.__version__, 'blas_threads': 2,
        'source_manifest_capture': 'after actual numerical run; source association, not preregistration timestamp',
        'source_sha256': {str(p): digest(p) for p in sorted(Path('tools/p2c_methods').glob('*.py'))}}
    output.write_text(json.dumps({'status': 'PASS', 'artifact_hashes_checked': len(manifest),
        'old_and_copied_dependencies_checked': len(summary['dependency_manifest']), 'D24_statuses': statuses,
        'iteration_residual_checks': checked_steps, 'prediction_max_absolute_reload_difference': max_prediction_diff,
        'same_measure_moment_max_relative_difference': max_moment_diff, 'selected_p_study_models_checked': len(summary['p_study']),
        'runtime': runtime}, indent=2, allow_nan=False), encoding='utf-8')
    print(output.read_text(encoding='utf-8'), flush=True)


if __name__ == '__main__':
    main()
