"""Fixed case/clock/failure coverage; never ranks policy outcomes."""
import hashlib
import json
from pathlib import Path

B = Path(__file__).resolve().parent.parent
ROOT = B.parents[1]
POLICY = {'E': 'endurance_restore_p0_ready_v14r6', 'K1': 'readiness_handoff_K1_v15',
          'C19': 'C19_critical_reserve_v19r1'}


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def main():
    choices = [(f'main_final_r1_{i:03d}', 'E', [f'main_L{i+1}']) for i in range(4)]
    choices += [('long_final_r1_000', p, ['long000']) for p in ('E', 'C19')]
    choices += [('long_final_r1_005', 'K1', ['long005', 'contact_0']),
        ('sensitivity_contact025_r1_000', 'K1', ['contact_0p25']),
        ('propagation_final_r1_000', 'E', ['propagation_ON']),
        ('propagation_final_r1_001', 'E', ['propagation_OFF']),
        ('normal_E_final_r1_000', 'E', ['normal']),
        ('dev_long_r1_000', 'C19', ['unknown_or_failure'])]
    rows = []
    for batch, alias, roles in choices:
        directory = B/'experiments'/batch
        manifest, terminal = read(directory/'manifest.json'), read(directory/'BATCH_RESULT.json')
        scene = manifest['scenarios'][0]
        pair = directory/'pairs'/(scene['id']+'__'+POLICY[alias])
        result = read(pair/'RESULT.json')
        assert result['model_id'] == 'M19_model_closeout' and result['raw_sha256']
        if 'unknown_or_failure' not in roles:
            assert result['status'] == 'completed' and terminal['unknown_full'] == 0
            assert result['analysis']['v19']['episode_audit']['status'] == 'passed'
        else:
            assert result['status'] == 'model_constraint_infeasible' and result.get('error')
        raw = pair/'trajectory.json.gz'
        rows.append(dict(path=raw.relative_to(ROOT).as_posix(), roles=roles,
            reason='Fixed registered case/clock/failure coverage; not a policy ranking. All omitted trajectories retain complete producer metadata/CSV/status/SHA.',
            producer_sha256=result['raw_sha256'], bytes=raw.stat().st_size,
            scientific_status=result['status'], source_epoch=manifest['source_sha256']))
    incomplete = {}
    for i in range(1, 9):
        incomplete[f'dev_key_r1_{i:03d}'] = 'Registered but never attempted; STOP_DEV_KEY_R1 retained after the earlier-source numerical failure. Exit of cancellation wrapper is not a physical trajectory.'
    incomplete['dev_long_r1_001'] = 'Registered but never attempted after failed C19 long000 prefix; STOP_DEV_LONG_R1 retained.'
    incomplete['dev_long_r2_001'] = 'Registered but never attempted after the finite numerical defensive guards required a new source epoch; STOP_DEV_LONG_R2 retained.'
    value = dict(selection_basis='scenario_clock_and_failure_coverage_not_policy_winner',
        raw_entries=rows, role_waivers={}, acknowledged_incomplete_batches=incomplete,
        selected_raw_count=len(rows), selected_raw_bytes=sum(r['bytes'] for r in rows),
        selection_limit='Compact representative whole raw only; every scientific result, failure, unknown, compact CSV, input inventory and command ledger remains selected.',
        no_full_old_raw_audit=True, physical_episodes_started=0,
        script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    destination=B/'design/DELIVERY_REPRESENTATIVES.json'
    with destination.open('x',encoding='utf-8') as stream:
        json.dump(value,stream,ensure_ascii=False,indent=2,allow_nan=False)
    print(json.dumps(dict(selected_raw_count=value['selected_raw_count'],selected_raw_bytes=value['selected_raw_bytes'],acknowledged_not_started_batches=len(incomplete))))


if __name__ == '__main__':
    main()
