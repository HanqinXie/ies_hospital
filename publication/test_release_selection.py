"""Compact publication evidence must survive interval/raw pruning."""
from pathlib import Path
import importlib.util

import pytest


def load_builder():
    path = Path(__file__).with_name('build_v20_release.py')
    spec = importlib.util.spec_from_file_location('tested_v20_release_builder', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('candidate', [False, True])
def test_selection_keeps_compact_evidence_but_not_interval_csv(tmp_path, monkeypatch, candidate):
    builder = load_builder()
    monkeypatch.setattr(builder, 'ROOT', tmp_path)
    monkeypatch.setattr(builder.base, 'RUN', tmp_path / builder.base.RUN_REL)
    monkeypatch.setattr(builder.base, 'selected_paths', lambda: [])
    monkeypatch.setattr(builder, 'supplemental_rows', lambda: [])
    for name in ('inputs/energyplus/README_setup.txt', 'publication/requirements.txt'):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('fixture\n', encoding='utf-8')
    compact = ('V20_RESOURCES.csv', 'V20_EMISSIONS.csv', 'V18_CARRIER_SERVICE.csv',
               'V18_ECONOMICS.csv', 'GT_RUNS.csv', 'GT_TRANSITIONS.csv',
               'MODEL_DOMAIN.csv', 'THERMAL_COVERAGE.csv')
    for batch in ('v20_main_000', 'v20_main_001'):
        directory = builder.base.RUN / 'experiments' / batch / 'analysis'
        directory.mkdir(parents=True)
        for name in (*compact, 'ENGINEERING_INTERVALS.csv', 'EVENT_TIMELINE.csv'):
            (directory / name).write_text('fixture\n', encoding='utf-8')
    names, _ = builder.select(candidate)
    expected_batches = ('v20_main_000',) if candidate else ('v20_main_000', 'v20_main_001')
    for batch in expected_batches:
        for name in compact:
            expected = f'{builder.base.RUN_REL}/experiments/{batch}/analysis/{name}'
            assert expected in names, f'Compact evidence was omitted: {expected}'
    assert not any(name.endswith(('ENGINEERING_INTERVALS.csv', 'EVENT_TIMELINE.csv')) for name in names)
    if candidate:
        assert not any('/v20_main_001/' in name for name in names)


def test_final_build_refuses_incomplete_campaign_before_copying(tmp_path, monkeypatch):
    from publication import release_gate
    builder = load_builder()
    monkeypatch.setattr(builder, 'select', lambda candidate: ([], []))
    def incomplete(root):
        raise ValueError('Final campaign is not qualified: remaining pending pairs')
    monkeypatch.setattr(release_gate, 'qualify_final_campaign', incomplete)
    def forbidden_stage(destination):
        raise AssertionError('Copy started without final campaign qualification')
    monkeypatch.setattr(builder.base, 'stage', forbidden_stage)
    with pytest.raises(ValueError, match='not qualified'):
        builder.build(tmp_path / 'final')


def test_final_build_saves_actual_gate_proof_in_selected_tree(tmp_path, monkeypatch):
    from publication import release_gate
    import json
    builder = load_builder()
    proof = dict(status='passed', actual_complete_pairs=406, actual_complete_batches=148, evidence_files=[],
                 signature='f' * 64, gate_sha256='a' * 64)
    calls = []
    monkeypatch.setattr(release_gate, 'qualify_final_campaign', lambda root: calls.append(root) or proof)
    monkeypatch.setattr(builder, 'select', lambda candidate: ([], []))
    def empty_stage(destination):
        target = Path(destination)
        (target / 'publication').mkdir(parents=True)
        (target / '.gitignore').write_text('fixture\n', encoding='utf-8')
        (target / 'publication/V20_FILE_SELECTION.json').write_text('{}', encoding='utf-8')
    monkeypatch.setattr(builder.base, 'stage', empty_stage)
    target = tmp_path / 'final'
    assert builder.build(target) == 0
    assert calls == [builder.ROOT]
    proof_path = target / 'publication/V20_FINAL_CAMPAIGN_PROOF.json'
    assert proof_path.is_file(), 'Final publication did not preserve its actual qualification proof'
    assert json.loads(proof_path.read_text(encoding='utf-8-sig')) == proof
    marker = json.loads((target / 'publication/V20_RELEASE_ROOT.json').read_text(encoding='utf-8-sig'))
    assert marker['campaign_qualification']['sha256'] == builder.base.sha(proof_path)
    selection = json.loads((target / 'publication/V20_FILE_SELECTION.json').read_text(encoding='utf-8-sig'))
    assert any(r['path'] == 'publication/V20_FINAL_CAMPAIGN_PROOF.json' for r in selection['files'])


def test_candidate_build_does_not_claim_final_campaign_qualification(tmp_path, monkeypatch):
    from publication import release_gate
    builder = load_builder()
    monkeypatch.setattr(builder, 'select', lambda candidate: ([], []))
    def forbidden_gate(root):
        raise AssertionError('Candidate unexpectedly required a final campaign')
    monkeypatch.setattr(release_gate, 'qualify_final_campaign', forbidden_gate)
    def reached_stage(destination):
        raise RuntimeError('Candidate reached copy')
    monkeypatch.setattr(builder.base, 'stage', reached_stage)
    with pytest.raises(RuntimeError, match='Candidate reached copy'):
        builder.build(tmp_path / 'candidate', candidate=True)


@pytest.mark.parametrize('mutation', ['not_selected', 'changed_after_qualification'])
def test_final_copy_must_match_qualified_evidence_bytes(tmp_path, monkeypatch, mutation):
    from publication import release_gate
    import hashlib
    builder = load_builder()
    evidence = dict(path='qualified/RESULT.json', bytes=3,
                    sha256=hashlib.sha256(b'old').hexdigest())
    proof = dict(status='passed', actual_complete_pairs=406, actual_complete_batches=148,
                 signature='f' * 64, gate_sha256='a' * 64, evidence_files=[evidence])
    monkeypatch.setattr(release_gate, 'qualify_final_campaign', lambda root: proof)
    names = [] if mutation == 'not_selected' else [evidence['path']]
    monkeypatch.setattr(builder, 'select', lambda candidate: (names, []))
    def changed_stage(destination):
        target = Path(destination)
        (target / 'publication').mkdir(parents=True)
        (target / '.gitignore').write_text('fixture\n', encoding='utf-8')
        (target / 'publication/V20_FILE_SELECTION.json').write_text('{}', encoding='utf-8')
        path = target / evidence['path']
        path.parent.mkdir(parents=True)
        path.write_bytes(b'new')
    monkeypatch.setattr(builder.base, 'stage', changed_stage)
    with pytest.raises(ValueError, match='[Ee]vidence'):
        builder.build(tmp_path / 'final')
