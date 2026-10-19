from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def test_prepare_has_single_global_partition_seeds_and_roles(tmp_path):
    from tools.p2b_experiment.prepare import freeze_experiment
    from tools.p2b_experiment.runner import verify_manifest
    m=freeze_experiment(tmp_path/'experiment',root=ROOT)
    assert len(m['scenarios'])==400
    assert m['options']['seed']==20261012
    assert m['p2b_sampling_seed']==20261014
    assert len(m['p2b_shards'])==8
    assert all(len(s['scenario_ids'])==50 for s in m['p2b_shards'])
    assert sum(a['fit_allowed'] for a in m['splits'].values())==240
    assert m['schemas']['label']=='p2a.first_observed_failure.v1'
    verify_manifest(m)
