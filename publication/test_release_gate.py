"""Read-only publication-gate tests: synthetic cases and saved main000 only.

No episode, annual value parsing, registration, or production edits are made.
The synthetic 148/406 census is an arithmetic fixture, not an observed campaign.
"""
from __future__ import annotations

from collections import Counter
import copy
from functools import lru_cache
import importlib.util
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / 'outputs/v20_load_accounting_20261019_run01'


@lru_cache(maxsize=1)
def gate():
    path = ROOT / 'publication/release_gate.py'
    assert path.is_file(), 'Final publication requires a strict actual-campaign qualification gate'
    spec = importlib.util.spec_from_file_location('publication_release_gate_tested', path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


@lru_cache(maxsize=1)
def saved_main():
    directory = RUN / 'experiments/v20_main_000'
    manifest = read(directory / 'manifest.json')
    plan = read(directory / 'plan.json')
    scene = manifest['scenarios'][0]
    result = read(directory / 'pairs' / (scene['id'] + '__' + plan['policy_specs'][0]['policy_id']) / 'RESULT.json')
    frozen = read(RUN / 'MODEL_FREEZE_ACCOUNTING01.json')
    return dict(directory=directory, plan=plan, manifest=manifest, scene=scene,
                record=result, frozen=frozen, terminal=read(directory / 'BATCH_RESULT.json'))


def synthetic_jobs():
    jobs = []
    counts = dict(main=80, long=8, normal=12, compat=7, fixed8=32, propagation=6, open1=2, contact025=1)
    for cohort, count in counts.items():
        for i in range(count):
            aliases = ['C18_M20'] if cohort == 'compat' else [(['E', 'K1', 'C19'][i % 3])] if cohort == 'normal' else ['E', 'K1', 'C19']
            day = i // 4 if cohort in ('main', 'fixed8') else i // 3 if cohort == 'normal' else i
            scene = dict(id=f'synthetic_{cohort}_{i // 3 if cohort == "normal" else i}',
                         source_day_index=day, canonical_class=('L' + str(i % 4 + 1)),
                         duration_steps=40, step_minutes=15.)
            if cohort == 'normal':
                hours = [24., 48., 72., 24.][i // 3]
                scene.update(normal_duration_hours=hours, duration_steps=int(hours * 4), canonical_class='N2')
            jobs.append(dict(name=f'v20_{cohort}_{i:03d}', cohort=cohort, scene=scene, policies=aliases,
                             normal_serial=cohort == 'normal', config_variant=cohort if cohort in ('open1', 'contact025') else 'base',
                             source_proposal='synthetic_only.json'))
    targets = [j['scene'] for j in jobs if j['cohort'] == 'main'][:5] + [j['scene'] for j in jobs if j['cohort'] == 'long'][:2]
    for job, scene in zip([j for j in jobs if j['cohort'] == 'compat'], targets):
        job['scene'] = copy.deepcopy(scene)
    return jobs


def test_synthetic_census_is_148_batches_406_pairs_not_actual_results():
    value = gate().qualify_census(synthetic_jobs())
    assert value['batches'] == 148 and value['pairs'] == 406
    assert value['fixed_backgrounds'] == 8 and value['normal_windows'] == 4
    assert value['cohort_pairs'] == dict(main=240, long=24, normal=12, compat=7, fixed8=96, propagation=18, open1=6, contact025=3)


@pytest.mark.parametrize('mutation', ['missing_batch', 'duplicate_batch', 'wrong_policy', 'main_class', 'fixed_background', 'normal_window'])
def test_census_rejects_wrong_denominator_or_background(mutation):
    jobs = synthetic_jobs()
    if mutation == 'missing_batch': jobs.pop()
    if mutation == 'duplicate_batch': jobs[-1]['name'] = jobs[0]['name']
    if mutation == 'wrong_policy': jobs[0]['policies'] = ['E', 'K1', 'C18_M20']
    if mutation == 'main_class': jobs[0]['scene']['canonical_class'] = 'L2'
    if mutation == 'fixed_background':
        for j in jobs:
            if j['cohort'] == 'fixed8': j['scene']['source_day_index'] = 0
    if mutation == 'normal_window': next(j for j in jobs if j['cohort'] == 'normal')['scene']['duration_steps'] = 40
    with pytest.raises(ValueError): gate().qualify_census(jobs)


def test_actual_main_window_has_all_60_finite_service_values_with_losses_retained():
    record = copy.deepcopy(saved_main()['record'])
    proof = gate().qualify_complete_window(record, 600.)
    assert proof['service_scalar_count'] == 60 and proof['window_minutes'] == [0., 600.]
    assert record['analysis']['v18']['windows']['registered_full']['all_registered']['carrier_service']['P0']['e']['ENS_kWh'] > 0


@pytest.mark.parametrize('mutation', ['window_prefix', 'ledger_prefix', 'short_bounds', 'unknown_tail',
                                      'short_observed', 'missing_all', 'missing_scalar', 'nonfinite_scalar',
                                      'boolean_scalar', 'unknown_cost'])
def test_complete_flag_alone_cannot_qualify_service_accounting(mutation):
    record = copy.deepcopy(saved_main()['record'])
    a = record['analysis']; w = a['v18']['windows']['registered_full']; l = a['v20']['service_ledgers']['registered_full']
    if mutation == 'window_prefix': w['complete'] = False
    if mutation == 'ledger_prefix': l['complete'] = False
    if mutation == 'short_bounds': l['window_minutes'] = [0., 599.]
    if mutation == 'unknown_tail': w['unknown_tail_minutes'] = [599., 600.]
    if mutation == 'short_observed': a['observed_until_minute'] = 599.
    if mutation == 'missing_all': l['all_registered'] = None
    if mutation == 'missing_scalar': l['all_registered']['P2']['h']['delivered_kWh'] = None
    if mutation == 'nonfinite_scalar': l['all_registered']['P3']['c']['requested_kWh'] = float('nan')
    if mutation == 'boolean_scalar': l['all_registered']['P1']['e']['ENS_kWh'] = False
    if mutation == 'unknown_cost': w['all_registered']['economics']['accounting_cost_USD'] = None
    with pytest.raises(ValueError): gate().qualify_complete_window(record, 600.)


def test_signed_rotor_and_unknown_exact_diesel_co2_are_not_failed_service():
    record = copy.deepcopy(saved_main()['record'])
    for name in ('observed_prefix', 'all_registered'):
        record['analysis']['v18']['windows']['registered_full'][name]['economics']['DG_rotor_delta_kWh'] = -1.
    assert gate().qualify_complete_window(record, 600.)['complete'] is True


@pytest.mark.parametrize('mutation', ['unknown_source', 'short_source', 'source_unit', 'unknown_DG_budget'])
def test_complete_service_flag_does_not_hide_unknown_source_or_budget_accounting(mutation):
    record = copy.deepcopy(saved_main()['record'])
    ledger = record['analysis']['v20']['service_ledgers']['registered_full']
    source = ledger['actual_source_integrals']['GRID_electric_kWh']
    if mutation == 'unknown_source': source['value'] = None
    if mutation == 'short_source': source['covered_minutes'] = 599.
    if mutation == 'source_unit': source['unit'] = 'kW'
    if mutation == 'unknown_DG_budget': ledger['DG_budget_finish_kWh_equivalent'] = None
    with pytest.raises(ValueError): gate().qualify_complete_window(record, 600.)


def test_actual_initial_and_stream_match_allow_policy_dependent_injection():
    saved = saved_main(); evidence = saved['terminal']['matching_evidence'][saved['scene']['id']]
    assert evidence['same_injection_state'] is False
    assert gate().qualify_matching(evidence, saved['scene'], 3)['actual_stream_recomputed'] is True


@pytest.mark.parametrize('mutation', ['wrong_stream', 'unknown_reset', 'short_list', 'unmatched_reset'])
def test_matching_fails_closed_on_missing_or_changed_actual_evidence(mutation):
    saved = saved_main(); evidence = copy.deepcopy(saved['terminal']['matching_evidence'][saved['scene']['id']])
    if mutation == 'wrong_stream': evidence['applied_exogenous_definition_sha256'] = ['0' * 64] * 3
    if mutation == 'unknown_reset': evidence['initial_state_sha256'] = [None] * 3
    if mutation == 'short_list': evidence['initial_state_sha256'].pop()
    if mutation == 'unmatched_reset': evidence['initial_state_sha256'][0] = 'f' * 64
    with pytest.raises(ValueError): gate().qualify_matching(evidence, saved['scene'], 3)


def test_normal_no_fault_injection_remains_not_applicable():
    scene = dict(start_hour=1, duration_steps=96, step_minutes=15., first_event_minute=None, faults={})
    evidence = dict(initial_state_sha256=['a' * 64], same_initial_state=True, same_exogenous_definition=True,
                    applied_exogenous_definition_sha256=[gate().stream_hash(scene)], injection_state_sha256=[None])
    assert gate().qualify_matching(evidence, scene, 1)['injection_not_applicable'] is True


def test_real_saved_main000_batch_qualifies_without_opening_raw():
    api = gate().load_api(ROOT)
    job = api.campaign.campaign_jobs(RUN / 'provenance/v19_design')[0]
    proof = gate().qualify_registered_batch(ROOT, job, saved_main()['frozen'], gate().sha(RUN / 'MODEL_FREEZE_ACCOUNTING01.json'), api=api)
    assert proof['pairs'] == 3 and proof['qualified'] is True
    assert proof['raw_files_opened'] == 0
    assert all(not row['path'].endswith('.gz') for row in proof['evidence_files'])


def test_pure_registration_rejects_resigned_unreviewed_scene_metadata():
    api = gate().load_api(ROOT); saved = saved_main()
    job = api.campaign.campaign_jobs(RUN / 'provenance/v19_design')[0]
    proposal, config = gate().expected_registration(ROOT, job, saved['frozen'], api)
    plan, manifest = copy.deepcopy(saved['plan']), copy.deepcopy(saved['manifest'])
    manifest['scenarios'][0]['unreviewed_new_dynamics'] = 1
    manifest['signature'] = gate().digest({k: v for k, v in manifest.items() if k != 'signature'})
    plan['manifest_signature'] = manifest['signature']; plan['manifest_sha256'] = 'f' * 64
    with pytest.raises(ValueError, match='scene'):
        gate().qualify_registration(plan, manifest, job, saved['frozen'], gate().sha(RUN / 'MODEL_FREEZE_ACCOUNTING01.json'),
                                    proposal, config, 'f' * 64, api)


def test_terminal_structure_rejects_fake_completed_counts_and_duplicate_attempts():
    saved = saved_main(); terminal = copy.deepcopy(saved['terminal'])
    journal = [json.loads(x) for x in (saved['directory'] / 'ATTEMPTS.jsonl').read_text().splitlines() if x.strip()]
    terminal['actual_attempts'] = 2
    with pytest.raises(ValueError): gate().qualify_terminal_structure(terminal, saved['scene'], saved['plan']['policy_specs'], journal)
    terminal = copy.deepcopy(saved['terminal']); journal.append(copy.deepcopy(journal[0]))
    with pytest.raises(ValueError): gate().qualify_terminal_structure(terminal, saved['scene'], saved['plan']['policy_specs'], journal)


def cross_panel_fixture():
    jobs = synthetic_jobs()
    batches = [dict(batch=j['name'], cohort=j['cohort'], scenario_id=j['scene']['id'],
        registered_scene_sha256=gate().digest(j['scene']), model_config_sha256='c' * 64,
        source_binding_sha256='d' * 64, runtime_inputs_sha256='e' * 64,
        matching=dict(actual_initial_state_sha256='a' * 64, registered_stream_sha256=gate().stream_hash(j['scene']))) for j in jobs]
    return jobs, batches


def test_cross_panel_normal_and_compat_actual_reset_are_required():
    jobs, batches = cross_panel_fixture()
    proof = gate().qualify_cross_panel_batches(jobs, batches)
    assert proof['normal_policy_pairs'] == 12 and proof['compatibility_policy_pairs'] == 7


@pytest.mark.parametrize('mutation', ['normal_reset', 'compat_reset', 'compat_inputs', 'compat_scene', 'missing_compat'])
def test_single_policy_within_panel_matching_cannot_replace_cross_panel_proof(mutation):
    jobs, batches = cross_panel_fixture()
    normal = next(b for b in batches if b['cohort'] == 'normal')
    compat = next(b for b in batches if b['cohort'] == 'compat')
    if mutation == 'normal_reset': normal['matching']['actual_initial_state_sha256'] = 'b' * 64
    if mutation == 'compat_reset': compat['matching']['actual_initial_state_sha256'] = 'b' * 64
    if mutation == 'compat_inputs': compat['runtime_inputs_sha256'] = 'f' * 64
    if mutation == 'compat_scene': compat['registered_scene_sha256'] = 'f' * 64
    if mutation == 'missing_compat': batches.remove(compat)
    with pytest.raises(ValueError): gate().qualify_cross_panel_batches(jobs, batches)


def test_actual_compact_export_bytes_match_terminal_receipt():
    saved = saved_main()
    exports = gate().qualify_compact_exports(ROOT, saved['directory'], saved['terminal'])
    assert len(exports) == 9 and all(row['path'].endswith(('.csv', '.json')) for row in exports)


def test_retained_compact_exports_cannot_claim_a_different_terminal_sha():
    saved = saved_main(); terminal = copy.deepcopy(saved['terminal'])
    terminal['analysis_receipt']['output_sha256']['V20_RESOURCES.csv'] = 'f' * 64
    with pytest.raises(ValueError): gate().qualify_compact_exports(ROOT, saved['directory'], terminal)


def test_real_freeze_census_preflight_stops_before_other_actual_batches(monkeypatch):
    # Verify the actual fixed metadata/preflight path, then deliberately stop
    # before loading even main000. No synthetic 406 execution proof is returned.
    reached = []

    def stop(root, job, frozen, freeze_sha256, *, api=None):
        reached.append(job['name'])
        raise ValueError('test_sentinel_no_other_actual_batch_read')

    monkeypatch.setattr(gate(), 'qualify_registered_batch', stop)
    with pytest.raises(ValueError, match='test_sentinel_no_other_actual_batch_read'):
        gate().qualify_final_campaign(ROOT)
    assert reached == ['v20_main_000']
