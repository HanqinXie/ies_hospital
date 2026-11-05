"""Pure module/path migration tests; never load or audit real IES producers."""
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

PACKAGE = Path(__file__).resolve().parents[1]


def load_plugin():
    spec = importlib.util.spec_from_file_location('portable_legacy_fixtures_under_test',
        PACKAGE / 'scripts/portable_legacy_fixtures.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def fixture_package(tmp_path):
    package = tmp_path / 'moved package'
    legacy = package / 'legacy_fixtures'
    for name in ('pilot14_epoch09_indexed_split', 'pilot16_epoch09_pending30_split'):
        producer = legacy / 'runs' / name
        (producer / 'evidence').mkdir(parents=True)
        for filename in ('BINDINGS.json', 'CASE.json', 'RESULT.json'):
            (producer / filename).write_text('{}', 'utf8')
    source = legacy / 'source_epoch09'
    (source / 'tools/annual_program').mkdir(parents=True)
    (source / 'tools/annual_program/witness.py').write_text('# tiny fixture', 'utf8')
    target = package / 'source/tests/test_annual_observer_source_compatibility.py'
    target.parent.mkdir(parents=True)
    target.write_text('# frozen module identity only; never executed', 'utf8')
    return package


def target_module(package):
    return SimpleNamespace(__file__=str(package / 'source/tests/test_annual_observer_source_compatibility.py'),
        BASE=Path(r'E:\hospital_ies_annual_program_20261030_run01'), Path=Path,
        unchanged_assertion_token='untouched')


def test_exact_target_module_maps_two_dependencies_and_keeps_other_paths(fixture_package):
    plugin = load_plugin()
    package = fixture_package
    module = target_module(package)
    result = plugin.migrate_module(module, package)
    assert result['kind'] == 'dependency_path_migration_only'
    assert module.BASE == package / 'legacy_fixtures'
    assert module.Path(r'D:\hospital_ies_annual_runtime_epoch09_20261030') == package / 'legacy_fixtures/source_epoch09'
    assert module.Path('other', 'directory') == Path('other', 'directory')
    assert module.Path(r'D:\unrelated') == Path(r'D:\unrelated')
    assert module.Path(r'D:\hospital_ies_annual_runtime_epoch09_20261030\child') == Path(r'D:\hospital_ies_annual_runtime_epoch09_20261030\child')
    assert module.unchanged_assertion_token == 'untouched'
    original_factory = module.Path
    assert plugin.migrate_module(module, package) == result
    assert module.Path is original_factory
    assert Path is sys.modules['pathlib'].Path


def test_other_module_with_same_basename_or_path_literals_is_unchanged(fixture_package):
    plugin = load_plugin()
    package = fixture_package
    module = target_module(package)
    module.__file__ = str(package / 'elsewhere/test_annual_observer_source_compatibility.py')
    original_base, original_factory = module.BASE, module.Path
    assert plugin.migrate_module(module, package) is None
    assert module.BASE == original_base
    assert module.Path is original_factory


@pytest.mark.parametrize('missing', ['runs/pilot14_epoch09_indexed_split/BINDINGS.json',
    'runs/pilot16_epoch09_pending30_split/RESULT.json', 'source_epoch09/tools/annual_program/witness.py'])
def test_missing_legacy_fixture_refuses_before_modifying_module(fixture_package, missing):
    plugin = load_plugin()
    package = fixture_package
    (package / 'legacy_fixtures' / missing).unlink()
    module = target_module(package)
    original_base, original_factory = module.BASE, module.Path
    with pytest.raises(ValueError, match='legacy fixture'):
        plugin.migrate_module(module, package)
    assert module.BASE == original_base
    assert module.Path is original_factory


def test_pytest_setup_hook_migrates_only_target_before_fixture_execution(fixture_package, monkeypatch):
    plugin = load_plugin()
    monkeypatch.setattr(plugin, 'PACKAGE', fixture_package)
    module = target_module(fixture_package)
    plugin.pytest_runtest_setup(SimpleNamespace(module=module))
    assert module.BASE == fixture_package / 'legacy_fixtures'
    assert plugin.pytest_runtest_setup.pytest_impl['tryfirst'] is True


def test_suite_environment_records_explicit_plugin_without_changing_command():
    sys.path.insert(0, str(PACKAGE))
    spec = importlib.util.spec_from_file_location('server_validator_legacy_env_test', PACKAGE / 'scripts/server_validate.py')
    validator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(validator)
    source = PACKAGE / 'source'
    environment = validator.suite_environment(PACKAGE, source)
    # Current source owns its exact historical child routing. The old plugin's
    # behavior remains tested above, but must not be auto-loaded in this parent.
    assert environment['PYTEST_PLUGINS'] == ''
    assert environment['PYTEST_ADDOPTS'] == ''
    import os
    assert environment['PYTHONPATH'].split(os.pathsep) == [str(source), str(source / 'src'), str(PACKAGE / 'scripts')]


def test_internal_license_worker_rejects_wrong_host_before_solver_import(tmp_path, monkeypatch):
    sys.path.insert(0, str(PACKAGE))
    spec = importlib.util.spec_from_file_location('server_validator_internal_host_test', PACKAGE / 'scripts/server_validate.py')
    validator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(validator)
    monkeypatch.setattr(validator, 'current_host', lambda *_: dict(system='Windows', cpu_model='wrong CPU', ram_total_bytes=256_000_000_000))
    import builtins
    original_import = builtins.__import__
    def reject_solver(name, *args, **kwargs):
        if name == 'gurobipy':
            raise AssertionError('Unit test forbids solver import')
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', reject_solver)
    with pytest.raises(ValueError):
        validator.run_license_worker(SimpleNamespace(worker_folder=tmp_path, timeout_seconds=1))
