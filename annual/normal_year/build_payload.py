"""Read-only audit of the already assembled current handoff payload.

Assembly owns source, freeze, cases and provenance. This entry never copies old
receipts into new evidence, overwrites provenance or starts a scientific model.
"""
import json
from pathlib import Path
from verify_source_copy import PROFILE,ROOT,read_json,safe_path,sha,verify


def validate_payload(package_root=ROOT):
    root=Path(package_root).resolve();identity=verify(root)
    index=read_json(root/'cases/INDEX.json')
    if index.get('source_commit')!=identity['source_commit']:
        raise ValueError('Case index source identity differs')
    rows=index.get('cases')
    if not isinstance(rows,list) or {r.get('method') for r in rows}!={'M_available','M_ready','C_ready'} or len(rows)!=3:
        raise ValueError('Expected exactly three registered normal-year methods')
    for row in rows:
        path=safe_path(root,row['path']);value=read_json(path)
        scenario=value['scenario']
        if sha(path)!=row['file_sha256']:raise ValueError('Registered case bytes differ: '+row['path'])
        if scenario['duration_steps']!=35040 or float(scenario['step_minutes'])!=15.:
            raise ValueError('Annual duration or dispatch cadence differs')
        if scenario.get('model_closure_events'):raise ValueError('Normal year contains disturbances')
    return dict(**identity,payload_audit='passed',cases=len(rows),
        source_and_provenance_modified=False,historical_receipts_copied_as_new=False,
        scientific_tests_executed=False,annual_started=False)


def main():print(json.dumps(validate_payload(),ensure_ascii=False))


if __name__=='__main__':main()
