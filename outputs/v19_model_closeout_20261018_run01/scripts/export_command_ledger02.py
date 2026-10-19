"""Preserve actual command exits, including orphan streams with unknown exits."""
import csv
import hashlib
import json
from pathlib import Path

B = Path(__file__).resolve().parent.parent


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    out = B/'review/final_command_ledger02'
    out.mkdir(exist_ok=False)
    logs = B/'logs'
    names = set()
    for suffix in ('.command.json', '.stdout.log', '.stderr.log'):
        names.update(p.name[:-len(suffix)] for p in logs.glob('*'+suffix))
    # The active wrapper writes its terminal receipt only after this exporter
    # exits; its streams must not be mislabelled unknown or self-hashed here.
    names.discard('final_command_ledger02')
    rows = []
    for name in sorted(names):
        receipt = logs/(name+'.command.json')
        record = json.loads(receipt.read_text(encoding='utf-8')) if receipt.exists() else {}
        row = dict(name=name, exit_code=record.get('exit_code'),
            terminal_receipt_present=receipt.exists(),
            status='exit_zero' if record.get('exit_code') == 0 else
                   'exit_nonzero' if record.get('exit_code') is not None else 'exit_unknown',
            started_utc=record.get('started_utc'), finished_utc=record.get('finished_utc'),
            elapsed_seconds=record.get('elapsed_seconds'), command=record.get('command'),
            cwd=record.get('cwd'), receipt_sha256=sha(receipt) if receipt.exists() else None)
        for channel in ('stdout', 'stderr'):
            path = logs/(name+'.'+channel+'.log')
            row[channel+'_bytes'] = path.stat().st_size if path.exists() else None
            row[channel+'_sha256'] = sha(path) if path.exists() else None
        rows.append(row)
    with (out/'COMMAND_LEDGER.csv').open('x',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader()
        for row in rows:
            writer.writerow(dict(row,command=json.dumps(row['command'],ensure_ascii=False)))
    gates=[]
    for path in sorted((B/'test_gates').glob('*.json')):
        value=json.loads(path.read_text(encoding='utf-8'))
        gates.append(dict(path=path.relative_to(B).as_posix(),sha256=sha(path),record=value))
    payload=dict(commands=rows,test_gates=gates,
        counts={status:sum(r['status']==status for r in rows) for status in ('exit_zero','exit_nonzero','exit_unknown')},
        unknown_exits_not_replaced_by_zero=True,
        excluded_active_log_producer='final_command_ledger02; its actual terminal receipt and streams are archived after exporter exit',
        orphan_context='review/normal_admission/orphaned_waiters01/RECOVERY_PRECONDITION.json',
        regression_scope='development_gate04 actual 165 passed; full historical pytest not executed',
        archive_build_and_fresh_stage_logs='External deliverables/v19_packaging_logs and archive sidecars; generated after this immutable snapshot',
        script_sha256=sha(Path(__file__)))
    (out/'COMMAND_LEDGER.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(command_counts=payload['counts'],test_gate_files=len(gates))))


if __name__ == '__main__':
    main()
