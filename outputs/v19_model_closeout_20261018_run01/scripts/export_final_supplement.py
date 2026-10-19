"""Project saved, qualified final 600-minute results; never open raw or rerun."""
import csv
import hashlib
import json
import math
from pathlib import Path

B = Path(__file__).resolve().parents[1]


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def read(p):
    return json.loads(p.read_text(encoding='utf-8-sig'))


def rows(p):
    with p.open(encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))


def write_csv(p, records):
    with p.open('x', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(records[0]))
        w.writeheader()
        w.writerows(records)


def number(r, k):
    v = float(r[k])
    if not math.isfinite(v):
        raise ValueError('Nonfinite saved field: ' + k)
    return v


def main():
    paper = B/'paper_results/FINAL02'
    summary = B/'review/summary_ALL_FINAL02'
    pr = read(paper/'PAPER_RESULTS_RECEIPT.json')
    sr = read(summary/'SUMMARY_RECEIPT.json')
    paths = [paper/'OUTCOME_STATUS.csv', paper/'COHORT_COVERAGE.csv', summary/'RESULT_WINDOWS.csv']
    for path in paths:
        receipt = pr if path.parent == paper else sr
        if receipt['output_sha256'][path.name] != sha(path):
            raise ValueError('Bound table changed: ' + path.name)
    outcome = [r for r in rows(paths[0]) if r['group'] in ('main', 'confirmation')]
    assert len(outcome) == 336 and all(r['eligible'] == 'True' for r in outcome)
    aliases = {'E': 'endurance_restore_p0_ready_v14r6', 'K1': 'readiness_handoff_K1_v15',
               'C19': 'C19_critical_reserve_v19r1'}
    saved = rows(paths[2])
    selected = []
    for o in outcome:
        matches = [r for r in saved if r['batch'] == o['batch'] and r['scenario_id'] == o['scenario_id']
                   and r['policy_id'] == aliases[o['policy']] and r['window'] == 'full']
        assert len(matches) == 1
        r = matches[0]
        for k in ('result_sha256', 'source_binding_sha256', 'model_config_sha256', 'policy_panel_sha256'):
            assert r[k] == o[k] and r[k]
        assert r['complete'] == 'True' and r['analysis_error'] == 'False'
        assert r['episode_audit_status'] == 'passed' and r['panel_complete_intersection'] == 'True'
        assert number(r, 'window_start_minute') == 0 and number(r, 'window_end_minute') == 600
        selected.append((o, r))
    lower, energy = [], []
    energy_keys = ['grid_purchase_kWh', 'natural_gas_total_MMBtu', 'DG_electric_work_kWh',
                   'DG_shaft_work_kWh', 'DG_loss_work_kWh', 'DG_rotor_delta_kWh', 'actual_energy_fee_USD']
    for group, expected in [('main', 20), ('confirmation', 8)]:
        for cls in ('L1', 'L2', 'L3', 'L4', 'ALL'):
            for policy in aliases:
                own = [r for o, r in selected if o['group'] == group and o['policy'] == policy
                       and (o['canonical_class'] == cls or cls == 'ALL')]
                count = expected * (4 if cls == 'ALL' else 1)
                assert len(own) == count
                identity = dict(group=group, canonical_class=cls, policy=policy, specifications=count,
                                window_start_minute=0, window_end_minute=600,
                                source_binding_sha256=pr['final_source_binding_sha256'])
                energy.append(dict(identity, **{k: math.fsum(number(r, k) for r in own) for k in energy_keys}))
                for priority in ('P2', 'P3'):
                    for carrier in ('e', 'h', 'c'):
                        mk = priority+'_'+carrier+'_material_minutes'
                        lower.append(dict(identity, priority=priority, carrier=carrier,
                            ENS_kWh=math.fsum(number(r, priority+'_'+carrier+'_ENS_kWh') for r in own),
                            material_specifications=sum(number(r, mk) > 1e-8 for r in own),
                            material_minutes=math.fsum(number(r, mk) for r in own)))
    out = paper/'supplement01'
    out.mkdir(exist_ok=False)
    write_csv(out/'LOWER_PRIORITY_ENS.csv', lower)
    write_csv(out/'SOURCE_ENERGY.csv', energy)
    (out/'README.md').write_text(
        '# Saved final service and source-energy supplement\n\n'
        'Main80 and independent-confirmation32 mechanisms are kept separate. '
        'Every 600-minute result must belong to the qualified three-policy intersection in FINAL02. '
        'P2/P3 ENS measures missed service, with carriers kept separate; it is not clinical harm. '
        'Exact demand/delivered integrals for the finite-energy comparison remain in '
        '`review/energy_tradeoff/*/LOWER_PRIORITY_SERVICE.csv`. '
        'Source energy and energy fees are actual saved ledgers. '
        'No raw, annual input, physical episode or new model calculation is opened here.\n', encoding='utf-8')
    receipt = dict(inputs=[dict(path=str(p.relative_to(B)), sha256=sha(p)) for p in paths],
        paper_receipt_sha256=sha(paper/'PAPER_RESULTS_RECEIPT.json'),
        summary_receipt_sha256=sha(summary/'SUMMARY_RECEIPT.json'), script_sha256=sha(Path(__file__)),
        qualified_outcomes=len(selected), lower_priority_rows=len(lower), source_energy_rows=len(energy),
        raw_opened=0, annual_inputs_loaded=0, physical_episodes_started=0,
        outputs={p.name: sha(p) for p in out.iterdir() if p.is_file()})
    (out/'RECEIPT.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(dict(qualified_outcomes=len(selected), lower_rows=len(lower), source_rows=len(energy))))


if __name__ == '__main__':
    main()
