"""Recompute the native evidence from saved arrays without solving again."""
from __future__ import annotations
import argparse
from collections import Counter
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import numpy as np
from scipy import sparse

ROOT = Path(__file__).resolve().parents[2]


def matrix(z, prefix='matrix'):
    return sparse.csr_matrix((z[prefix+'_data'], z[prefix+'_indices'], z[prefix+'_indptr']),
                             shape=tuple(z[prefix+'_shape']))


def violation(a, lo, hi, blo, bhi, integer, x, rows):
    y = np.asarray(a @ x).ravel()
    residual = np.maximum(0., np.maximum(lo-y, y-hi))
    locks = np.array([r['kind'] == 'priority_lock' for r in rows])
    bound = np.maximum(0., np.maximum(blo-x, x-bhi)).max(initial=0.)
    return (float(max(residual[~locks].max(initial=0.), bound)),
            float(residual[locks].max(initial=0.)),
            float(np.abs(x[integer != 0]-np.rint(x[integer != 0])).max(initial=0.)))


def review(summary_path, out):
    summary = json.loads(Path(summary_path).read_text(encoding='utf-8'))
    seen = set(); counts = Counter(); records = []; worst = np.zeros(3); proofs = 0
    for case in summary['records']:
        key = (case['group'], case['case_id'])
        assert key not in seen
        seen.add(key)
        folder = ROOT / case['raw_layers']
        previous = None
        for rank in range(5):
            npz = folder / f'layer_{rank:02d}.npz'
            metadata = json.loads(npz.with_suffix('.json').read_text(encoding='utf-8'))
            assert hashlib.sha256(npz.read_bytes()).hexdigest() == metadata['npz_sha256']
            with np.load(npz, allow_pickle=False) as z:
                a = matrix(z); sa = matrix(z, 'solver_matrix'); x = z['x_raw']
                assert metadata['rank'] == rank and metadata['result']['status'] == 0
                assert x.shape == z['objective'].shape == z['bounds_lower'].shape
                v = violation(a, z['lower'], z['upper'], z['bounds_lower'], z['bounds_upper'],
                              z['integrality'], x, metadata['rows'])
                assert v[0] <= 1e-7 and v[1] <= 1e-10 and v[2] <= 1e-9
                sv = violation(sa, z['solver_lower'], z['solver_upper'], z['solver_bounds_lower'],
                               z['solver_bounds_upper'], z['integrality'], x, metadata['rows'])
                assert sv[0] <= 1e-7 and sv[1] <= 1e-10 and sv[2] <= 1e-9
                scales = z['row_scales']
                assert np.all(scales > 0.)
                assert np.array_equal((sparse.diags(scales) @ a).toarray(), sa.toarray())
                assert np.array_equal(z['lower']*scales, z['solver_lower'])
                assert np.array_equal(z['upper']*scales, z['solver_upper'])
                assert np.array_equal(z['bounds_lower'], z['solver_bounds_lower'])
                if rank == 0:
                    with np.load(ROOT / case['old_base_reference'], allow_pickle=False) as old:
                        for field in ('lower', 'upper', 'bounds_lower', 'bounds_upper', 'integrality', 'objective'):
                            assert np.array_equal(z[field], old[field]), (key, field)
                        assert np.array_equal(a.toarray(), matrix(old).toarray())
                else:
                    assert np.array_equal(previous, z['previous_x'])
                    pv = violation(a, z['lower'], z['upper'], z['bounds_lower'], z['bounds_upper'],
                                   z['integrality'], previous, metadata['rows'])
                    assert pv[0] <= 1e-7 and pv[1] <= 1e-10 and pv[2] <= 1e-9
                raw_objective = float(z['objective'] @ x)
                assert abs(raw_objective-metadata['result']['raw_objective']) <= 1e-12
                assert abs(raw_objective-metadata['result']['res_fun']) <= 1e-9*max(1., abs(raw_objective))
                assert metadata['result']['mip_gap'] <= 1e-6
                for p in metadata['entailed_bound_proofs']:
                    lock = next(k for k in metadata['locks'] if k['source_rank'] == p['source_rank'])
                    c = np.asarray(lock['objective']); j = p['index']; nz = np.flatnonzero(c)
                    minimum = sum((Fraction.from_float(float(c[k]))*Fraction.from_float(float(z['bounds_lower'][k]))
                                   for k in nz if k != j), Fraction())
                    exact = (Fraction.from_float(float(lock['upper']))-minimum)/Fraction.from_float(float(c[j]))
                    assert exact == Fraction(int(p['exact_derived_numerator']), int(p['exact_derived_denominator']))
                    assert Fraction.from_float(float(p['derived_upper'])) >= exact
                    proofs += 1
                previous = x.copy(); worst = np.maximum(worst, v)
                counts[case['group']] += 1
                records.append({'group': case['group'], 'case_id': case['case_id'], 'rank': rank,
                                'max_original_violation': v[0], 'max_lock_violation': v[1],
                                'max_integer_distance': v[2], 'raw_objective': raw_objective,
                                'mip_dual_bound': metadata['result']['mip_dual_bound'],
                                'npz_sha256': metadata['npz_sha256']})
    result = {'passed': True, 'review_type': 'separate Python process; raw array recomputation, no new solve',
              'unique_cases': len(seen), 'actual_layers': len(records), 'groups_layers': dict(counts),
              'max_original_violation': float(worst[0]), 'max_lock_violation': float(worst[1]),
              'max_integer_distance': float(worst[2]), 'exact_fraction_proofs': proofs, 'records': records}
    Path(out).write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in result.items() if k != 'records'}))
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('summary'); p.add_argument('out'); a = p.parse_args()
    review(a.summary, a.out)
