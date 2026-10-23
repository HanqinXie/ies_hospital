"""Small selection arithmetic fixtures; these are not scientific outcomes."""
import importlib.util
import json
from pathlib import Path

import pytest

PROJECT=Path(__file__).resolve().parents[1]
STAGING=PROJECT if (PROJECT/'selection.py').is_file() else PROJECT/'tools'/'sci_engineering'


def api():
    path=STAGING/'selection.py'
    assert path.is_file(),'Missing preregistered development selector'
    spec=importlib.util.spec_from_file_location('sci_selection_test',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def specifications():
    return [dict(policy_id=f'{alias}_{value}',kind='sci_engineering',display_alias=alias,
        selection_role='development_only',config={parameter:value,'unchanged':True})
        for alias,parameter,values in [('F','fixed_reserve_hours',(.5,1.,2.)),
            ('A','release_after_minutes',(120.,240.))] for value in values]


def cases(selector):
    specs=specifications();rows=[]
    for s in ('s1','s2'):
        for spec in specs:
            row=dict(phase='development',scenario_id=s,source_block_id='b'+s,
                configuration_id='H0',model_id='M21',source_binding_sha256='source',scenario_sha256=s,
                configuration_sha256='config',policy_id=spec['policy_id'],eligible=True,window_minutes=600.)
            for field in selector.required_metrics():row[field]=0.
            rows.append(row)
    return specs,rows


def test_unique_reliability_dominator_wins_even_with_larger_cost():
    s=api();specs,rows=cases(s)
    for row in rows:
        if row['policy_id'].startswith('F_'):
            value=float(row['policy_id'][2:]);row['P0_e_ENS_kWh']=value
            row['energy_cost_USD']=100./value
        elif row['policy_id']=='A_120.0':row['P2_e_ENS_kWh']=1.
    chosen=s.select_rows(rows,specs,s.default_rule())
    assert chosen['F']['selected_parameter_value']==.5
    assert chosen['F']['decision_tier']=='critical_reliability'
    assert chosen['A']['selected_parameter_value']==240.
    assert chosen['A']['decision_tier']=='general_service'


def test_critical_tradeoff_does_not_use_cost_and_dominated_default_uses_nearest_frontier():
    s=api();specs,rows=cases(s)
    for row in rows:
        if row['policy_id'].startswith('F_'):
            v=float(row['policy_id'][2:]);x,y={.5:(0.,2.),1.:(3.,3.),2.:(2.,0.)}[v]
            row.update(P0_e_ENS_kWh=x,P1_c_ENS_kWh=y,energy_cost_USD=0. if v==1. else 100.)
    chosen=s.select_rows(rows,specs,s.default_rule())
    assert chosen['F']['selected_parameter_value']==.5
    assert chosen['F']['decision_status']=='representative_of_tradeoff_frontier'
    assert set(chosen['F']['remaining_candidate_values'])=={.5,2.}
    assert chosen['F']['unified_winner_claimed'] is False
    assert chosen['A']['selected_parameter_value']==120.


def test_unknown_failed_or_missing_paired_candidate_refuses_selection():
    s=api();specs,rows=cases(s)
    rows[0]['eligible']=False
    with pytest.raises(ValueError,match='unknown|failed|incomplete'):s.select_rows(rows,specs,s.default_rule())
    rows[0]['eligible']=True;rows[0]['P0_c_ENS_kWh']=None
    with pytest.raises(ValueError,match='unknown|finite'):s.select_rows(rows,specs,s.default_rule())
    rows[0]['P0_c_ENS_kWh']=0.
    with pytest.raises(ValueError,match='paired|candidate|missing'):s.select_rows(rows[1:],specs,s.default_rule())


def test_confirmation_plan_is_rejected_before_any_result_is_read(tmp_path):
    s=api();campaign=tmp_path/'confirmation';campaign.mkdir()
    (campaign/'PLAN.json').write_text(json.dumps(dict(phase='development',panel={'scenarios':[{'id':'sealed','role':'confirmation'}]})),encoding='utf-8')
    target=campaign/'pairs'/'any'/'attempt_0001';target.mkdir(parents=True)
    (target/'RESULT.json').write_text('This must never be parsed',encoding='utf-8')
    with pytest.raises(ValueError,match='confirmation|development role'):s.collect_development([campaign])


def test_candidate_pairing_includes_source_bytes_window_and_unchanged_configuration():
    s=api();specs,rows=cases(s)
    rows[0]['window_minutes']=720.
    with pytest.raises(ValueError,match='window'):s.select_rows(rows,specs,s.default_rule())
    rows[0]['window_minutes']=600.;rows[0]['source_binding_sha256']='changed'
    with pytest.raises(ValueError,match='source|model'):s.select_rows(rows,specs,s.default_rule())
    rows[0]['source_binding_sha256']='source';specs[0]['config']['unchanged']=False
    with pytest.raises(ValueError,match='nonselection|configuration'):s.select_rows(rows,specs,s.default_rule())


def test_finalization_closes_registered_development_evidence_and_detects_drift(tmp_path):
    s=api();specs=specifications();campaign=tmp_path/'development';campaign.mkdir()
    fixture_path=Path(__file__).with_name('test_sci_analysis.py')
    fixture_spec=importlib.util.spec_from_file_location('selection_arithmetic_fixture',fixture_path)
    fixture=importlib.util.module_from_spec(fixture_spec);fixture_spec.loader.exec_module(fixture)
    scene=dict(id='s1',role='development',source_block_id='bs1',canonical_class='L2',season_quarter=3,
        duration_steps=40,step_minutes=15.)
    config={'unchanged_capacity':True}
    plan=dict(phase='development',model_id='M21_sci_engineering',panel={'scenarios':[scene]},policy_specs=specs,configurations={'H0':config})
    (campaign/'PLAN.json').write_text(json.dumps(plan),encoding='utf-8')
    (campaign/'MANIFEST.json').write_text(json.dumps({'simulator_hash':'source_one'}),encoding='utf-8')
    terminals=[]
    for policy in specs:
        target=campaign/'pairs'/('s1__'+policy['policy_id']+'__H0')/'attempt_0001';target.mkdir(parents=True)
        raw=target/'UNIT_FIXTURE.raw';raw.write_bytes(b'Unit fixture, never a trajectory or research result')
        row=fixture.result(policy=policy['policy_id'],block='bs1')
        row.pop('_scenario');row.pop('_policy')
        row.update(phase='development',source_unchanged=True,policy_sha256=s._digest(policy),scenario_sha256=s._digest(scene),
            configuration_sha256=s._digest(config),raw_path=str(raw),raw_sha256=s._sha(raw))
        receipt_fixture,sidecar,entry=fixture.sidecar_result(target)
        row.update(solver_row_evidence=receipt_fixture['solver_row_evidence'],raw_bytes=raw.stat().st_size,
            raw_total_bytes=raw.stat().st_size+sidecar.stat().st_size)
        result_path=target/'RESULT.json';result_path.write_text(json.dumps(row),encoding='utf-8')
        terminals.append(dict(event='finished',result_path=str(result_path),result_sha256=s._sha(result_path)))
    (campaign/'ATTEMPTS.jsonl').write_text('\n'.join(json.dumps(x) for x in terminals),encoding='utf-8')
    rule=tmp_path/'RULE.json';s.register_rule(rule)
    closed=s.finalize([campaign],rule,tmp_path/'closed')
    assert closed['selection_closed'] is True and closed['confirmation_results_read']==0
    assert closed['selected']['F']['selected_parameter_value']==1.
    assert closed['selected']['A']['selected_parameter_value']==120.
    assert len(closed['latest_candidate_evidence'])==5
    assert all(r['raw_sha256_verified_for_selection'] for r in closed['latest_candidate_evidence'])
    assert all(r['solver_sidecar_verified_for_selection'] for r in closed['latest_candidate_evidence'])
    assert (tmp_path/'closed'/'SELECTED_FA_POLICIES.json').is_file()
    sidecar.unlink()
    with pytest.raises(ValueError,match='sidecar|evidence|unknown|incomplete'):
        s.finalize([campaign],rule,tmp_path/'sidecar_must_not_close')
    # Restore valid sidecar evidence to isolate the existing raw-drift assertion.
    fixture.sidecar_result(target)
    assert s._sha(sidecar)==row['solver_row_evidence']['gzip_sha256']
    raw.write_bytes(b'Corrupted unit raw receipt')
    with pytest.raises(ValueError,match='raw|Raw|evidence|SHA|drift'):
        s.finalize([campaign],rule,tmp_path/'must_not_close')
