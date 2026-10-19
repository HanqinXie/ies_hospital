"""One short-lived, strictly gated saved-result collection; no physics or wait.

Only the standard library is imported before all completion records pass.
The prepared new03 collector and its guards remain byte-for-byte unchanged.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import traceback

ROOT = Path(__file__).resolve().parents[4]
RUN_REL = 'outputs/v20_load_accounting_20261019_run01'
HELPER_SHA = '526834c9b51418a0b09b8922045439c095fa9de1fb4e2be4be147d5cc0babe81'
OLD_PREFIX = '../../outputs/v20_load_accounting_20261019_run01/review/normal_window_analysis01/'
NEW_PREFIX = '../../outputs/v20_load_accounting_20261019_run01/review/normal_window_analysis03/'


def require(value, message):
    if not value:
        raise ValueError(message)


def now():
    return datetime.now(timezone.utc).isoformat()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(part)
    return digest.hexdigest()


def write_new(path, value):
    with Path(path).open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def fingerprint(root, path):
    path = Path(path).resolve(strict=True)
    require(path.is_relative_to(root), 'Evidence path escaped current scientific root')
    return dict(path=path.relative_to(root).as_posix(), bytes=path.stat().st_size, sha256=sha(path))


def validate_ready_records(s3, normal, report, summary):
    require(s3.get('status') == 'passed' and s3.get('strict_batches') == 148
            and s3.get('source_modified') is False and s3.get('continuation_of') == 'supervision02'
            and s3.get('normal_capture_name') == 'normal03' and s3.get('normal_supervision_name') == 'canonical02'
            and s3.get('previous_helper_error_preserved') is True
            and s3.get('existing_normal_009_strictly_skipped') is True
            and s3.get('normal_009_resource_peak_unknown') is True, 'S3 successful continuation evidence differs')
    cp = s3.get('checkpoint') or {}
    require(cp.get('registered') == cp.get('actual_started') == cp.get('actual_finished') == 406
            and cp.get('active') == 0 and cp.get('not_started') == 0
            and cp.get('failed_terminal_journals') == [], 'S3 actual 406 terminal denominator differs')
    require(normal.get('name') == 'normal03' and normal.get('exit_code') == 0,
            'Actual normal03 did not succeed')
    require(report.get('name') == 'final_readback01' and report.get('exit_code') == 0,
            'Actual FINAL01 report command did not succeed')
    require(summary.get('expected_pairs') == summary.get('actual_started_pairs') == summary.get('actual_finished_pairs')
            == summary.get('complete_registered_full_pairs') == summary.get('eligible_terminal_pairs') == 406
            and summary.get('unknown_full_pairs') == 0 and summary.get('status_counts') == {'completed': 406},
            'FINAL01 complete eligible canonical denominator differs')


def validate_stop_records(stop, capture):
    require(stop.get('status') == 'owned_readonly_waiter_stopped_preserve_actual_exit'
            and stop.get('actual_terminated_pid') == 82524 and stop.get('observed_OS_process_exit_code') == 15
            and capture.get('name') == 'normal_analysis_wait02' and capture.get('exit_code') == 15
            and stop.get('capture_record') == capture, 'Original waiter actual exit15 must remain unchanged')
    identity = stop.get('identity_rechecked') or {}
    require(identity.get('pid') == 82524 and identity.get('children') == []
            and stop.get('scientific_execution_failed') is False and stop.get('numeric_analysis_generated') is False
            and stop.get('READONLY_OWNER_preserved') is True
            and stop.get('new_oneshot_collection_required_after_S3') is True,
            'Original non-scientific stop provenance differs')


def validate_successor_ready_records(successor, proof, capture, report, summary):
    require(successor.get('status') == 'passed' and successor.get('exit_code') == 0
            and successor.get('strict_batches') == 148 and successor.get('qualified_pairs') == 406
            and successor.get('no_frozen_science_modified') is True and successor.get('old_STOP_preserved') is True
            and successor.get('normal009_resource_peak_unknown') is True
            and successor.get('old_measured_resource_batches') == 10 and successor.get('new_measured_resource_batches') == 1
            and successor.get('actual_scientific_children_started') == 1, 'True RUN02 successor/resource partition differs')
    child = successor.get('last_child') or {}
    require(child.get('batch') == 'v20_normal_008' and type(child.get('pid')) is int and child['pid'] > 0
            and child.get('successful_Popen') is True and child.get('owned_child_drained') is True and child.get('exit_code') == 0,
            'True RUN02 actual008 child differs')
    require(proof.get('schema') == 'hospital_ies.v20.publication_final_campaign_qualification.v1'
            and proof.get('status') == 'passed' and proof.get('model_id') == 'M20_load_accounting'
            and proof.get('actual_complete_batches') == 148
            and proof.get('actual_complete_pairs') == proof.get('actual_started_attempts') == proof.get('actual_finished_attempts') == 406
            and proof.get('failed_or_unknown_canonical_pairs') == 0
            and proof.get('exact_registered_duration_and_service_ledger') is True and proof.get('actual_reset_matching') is True
            and proof.get('actual_applied_streams_recomputed') is True
            and proof.get('raw_files_opened') == proof.get('annual_values_parsed') == proof.get('physical_episodes_started') == 0,
            'True successor strict148/406 metadata qualification differs')
    require(capture.get('name') == 'normal008_lightweight_run02' and capture.get('exit_code') == 0
            and report.get('name') == 'final_readback01' and report.get('exit_code') == 0, 'Actual successor/report capture did not succeed')
    require(summary.get('expected_pairs') == summary.get('actual_started_pairs') == summary.get('actual_finished_pairs')
            == summary.get('complete_registered_full_pairs') == summary.get('eligible_terminal_pairs') == 406
            and summary.get('unknown_full_pairs') == 0 and summary.get('status_counts') == {'completed':406},
            'FINAL01 exact qualified406 denominator differs')


def readiness(root):
    """Standard library only, true RUN02 success first; no wait or numeric import."""
    root=Path(root).resolve(strict=True);run=root/RUN_REL;out=run/'review/normal_window_analysis03'
    successor=run/'review/normal008_continuation01/L72_RUN02'
    files=[successor/'SUMMARY.json',successor/'FINAL_406_PROOF.json',run/'logs/normal008_lightweight_run02.command.json',
           run/'logs/final_readback01.command.json',run/'paper_results/FINAL01/SUMMARY.json',run/'paper_results/FINAL01/RECEIPT.json',
           run/'paper_results/FINAL01/INPUT_INDEX.json',out/'SUCCESSOR_CONTRACT.json',out/'PREPARATION.json',
           run/'review/normal_wait02_stop01/RESULT.json',run/'logs/normal_analysis_wait02.command.json',
           run/'review/normal008_continuation01/L72_RUN01/IDLE_STOP_OUTCOME.json',run/'logs/normal008_lightweight_run01.command.json']
    missing=[p.relative_to(root).as_posix() for p in files if not p.is_file()]
    if missing:return dict(ready=False,status='not_ready_no_helper_import',missing=missing,project_helper_imported=False)
    s4,proof,outer,report,summary=[read(p) for p in files[:5]]
    validate_successor_ready_records(s4,proof,outer,report,summary)
    validate_stop_records(read(files[9]),read(files[10]))
    idle,oldcapture=read(files[11]),read(files[12])
    require(idle.get('status')=='owned_idle_monitor_stopped_no_science' and idle.get('actual_wait_exit_code')==15
            and idle.get('scientific_episodes_started')==0 and idle.get('no_008_attempt_after_stop') is True
            and oldcapture.get('name')=='normal008_lightweight_run01' and oldcapture.get('exit_code')==15,
            'Aborted RUN01 must remain actual exit15 with no scientific attempt')
    for record in (outer,report):require(Path(record.get('cwd','')).resolve()==root,'Actual successor/report working root differs')
    contract,prepared=read(files[7]),read(files[8]);helper=out/'normal_window_analysis.py'
    require(prepared.get('script_sha256')==sha(helper)==HELPER_SHA,'Prepared new03 helper source differs')
    require(prepared.get('successor_contract_sha256')==sha(files[7]) and contract.get('status')=='bound_prepared'
            and contract.get('successor')=='normal008_continuation01/L72_RUN02'
            and contract.get('outer_capture_name')=='normal008_lightweight_run02'
            and s4.get('script_sha256')==contract.get('successor_helper_sha256')==sha(root/'publication/continue_normal008.py'),
            'Bound true successor implementation/contract differs')
    for name,filename in [('final_406_proof','FINAL_406_PROOF.json'),('resource_result','v20_normal_008.result.json')]:
        row=s4[name];path=(root/row['path']).resolve(strict=True)
        require(path==(successor/filename).resolve() and path.stat().st_size==row['bytes'] and sha(path)==row['sha256'],
                'Closed successor artifact SHA/path differs')
    frozen=read(run/'MODEL_FREEZE_ACCOUNTING01.json');receipt=read(files[5])
    require(receipt.get('script_sha256')==frozen['source_sha256']['scripts/v20_report.py']==sha(root/'scripts/v20_report.py')
            and receipt.get('raw_opened') is False and receipt.get('annual_values_read') is False,'Frozen FINAL01 report source/scope differs')
    for name in ('SUMMARY.json','INPUT_INDEX.json'):
        require(receipt.get('output_files',{}).get(name)==sha(run/'paper_results/FINAL01'/name),'FINAL01 output receipt SHA differs')
    files.extend([helper,root/'publication/continue_normal008.py',run/'MODEL_FREEZE_ACCOUNTING01.json'])
    return dict(ready=True,status='ready_true_successor_validation',project_helper_imported=False,helper_sha256=HELPER_SHA,
        original_waiter_exit_code=15,normal03_intentional_exit_code=125,idleRUN01_exit_code=15,
        evidence_files=[fingerprint(root,p) for p in files])


def _load_helper(path):
    spec = importlib.util.spec_from_file_location('_normal_analysis02_actual_oneshot', path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def correct_document_links(root, out, doc, receipt_path):
    """Preserve the generated document and original receipt, replace links only."""
    root, out, doc, receipt_path = map(Path, (root, out, doc, receipt_path))
    receipt = read(receipt_path)
    rows = receipt['outputs']
    relative = doc.relative_to(root).as_posix()
    matches = [r for r in rows if r['path'] == relative]
    require(len(matches) == 1 and matches[0]['sha256'] == sha(doc), 'As-generated document does not match original receipt')
    original = doc.read_bytes()
    old, new = OLD_PREFIX.encode('ascii'), NEW_PREFIX.encode('ascii')
    require(original.count(old) == 8 and original.count(new) == 0, 'Expected exactly eight old document links')
    unchanged = [dict(r) for r in rows if r['path'] != relative]
    for row in unchanged:
        require(sha(root / row['path']) == row['sha256'], 'A numerical output changed before link correction')
    backup = out / 'NORMAL_WINDOW_ANALYSIS_AS_GENERATED01.md'
    with backup.open('xb') as stream:
        stream.write(original)
    corrected = original.replace(old, new)
    temporary = out / 'NORMAL_WINDOW_ANALYSIS_CORRECTED01.tmp'
    with temporary.open('xb') as stream:
        stream.write(corrected)
    os.replace(temporary, doc)
    require(doc.read_bytes() == corrected and sha(backup) == matches[0]['sha256'], 'Document correction byte verification failed')
    for row in unchanged:
        require(sha(root / row['path']) == row['sha256'], 'A numerical output changed during link correction')
    proof = dict(schema='hospital_ies.v20.normal_document_link_correction.v1', status='passed', utc=now(),
        original_collection_receipt=fingerprint(root, receipt_path), original_collection_receipt_not_modified=True,
        original_document=fingerprint(root, backup), published_document=fingerprint(root, doc),
        original_document_hash_in_collection_receipt=matches[0]['sha256'],
        supersedes_only_document_output_hash=True, correction_does_not_revalidate_or_change_numbers=True,
        replacements=8, old_prefix=OLD_PREFIX, new_prefix=NEW_PREFIX, only_link_prefix_changed=True,
        numeric_tables_unchanged=True, unchanged_output_files=unchanged,
        provenance='The prepared ff04 renderer used the previous analysis01 link prefix; all tables were written in analysis02.')
    write_new(out / 'DOCUMENT_LINK_CORRECTION01.json', proof)
    return proof


def verify_document_links(root, out, doc, receipt_path):
    root,out,doc,receipt_path=map(Path,(root,out,doc,receipt_path));receipt=read(receipt_path)
    relative=doc.relative_to(root).as_posix();rows=[r for r in receipt['outputs'] if r['path']==relative]
    require(len(rows)==1 and rows[0]['sha256']==sha(doc),'New03 generated document not bound by collection receipt')
    text=doc.read_text(encoding='utf-8-sig')
    require(text.count(NEW_PREFIX)==8 and OLD_PREFIX not in text
            and '../../outputs/v20_load_accounting_20261019_run01/review/normal_window_analysis02/' not in text,
            'New03 document links reference a superseded analysis')
    for row in receipt['outputs']:require(sha(root/row['path'])==row['sha256'],'New03 generated output SHA differs')
    proof=dict(status='passed',utc=now(),document=fingerprint(root,doc),collection_receipt=fingerprint(root,receipt_path),
               document_modified=False,link_prefix=NEW_PREFIX,links_verified=8,numeric_tables_unchanged=True,
               legacy_analysis02_not_executed=True)
    write_new(out/'DOCUMENT_LINK_VERIFICATION01.json',proof);return proof


def collect_once(root):
    root = Path(root).resolve(strict=True)
    ready = readiness(root)
    require(ready['ready'], 'not ready: no prepared project helper was imported')
    out = root / RUN_REL / 'review/normal_window_analysis03'
    require(not any((out / n).exists() for n in ('COLLECTION_RECEIPT.json', 'ONESHOT_OWNER01.json', 'ONESHOT_RESULT01.json'))
            and not list(out.glob('*.csv')), 'One-shot outputs already exist; preserve them and do not retry automatically')
    launcher_sha = sha(Path(__file__))
    intent = dict(schema='hospital_ies.v20.normal_oneshot_owner.v1', utc=now(), pid=os.getpid(), command=list(sys.argv), cwd=str(Path.cwd()), scientific_root=str(root),
                  readiness=ready, launcher_sha256=launcher_sha, helper_sha256=HELPER_SHA,
                  original_waiter_exit_code=15, original_waiter_exit_is_not_collection_status=True,
                  no_wait_loop=True, physical_episodes_started=0)
    write_new(out / 'ONESHOT_OWNER01.json', intent)
    try:
        helper = _load_helper(out / 'normal_window_analysis.py')
        require(helper.ROOT == root and helper.HERE == out and sha(Path(helper.__file__)) == HELPER_SHA,
                'Prepared helper import origin differs')
        collected = helper.collect()
        require(collected.get('status') == 'passed' and collected.get('actual_qualified_trajectories') == 12
                and collected.get('physical_episodes_started') == collected.get('raw_files_opened') == collected.get('annual_values_parsed') == 0,
                'Saved-result collector did not return the qualified twelve normals')
        correction = verify_document_links(root, out, root / 'docs/v20/NORMAL_WINDOW_ANALYSIS.md', out / 'COLLECTION_RECEIPT.json')
        require(sha(Path(__file__)) == launcher_sha and sha(out / 'normal_window_analysis.py') == HELPER_SHA,
                'One-shot launcher or prepared helper changed during execution')
        result = dict(schema='hospital_ies.v20.normal_oneshot_result.v1', status='passed', utc=now(), pid=os.getpid(), command=list(sys.argv), cwd=str(Path.cwd()), scientific_root=str(root),
            launcher_sha256=launcher_sha, prepared_helper_sha256=HELPER_SHA,
            original_waiter_exit_code=15, original_waiter_capture_unchanged=True,
            collection_receipt=fingerprint(root, out / 'COLLECTION_RECEIPT.json'),
            document_link_verification=fingerprint(root, out / 'DOCUMENT_LINK_VERIFICATION01.json'),
            final_document=correction['document'], normal_qualified_trajectories=12,
            no_new_registration=True, no_physics=True, no_wait_loop=True)
        write_new(out / 'ONESHOT_RESULT01.json', result)
        return result
    except BaseException as exc:
        if not (out / 'ONESHOT_RESULT01.json').exists():
            write_new(out / 'ONESHOT_RESULT01.json', dict(status='failed_preserve_postprocess_evidence', utc=now(), pid=os.getpid(), command=list(sys.argv), cwd=str(Path.cwd()), scientific_root=str(root),
                error_type=type(exc).__name__, error=str(exc), launcher_sha256=launcher_sha,
                original_waiter_exit_code=15, no_physics=True, no_automatic_retry=True))
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--check-only', action='store_true')
    mode.add_argument('--collect', action='store_true')
    args = parser.parse_args(argv)
    try:
        result = readiness(ROOT) if args.check_only else collect_once(ROOT)
        print(json.dumps(result, ensure_ascii=True, allow_nan=False))
        return 0 if result.get('ready', result.get('status') == 'passed') else 3
    except BaseException:
        traceback.print_exc()
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
