"""Export frozen long000 A/B/C/D observations; no simulation or raw reads."""
import csv
import hashlib
import json
from pathlib import Path

B = Path(__file__).resolve().parent.parent
D = B / 'experiments/long_final_r1_000'


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    out = B / 'review/final_long000_semantics01'
    out.mkdir(exist_ok=False)
    frozen = read(B / 'MODEL_FREEZE_CLOSEOUT01.json')
    plan, terminal = read(D / 'plan.json'), read(D / 'BATCH_RESULT.json')
    assert plan['source_sha256'] == frozen['source_sha256']
    assert terminal['complete_full'] == terminal['actual_attempts'] == 3
    assert terminal['unknown_full'] == terminal['analysis_failure_or_unavailable_count'] == 0
    source = D / 'analysis/ENGINEERING_INTERVALS.csv'
    rows = []
    with source.open(encoding='utf-8-sig', newline='') as stream:
        for r in csv.DictReader(stream):
            start, end = float(r['start_minute']), float(r['end_minute'])
            if not (32.5 <= start <= 60 or 805 <= start < 830):
                continue
            values = {k: json.loads(r[k]) for k in ('loads_kW', 'disconnected_loads_kW',
                'connected_unserved_kW', 'unserved_kW', 'action', 'arrived_source_setpoints', 'pre_state')}
            for i, priority in enumerate(('P0', 'P1')):
                for j, carrier in enumerate(('e', 'h', 'c')):
                    demand = values['loads_kW'][i][j]
                    cut = values['disconnected_loads_kW'][i][j]
                    shortage = values['connected_unserved_kW'][i][j]
                    delivered = demand - values['unserved_kW'][i][j]
                    residual = demand - cut - shortage - delivered
                    assert abs(residual) <= 1e-7
                    rows.append(dict(policy_id=r['policy_id'], start_minute=start, end_minute=end,
                        command_id=r['command_id'], priority=priority, carrier=carrier,
                        A_demand_kW=demand, B_active_cut_kW=cut,
                        C_connected_unserved_kW=shortage, D_delivered_kW=delivered,
                        ledger_identity_residual_kW=residual,
                        actual_DG_kW=values['action']['diesel_kW'],
                        arrived_DG_ceiling_kW=values['arrived_source_setpoints']['diesel_kW'],
                        DG_status_before=values['pre_state']['diesel_status'],
                        remaining_shaft_before_kWh=values['pre_state']['diesel_energy_remaining_kWh']))
    assert rows and len({r['policy_id'] for r in rows}) == 3
    target = out / 'A_B_C_D_KEY_INTERVALS.csv'
    with target.open('x', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)
    receipt = dict(freeze_sha256=sha(B/'MODEL_FREEZE_CLOSEOUT01.json'),
        source_csv_sha256=sha(source), batch_result_sha256=sha(D/'BATCH_RESULT.json'),
        output_sha256=sha(target), script_sha256=sha(Path(__file__)), rows=len(rows),
        scope='Frozen final long000, observed early startup/contact and late return intervals only',
        output_basis='D = original demand minus logged total unserved; A-B-C-D checked, original episode audits separate',
        raw_files_opened=0, physical_episodes_started=0)
    (out/'RECEIPT.json').write_text(json.dumps(receipt, indent=2), encoding='utf-8')
    print(json.dumps(receipt))


if __name__ == '__main__':
    main()
