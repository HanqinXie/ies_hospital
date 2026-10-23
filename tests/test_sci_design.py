"""Scientific separation tests for the new V21 metadata recipe."""
from pathlib import Path
import importlib
import json
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]


def design():
    return importlib.import_module('tools.sci_engineering.design')


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')


def test_source_support_includes_full_episode_and_endpoint_hour():
    d = design()
    support = d.source_support(dict(start_hour=25, duration_steps=40,
        step_minutes=15., pre_event_warmup_steps=8, tail_interval_minutes=[480., 600.]))
    assert support == dict(raw_start_minute=1440., raw_end_minute=2100.)


def test_scanner_distinguishes_planned_from_actual_without_reading_results(tmp_path, monkeypatch):
    d = design()
    scene = dict(id='old_real', start_hour=25, duration_steps=40, step_minutes=15.)
    planned = dict(id='never_started', start_hour=100, duration_steps=40, step_minutes=15.)
    write(tmp_path/'outputs/run/design/PROPOSAL.json', dict(scenarios=[scene, planned]))
    write(tmp_path/'outputs/run/manifest.json', dict(scenarios=[scene]))
    (tmp_path/'outputs/run/ATTEMPTS.jsonl').write_text(
        json.dumps(dict(event='started', scenario_id='old_real', policy_id='P'))+'\n', encoding='utf-8')
    write(tmp_path/'outputs/run/pairs/old_real__P/RESULT.json', dict(secret_result='must not be read'))
    original = Path.read_bytes
    def guarded(path):
        assert path.name != 'RESULT.json'
        return original(path)
    monkeypatch.setattr(Path, 'read_bytes', guarded)
    registry = d.scan_historical_support(tmp_path, sealed_days=(21,))
    assert registry['parse_errors'] == []
    assert {row['scenario_id'] for row in registry['actual_support']} == {'old_real'}
    assert {row['scenario_id'] for row in registry['planned_support']} == {'old_real', 'never_started'}
    assert registry['sealed_days'] == [21]
    assert registry['result_or_raw_files_read'] is False


def test_failed_actual_start_and_missing_terminal_still_count_as_exposed(tmp_path):
    d = design()
    scene = dict(id='failed', start_hour=121, duration_steps=8, step_minutes=15.)
    write(tmp_path/'outputs/real/manifest.json', dict(scenarios=[scene]))
    (tmp_path/'outputs/real/attempts.jsonl').write_text(
        json.dumps(dict(event='started', scenario_id='failed'))+'\n', encoding='utf-8')
    registry = d.scan_historical_support(tmp_path, sealed_days=())
    assert len(registry['actual_support']) == 1
    assert registry['actual_support'][0]['terminal_observed'] is False


def test_branch_attempt_inherits_actual_snapshot_parent_support(tmp_path):
    d=design()
    scene=dict(id='snapshot_parent',start_hour=241,duration_steps=8,step_minutes=15.)
    write(tmp_path/'outputs/real/manifest.json',dict(scenarios=[scene]))
    rows=[dict(event='started',attempt_type='snapshot_capture',scenario_id='snapshot_parent'),
          dict(event='finished',attempt_type='snapshot_capture',status='completed'),
          dict(event='started',attempt_type='action_branch',candidate_id='branch_0')]
    (tmp_path/'outputs/real/attempts.jsonl').write_text('\n'.join(json.dumps(x) for x in rows)+'\n',encoding='utf8')
    registry=d.scan_historical_support(tmp_path,sealed_days=())
    assert registry['unresolved_starts']==[]
    assert {r['scenario_id'] for r in registry['actual_support']}=={'snapshot_parent'}


def test_probe_sidecar_identity_and_fit_journal_do_not_leave_false_unknowns(tmp_path):
    d=design()
    scene=dict(id='probe_parent',start_hour=481,duration_steps=8,step_minutes=15.)
    write(tmp_path/'outputs/real/manifest.json',dict(scenarios=[scene]))
    directory=tmp_path/'outputs/real/probe/snapshot_002/attempt_0001'
    write(directory/'plan.json',dict(entry=dict(scenario_id='probe_parent')))
    (directory/'attempts.jsonl').write_text(json.dumps(dict(event='started',attempt_type='isolated_current_projection'))+'\n',encoding='utf8')
    fit=tmp_path/'outputs/real/fit_attempts.jsonl'
    fit.write_text(json.dumps(dict(event='started',model='NN'))+'\n',encoding='utf8')
    registry=d.scan_historical_support(tmp_path,sealed_days=())
    assert registry['unresolved_starts']==[]
    assert {r['scenario_id'] for r in registry['actual_support']}=={'probe_parent'}


def test_windows_utf16_plan_is_parsed_with_original_bytes_bound(tmp_path):
    d=design();path=tmp_path/'outputs/real/workflow_plan_prelaunch.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(dict(scenarios=[dict(id='unicode_plan',start_hour=701,duration_steps=8)])),encoding='utf-16')
    registry=d.scan_historical_support(tmp_path,sealed_days=())
    assert registry['parse_errors']==[]
    assert {r['scenario_id'] for r in registry['planned_support']}=={'unicode_plan'}


def test_business_templates_use_named_source_zones_without_priority_reclassification():
    d=design()
    blocks=d.select_source_blocks(dict(actual_support=[],parse_errors=[],unresolved_starts=[],sealed_days=[]))
    scenes=d.scenario_panel(blocks)['scenarios']
    shocked=[s for s in scenes if s['canonical_class']=='L3']
    assert {s['clinical_template_id'] for s in shocked}=={'emergency_department','observation_rooms','operating_rooms'}
    assert all(s['shock_by_zone'] and not s['shock_by_priority'] for s in shocked)
    assert any('ER_TRAUMA1_FLR_1' in s['shock_by_zone'] for s in shocked)
    assert any('IC_PATROOM1_MULT5_FLR_2' in s['shock_by_zone'] for s in shocked)
    assert any('OR2_MULT5_FLR_2' in s['shock_by_zone'] for s in shocked)


def test_source_hour_novelty_does_not_claim_new_day_and_roles_never_share_days():
    d=design()
    historical=[dict(raw_start_minute=day*1440.,raw_end_minute=day*1440.+60.) for day in range(365)]
    blocks=d.select_source_blocks(dict(actual_support=historical,parse_errors=[],unresolved_starts=[],sealed_days=[]),
                                  episode_minutes=600.)
    assert all(b['fresh_source_hours'] and b['historical_same_calendar_day_exposure'] for b in blocks)
    assert all(not b['globally_historical_unseen'] and not b['new_calendar_background'] for b in blocks)
    touched=[]
    for block in blocks:
        assert not set(block['source_calendar_days'])&set(touched)
        touched.extend(block['source_calendar_days'])


def test_blocks_are_seasonally_balanced_and_isolated_including_endpoint_and_embargo():
    d = design()
    registry = dict(actual_support=[], parse_errors=[], unresolved_starts=[], sealed_days=list(d.SEALED_DAYS))
    blocks = d.select_source_blocks(registry, n_confirmation=24, n_development=8, episode_minutes=1080.)
    assert len(blocks) == 32
    for role, count in [('confirmation', 6), ('development', 2)]:
        assert [sum(b['role']==role and b['season_quarter']==q for b in blocks) for q in range(1,5)] == [count]*4
    assert d.validate_block_isolation(blocks)['isolated']
    for block in blocks:
        assert block['raw_end_minute']-block['raw_start_minute'] == 1140.
        assert block['historical_exposure_status'] == 'no_recorded_actual_incident_overlap'
        assert all(not(block['raw_start_minute'] < (day+1)*1440 and day*1440 < block['raw_end_minute']) for day in d.SEALED_DAYS)


def test_strict_new_selection_refuses_insufficient_or_unresolved_support():
    d = design()
    registry = dict(actual_support=[dict(raw_start_minute=0., raw_end_minute=525600.)],
                    parse_errors=[], unresolved_starts=[], sealed_days=[])
    with pytest.raises(ValueError, match='Insufficient'):
        d.select_source_blocks(registry, n_confirmation=24, n_development=8)
    registry.update(actual_support=[], unresolved_starts=[dict(scenario_id='unknown')])
    with pytest.raises(ValueError, match='unresolved'):
        d.select_source_blocks(registry)


def test_explicit_historical_selection_never_relabels_history_as_unseen():
    d = design()
    registry = dict(actual_support=[dict(raw_start_minute=0., raw_end_minute=525600.)],
                    parse_errors=[], unresolved_starts=[], sealed_days=[])
    blocks = d.select_source_blocks(registry, historical_mode='current_round_isolated')
    assert all(b['historical_exposure_status']=='historically_exposed_current_round_isolated' for b in blocks)
    assert all(b['globally_historical_unseen'] is False for b in blocks)


def test_four_classes_map_explicitly_and_factors_are_not_index_locked():
    d = design()
    blocks = d.select_source_blocks(dict(actual_support=[], parse_errors=[], unresolved_starts=[], sealed_days=[]))
    panel = d.scenario_panel(blocks, variants_per_block=4, seed=20261023)
    scenes = panel['scenarios']
    assert len(scenes) == len(blocks)*16
    assert {s['canonical_class']:s['event_class'] for s in scenes} == d.CANONICAL_TO_NATIVE
    assert len({s['id'] for s in scenes}) == len(scenes)
    assert {s['fault_target'] for s in scenes if s['canonical_class']!='L3'} >= set(d.FAULT_TARGETS)
    factors = {(s['response_delay_minutes'], s['fault_availability'], s['demand_amplitude']) for s in scenes}
    for delay in (0., 15., 30.):
        assert len({(a,b) for x,a,b in factors if x==delay}) > 2
    first = next(s for s in scenes if s['canonical_class']=='L1')
    paired = next(s for s in scenes if s['matched_factor_id']==first['matched_factor_id'] and s['canonical_class']=='L2')
    for key in ('fault_target','fault_availability','fault_duration_minutes','response_delay_minutes','start_hour'):
        assert first[key] == paired[key]
    assert first['enable_propagation'] is False
    assert paired['enable_propagation'] is True


def test_cross_role_overlap_is_rejected_including_recovery_tail():
    d = design()
    with pytest.raises(ValueError, match='overlap'):
        d.validate_block_isolation([
            dict(source_block_id='dev', role='development', raw_start_minute=0.,raw_end_minute=600.),
            dict(source_block_id='val', role='confirmation',raw_start_minute=660.,raw_end_minute=1260.)])


def test_cli_pilot_proposal_has_real_preparation_tail_and_four_classes(tmp_path):
    cli=importlib.import_module('scripts.sci_campaign')
    output=tmp_path/'PILOT.json'
    assert cli.main(['pilot-proposal','--output',str(output),'--start-hour','4001'])==0
    panel=json.loads(output.read_text(encoding='utf8'))
    assert len(panel['scenarios'])==4
    assert {s['canonical_class'] for s in panel['scenarios']}=={'L1','L2','L3','L4'}
    assert all(s['duration_steps']==24 and s['pre_event_warmup_steps']==1 for s in panel['scenarios'])
    assert all(s['fault_start_minute']==60. and s['recovery_start_minute']==180. for s in panel['scenarios'])
    assert all(s['design_family'] and s['sampling_cohort'] and s['sampling_method'] for s in panel['scenarios'])


def test_cli_design_keeps_development_and_confirmation_provenance(tmp_path):
    cli=importlib.import_module('scripts.sci_campaign')
    registry=tmp_path/'REGISTRY.json';output=tmp_path/'PANEL.json'
    write(registry,dict(actual_support=[],parse_errors=[],unresolved_starts=[],sealed_days=[]))
    assert cli.main(['design','--registry',str(registry),'--output',str(output),
        '--confirmation-blocks','4','--development-blocks','4',
        '--confirmation-variants','1','--development-variants','1'])==0
    panel=json.loads(output.read_text(encoding='utf8'))
    assert len(panel['scenarios'])==32
    assert {s['role'] for s in panel['scenarios']}=={'development','confirmation'}
