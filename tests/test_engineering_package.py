"""Tiny package fixtures only; no production archive or physical replay."""
import hashlib
import importlib
import json
from pathlib import Path
import zipfile

import pytest


def api():
    try:
        return importlib.import_module('tools.engineering_reporting.package')
    except ModuleNotFoundError:
        pytest.fail('Independent engineering light-package helper is missing')


def put(root, relative, text):
    path = root/relative; path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf8'); return path


def fixture(a, root):
    run = root/'outputs/v14_test'
    put(root, 'src/hospital_ies/__init__.py', '')
    put(root, 'tools/engineering_model/__init__.py', '')
    put(root, 'tools/engineering_model/runner.py', 'from tools.shared import helper\n')
    helper = put(root, 'tools/shared.py', 'helper = 1\n')
    put(root, 'tools/unused.py', '# not an actual dependency\n')
    put(root, 'requirements.txt', 'numpy>=1.24\n')
    for relative in a.PHYSICAL_INPUTS:
        put(root, relative, '{}\n' if relative.endswith('.json') else 'tiny source bytes\n')
    for relative in a.METADATA_DEPENDENCIES:
        put(root, relative, '{}\n')
    plan = dict(phase='development', model_id='M0', model_config={}, policy_specs=[{'policy_id': 'F'}, {'policy_id': 'S'}],
        scenario_ids=['s'], registered_pairs=2, source_sha256={'tools/shared.py': a.sha(helper)})
    batch = run/'experiments/one'; batch.mkdir(parents=True)
    put(root, 'outputs/v14_test/experiments/one/plan.json', json.dumps(plan))
    put(root, 'outputs/v14_test/experiments/one/manifest.json', json.dumps({'scenarios': [{'id':'s', 'analysis_class':'N2'}]}))
    put(root, 'outputs/v14_test/experiments/one/BATCH_RESULT.json', '{}')
    rows = []; selected=[]
    for policy in ('F', 'S'):
        relative = f'outputs/v14_test/experiments/one/pairs/s__{policy}/trajectory.json.gz'
        path = root/relative; path.parent.mkdir(parents=True); path.write_bytes(b'compressed fixture '+policy.encode())
        record = dict(scenario_id='s', policy_id=policy, model_id='M0', raw_path=str(path), raw_sha256=a.sha(path),
            status='completed', analysis={'windows': {}})
        put(root, relative.replace('trajectory.json.gz', 'RESULT.json'), json.dumps(record))
        rows.append(record); selected.append(dict(batch='one', scenario_id='s', policy_id=policy, reason='root matched fixture'))
    put(root, 'outputs/v14_test/experiments/one/analysis/RESULTS.json', json.dumps({'records':rows}))
    put(root, 'outputs/v14_test/design_v2/DEVELOPMENT_PROPOSAL.json', '{"scenarios": [{"id":"s"}]}')
    report = put(root, 'outputs/v14_test/ENGINEERING_MODEL_REPORT.md', '# Fixture report\n')
    selection = put(root, 'outputs/v14_test/review/RAW_SELECTION.json', json.dumps(dict(schema=a.SELECTION_SCHEMA,
        status='final', selected_by='root', all_batches_stopped=True, raw_examples=selected)))
    return run, selection, report


def test_source_closure_uses_mature_inventory_but_retains_actual_dependencies_only(tmp_path):
    a = api(); fixture(a, tmp_path)
    sources = a.source_closure(tmp_path, ['tools/engineering_model/runner.py'])
    assert 'tools/engineering_model/runner.py' in sources and 'tools/shared.py' in sources
    assert 'tools/unused.py' not in sources


def test_final_selection_is_required_and_unpaired_examples_are_rejected(tmp_path):
    a = api(); run, selection, report = fixture(a, tmp_path)
    value = json.loads(selection.read_text(encoding='utf8')); value['status'] = 'draft'
    selection.write_text(json.dumps(value), encoding='utf8')
    with pytest.raises(ValueError, match='final'):
        a.prepare(tmp_path, run, selection, reports=[report])
    value['status'] = 'final'; value['raw_examples'].pop(); selection.write_text(json.dumps(value), encoding='utf8')
    with pytest.raises(ValueError, match='matched'):
        a.prepare(tmp_path, run, selection, reports=[report])


def test_inventory_keeps_inputs_and_selected_raw_without_copying_other_outputs(tmp_path, monkeypatch):
    a = api(); run, selection, report = fixture(a, tmp_path)
    unselected = run/'experiments/one/pairs/unselected/trajectory.json.gz'
    unselected.parent.mkdir(); unselected.write_bytes(b'must never be copied or hashed')
    put(tmp_path, 'outputs/old_run/huge.json', '{}')
    put(tmp_path, 'outputs/v14_test/temp/unneeded.json', '{}')
    put(tmp_path, 'outputs/v14_test/PAPER_RESULTS.md', 'not in scope')
    original = Path.open
    def guarded(path, *args, **kwargs):
        if path == unselected:
            pytest.fail('Unselected raw was read')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', guarded)
    prepared = a.prepare(tmp_path, run, selection, reports=[report])
    names = set(prepared['entries'])
    assert set(a.PHYSICAL_INPUTS) <= names
    assert len([name for name in names if name.endswith('trajectory.json.gz')]) == 2
    assert not any('old_run/huge' in name or '/temp/' in name or 'PAPER_RESULTS' in name for name in names)
    assert 'README_REPLAY.md' in names and 'DEPENDENCIES.json' in names
    assert 'tools/shared.py' in names


def test_tiny_archive_checks_hashes_and_preserves_original_workspace_hierarchy(tmp_path):
    a = api(); run, selection, report = fixture(a, tmp_path)
    archive = tmp_path/'delivery/review.zip'
    receipt = a.build(tmp_path, run, selection, archive, reports=[report])
    assert receipt['selected_raw_count']==2 and receipt['physical_reproduction_performed'] is False
    assert a.verify(archive)['verified'] is True
    with zipfile.ZipFile(archive) as bundle:
        assert 'tools/engineering_model/runner.py' in bundle.namelist()
        text = bundle.read('README_REPLAY.md').decode('utf8')
        assert 'runner register' in text and 'runner run' in text
        assert '--directory' in text and 'fresh subcommand' not in text
        assert '--policies F,E' in text
        assert 'gurobi' in text.lower() and 'license' in text.lower()
    with pytest.raises(FileExistsError):
        a.build(tmp_path, run, selection, archive, reports=[report])


def test_path_escape_in_dependency_or_selection_cannot_enter_archive(tmp_path):
    a = api(); run, selection, report = fixture(a, tmp_path)
    value = json.loads(selection.read_text(encoding='utf8'))
    value['raw_examples'][0]['batch'] = '../outside'
    selection.write_text(json.dumps(value), encoding='utf8')
    with pytest.raises(ValueError):
        a.prepare(tmp_path, run, selection, reports=[report])


def test_replay_readme_uses_proposal_containing_explicit_selected_scenario(tmp_path):
    a = api(); run, selection, report = fixture(a, tmp_path)
    put(tmp_path, 'outputs/v14_test/design_v2/DEVELOPMENT_PROPOSAL.json', '{"scenarios": [{"id":"different"}]}')
    put(tmp_path, 'outputs/v14_test/design_v2/LONG_NORMAL_PROPOSAL.json', '{"scenarios": [{"id":"s"}]}')
    prepared = a.prepare(tmp_path, run, selection, reports=[report])
    text = prepared['entries']['README_REPLAY.md']['source'].decode('utf8')
    assert '--proposal "outputs/v14_test/design_v2/LONG_NORMAL_PROPOSAL.json"' in text
