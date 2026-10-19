"""Complete publication closure without changing the frozen scientific source."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
spec = importlib.util.spec_from_file_location('v20_frozen_repository', ROOT / 'scripts/v20_repository.py')
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)
DESIGN = 'outputs/v19_model_closeout_20261018_run01/design/'
SUPPLEMENTAL = (
    'LONG_FINAL_R1_PROPOSAL.json', 'COMPAT_FINAL_R1_PROPOSAL.json',
    'COMPAT_LONG_FINAL_R1_PROPOSAL.json', 'PROPAGATION_FINAL_R1_PROPOSAL.json',
    'NORMAL_E_FINAL_R1_PROPOSAL.json', 'SENSITIVITY_OPEN1_R1_PROPOSAL.json',
    'SENSITIVITY_CONTACT025_R1_PROPOSAL.json',
)
REFERENCE_IGNORES = (
    '/inputs/reference/NMCSD_Brown_Dirks.pdf',
    '/inputs/reference/NMCSD_tables.xlsx',
    '/inputs/reference/PNNL_Prototype_Scorecards_2019.xlsx',
)
FILE_LIMIT = 100 * 1024**2
COMPACT_BATCH_CSV = frozenset((
    'V20_RESOURCES.csv', 'V20_EMISSIONS.csv', 'V18_CARRIER_SERVICE.csv',
    'V18_ECONOMICS.csv', 'GT_RUNS.csv', 'GT_TRANSITIONS.csv',
    'MODEL_DOMAIN.csv', 'THERMAL_COVERAGE.csv',
))


def supplemental_rows():
    """Pin the seven historical test fixtures to the preserved starting receipt."""
    receipt = base.read(base.RUN / 'provenance/STARTING_POINT.json')
    pinned = {row['repository_path']: row for row in receipt['source_files']}
    rows = []
    for name in SUPPLEMENTAL:
        evidence = base.RUN_REL + '/provenance/v19_design/' + name
        path = DESIGN + name
        row = pinned[evidence]
        source = base.safe(ROOT, path)
        if not source.is_file() or source.stat().st_size != row['bytes'] or base.sha(source) != row['sha256']:
            raise ValueError('Historical fixture differs from original receipt: ' + path)
        if base.sha(base.safe(ROOT, evidence)) != row['sha256']:
            raise ValueError('Historical fixture evidence differs: ' + evidence)
        rows.append(dict(path=path, bytes=row['bytes'], sha256=row['sha256'], evidence=evidence,
                         purpose='Original-path fixture required by default V20 campaign regression'))
    return rows


def select(candidate=False):
    rows = supplemental_rows()
    names = set(base.selected_paths())
    # The frozen base selector prunes all experiment CSVs. Restore only these
    # compact accounting/event summaries; full interval exports remain local.
    names.update(p.relative_to(ROOT).as_posix()
                 for p in (base.RUN / 'experiments').rglob('*.csv')
                 if p.name in COMPACT_BATCH_CSV)
    if candidate:
        # A live campaign can append journals. Candidate QA copies the completed
        # reference batch and static metadata, never an in-flight batch/log.
        prefix = base.RUN_REL + '/'
        names = {n for n in names if not n.startswith(prefix + 'orchestration/')
                 and not (n.startswith(prefix + 'experiments/') and
                          not n.startswith(prefix + 'experiments/v20_main_000/'))
                 and not (n.startswith(prefix + 'logs/') and
                          not any(n.startswith(prefix + 'logs/' + x) for x in ('core_gate', 'pilot01')))}
    names.update(row['path'] for row in rows)
    names.add('inputs/energyplus/README_setup.txt')
    names.add('publication/requirements.txt')
    names.update(p.relative_to(ROOT).as_posix() for p in (ROOT / 'publication').glob('*.py'))
    for name in sorted(names):
        path = base.safe(ROOT, name)
        if not path.is_file() or path.is_symlink():
            raise ValueError('Missing or linked publication file: ' + name)
        if path.stat().st_size >= FILE_LIMIT:
            raise ValueError('GitHub per-file limit exceeded: ' + name)
        if path.suffix.lower() in ('.zip', '.gz', '.7z', '.sql', '.sqlite', '.lic'):
            raise ValueError('Excluded archive/raw/license in release: ' + name)
    return sorted(names), rows


def build(destination, candidate=False):
    if not Path(destination).is_absolute():
        raise ValueError('Use a new absolute publication destination')
    target = Path(destination).resolve()
    qualification = None
    if not candidate:
        from publication.release_gate import qualify_final_campaign
        qualification = qualify_final_campaign(ROOT)
    names, supplemental = select(candidate)
    if qualification is not None:
        missing = {row['path'] for row in qualification['evidence_files']} - set(names)
        if missing:
            raise ValueError('Qualified evidence missing from release selection: ' + repr(sorted(missing)))
    original_selector = base.selected_paths
    try:
        base.selected_paths = lambda: names
        base.stage(str(target))
    finally:
        base.selected_paths = original_selector
    if qualification is not None:
        evidence_errors = base.verify_rows(target, qualification['evidence_files'])
        if evidence_errors:
            raise ValueError('Copied evidence differs from final qualification: ' + repr(evidence_errors))
    with (target / '.gitignore').open('a', encoding='utf-8', newline='\n') as out:
        out.write('\n# Third-party source originals are not dispatch dependencies.\n')
        out.write('\n'.join(REFERENCE_IGNORES) + '\n')
    campaign_qualification = dict(status='not_required_for_candidate')
    if qualification is not None:
        proof_name = 'publication/V20_FINAL_CAMPAIGN_PROOF.json'
        proof_path = target / proof_name
        base.write_new(proof_path, qualification)
        campaign_qualification = dict(status='passed', path=proof_name,
            sha256=base.sha(proof_path), signature=qualification['signature'],
            gate_sha256=qualification['gate_sha256'], actual_complete_batches=148,
            actual_complete_pairs=406)
    marker = dict(schema='hospital_ies.v20.release_root.v1',
                  utc=datetime.now(timezone.utc).isoformat(),
                  source_root=str(ROOT), original_scientific_root=str(ROOT),
                  original_research_root=base.read(base.RUN / 'provenance/STARTING_POINT.json')['source_root'],
                  staged_root=str(target), status='candidate' if candidate else 'final',
                  freeze_sha256=base.sha(base.RUN / base.FREEZE),
                  wrapper_sha256=base.sha(Path(__file__)),
                  reproduction_wrapper_sha256=base.sha(ROOT / 'publication/reproduce_v20.py'),
                  campaign_qualification=campaign_qualification,
                  supplemental_closure=supplemental,
                  candidate_omits_inflight_results=candidate,
                  scientific_source_modified=False, per_file_limit_bytes_exclusive=FILE_LIMIT)
    base.write_new(target / 'publication/V20_RELEASE_ROOT.json', marker)
    # The manifest excludes itself to avoid a recursive hash; includes generated
    # marker/gitignore so clone verification checks their actual published bytes.
    selection = target / 'publication/V20_FILE_SELECTION.json'
    inventory = []
    for path in sorted(target.rglob('*')):
        if path.is_file() and path != selection:
            inventory.append(dict(path=path.relative_to(target).as_posix(), bytes=path.stat().st_size,
                                  sha256=base.sha(path)))
    value = dict(schema='hospital_ies.v20.repository_selection.v2', files=inventory,
                 selected_bytes=sum(x['bytes'] for x in inventory),
                 raw_trajectories_included=False, original_workspace_modified=False,
                 selection_self_hash_excluded=True, publication_marker_included=True,
                 candidate=candidate, supplemental_fixture_count=len(supplemental))
    selection.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    errors = base.verify_rows(target, inventory)
    if errors:
        raise ValueError('Release copy verification failed: ' + repr(errors))
    print(json.dumps(dict(status='staged', destination=str(target), files=len(inventory),
                          selected_bytes=value['selected_bytes'], candidate=candidate)))
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination', required=True)
    parser.add_argument('--candidate', action='store_true', help='QA snapshot; omit in-flight campaign records')
    args = parser.parse_args()
    return build(args.destination, args.candidate)


if __name__ == '__main__':
    raise SystemExit(main())
