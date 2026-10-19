"""Freeze P2B design and executable dependency manifest before any new run."""
import json
from pathlib import Path
import sys
from datetime import datetime, timezone
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from tools.p2b_experiment.design import build_design, write_design, SPLIT_VERSION
from tools.p2b_experiment.runner import sha, verify_manifest
from hospital_ies.dataset_manifest import build_manifest, digest
from hospital_ies.data_contract import SCHEMA_VERSIONS
from hospital_ies.execution import execution_contract


def freeze_experiment(out,root=ROOT):
    out=Path(out);root=Path(root)
    if out.exists():raise FileExistsError('Immutable experiment destination already exists')
    design=build_design(root,seed=20261014)
    schemas=dict(SCHEMA_VERSIONS,split=SPLIT_VERSION)
    contract=execution_contract(json.loads((root/'configs/p1b_execution_research.json').read_text()))
    m=build_manifest(root,design['scenarios'],design['policies'],contract,schemas,
        dict(seed=20261012,security_weight=.35,label_horizons=[15,30,60],
             history_minutes=60,score_start_minute=60,score_end_minute=420,observed_end_minute=480),
        design['assignments'])
    m.pop('signature')
    m.update(manifest_version='p2b.global_manifest.v1',p2b_design_hash=design['design_hash'],
        p2b_sampling_seed=20261014,registered_utc=datetime.now(timezone.utc).isoformat(),
        probability_semantics='Explicit hypothetical p_study and separate q_design; no real p_reference',
        p2b_study_distribution=design['study_distribution'],p2b_counts=design['counts'],
        p2b_time_blocks=design['time_blocks'],p2b_support=design['support_rows'],
        p2b_method_scope='Offline only; no controller or architecture changes',
        p2b_shards=[dict(id=f'shard_{i:02d}',scenario_ids=[s['id'] for s in design['scenarios'][i*50:(i+1)*50]]) for i in range(8)],
        p2b_dependencies=[dict(path=rel,sha256=sha((root/rel).read_bytes())) for rel in
            ('tools/p2b_experiment/design.py','tools/p2b_experiment/runner.py','tools/p2b_experiment/prepare.py',
             'P2B_EXPERIMENT_PLAN.md','P2B_CODEX_TASK.md','P2B_METHOD_CHECKS.md','MODEL_SCOPE.md')])
    m['signature']=digest(m)
    verify_manifest(m)
    out.mkdir(parents=True)
    write_design(design,out/'design')
    (out/'global_manifest.json').write_text(json.dumps(m,ensure_ascii=False,indent=2),encoding='utf-8')
    return m


if __name__=='__main__':
    m=freeze_experiment(Path(sys.argv[1]))
    print(json.dumps(dict(signature=m['signature'],counts=m['p2b_counts'],registered_utc=m['registered_utc'])))
