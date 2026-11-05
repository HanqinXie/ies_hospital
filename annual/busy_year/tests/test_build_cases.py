import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


PACKAGE = Path(__file__).resolve().parents[1]


def implementation():
    path = PACKAGE / 'build_cases.py'
    if not path.is_file():
        pytest.fail('The disturbed-year canonical case builder is missing', pytrace=False)
    spec = importlib.util.spec_from_file_location('busy_case_builder_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_refuses_missing_reference_before_creating_frozen_output(tmp_path):
    module = implementation()
    output = tmp_path / 'frozen'
    with pytest.raises((ValueError, FileNotFoundError)):
        module.freeze(tmp_path / 'missing_reference', output)
    assert not output.exists()


def test_candidate_cases_are_explicitly_unresolved_and_not_admitted(tmp_path):
    module = implementation()
    index = module.write_candidates(tmp_path / 'candidates')
    assert index['calendar_state'] == 'candidate_requires_normal_reference'
    assert index['annual_admitted'] is False
    assert len(index['cases']) == 3
    assert {row['method'] for row in index['cases']} == {'M_available', 'M_ready', 'C_ready'}
    values = [json.loads((tmp_path / 'candidates' / row['path']).read_text('utf8'))
              for row in index['cases']]
    parameters = [case['annual_program']['parameters'] for case in values]
    assert all(row['mode'] == 'FD' and row['duration_minutes'] == 525600 for row in parameters)
    assert all(row['demand_start_minute'] == 280800 for row in parameters)
    assert all(row['demand_duration_minutes'] == 40320 for row in parameters)
    assert all(row['calendar'] == parameters[0]['calendar'] for row in parameters)
    assert len({case['scenario']['exogenous_stream_id'] for case in values}) == 1
    assert len({case['id'] for case in values}) == 3


def test_candidate_builder_never_overwrites_existing_destination(tmp_path):
    module = implementation()
    output = tmp_path / 'existing'
    output.mkdir()
    marker = output / 'marker'
    marker.write_text('preserve', encoding='utf8')
    with pytest.raises((ValueError, FileExistsError)):
        module.write_candidates(output)
    assert marker.read_text('utf8') == 'preserve'


def test_frozen_output_cannot_be_inside_immutable_package():
    module = implementation()
    with pytest.raises(ValueError, match='package'):
        module.external_destination(PACKAGE / 'unintended_output')


def test_frozen_output_cannot_mutate_original_reference_tree(tmp_path, monkeypatch):
    import calendar_plan
    module = implementation()
    reference = tmp_path / 'original_normal'
    reference.mkdir()
    output = reference / 'unintended_new_files'
    def unexpected_read(_):
        raise AssertionError('Output overlap must be rejected before reference parsing')
    monkeypatch.setattr(calendar_plan, 'resolve_plan', unexpected_read)
    with pytest.raises(ValueError, match='overlap'):
        module.freeze(reference, output)
    assert list(reference.iterdir()) == []


def test_builder_bootstrap_prevents_later_scientific_bytecode_writes(tmp_path):
    toy = tmp_path / 'source_fixture'
    toy.mkdir()
    (toy / 'scientific_fixture.py').write_text('VALUE = 1\n', encoding='utf8')
    code = ('import json,runpy,sys;runpy.run_path(sys.argv[1],run_name="bootstrap_test");'
            'sys.path.insert(0,sys.argv[2]);import scientific_fixture;'
            'print(json.dumps({"no_bytecode":sys.dont_write_bytecode}))')
    environment = dict(os.environ)
    environment.pop('PYTHONDONTWRITEBYTECODE', None)
    result = subprocess.run([sys.executable, '-c', code, str(PACKAGE / 'build_cases.py'), str(toy)],
        cwd=tmp_path, env=environment, text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout.strip().splitlines()[-1])['no_bytecode'] is True
    assert not (toy / '__pycache__').exists()
