"""Relocate exactly two historical dependencies of one frozen test module.

No test, assertion, tolerance, scientific function or other module is changed.
The original full pytest collection remains intact. Loading this plugin itself
does not import the scientific package or read historical producer contents.
"""
from pathlib import Path
import pytest

PACKAGE = Path(__file__).resolve().parents[1]
TARGET = 'source/tests/test_annual_observer_source_compatibility.py'
OLD_BASE = r'E:\hospital_ies_annual_program_20261030_run01'
OLD_SOURCE = r'D:\hospital_ies_annual_runtime_epoch09_20261030'
PRODUCERS = ('pilot14_epoch09_indexed_split', 'pilot16_epoch09_pending30_split')
MARKER = '_normal_year_dependency_path_migration'


def validate_fixture_layout(package_root):
    root = Path(package_root).resolve() / 'legacy_fixtures'
    required = [root / 'source_epoch09/tools/annual_program/witness.py']
    for name in PRODUCERS:
        directory = root / 'runs' / name
        required.extend(directory / filename for filename in ('BINDINGS.json', 'CASE.json', 'RESULT.json'))
        if not (directory / 'evidence').is_dir():
            raise ValueError('Missing legacy fixture evidence directory: ' + str(directory))
    for path in required:
        if not path.is_file():
            raise ValueError('Missing legacy fixture dependency: ' + str(path))
    return root


def migrate_module(module, package_root=None):
    package = Path(package_root if package_root is not None else PACKAGE).resolve()
    if module is None or Path(getattr(module, '__file__', '')).resolve() != (package / TARGET).resolve():
        return None
    previous = getattr(module, MARKER, None)
    if previous is not None:
        return previous
    # Validate both old producers and source before changing any module global.
    legacy = validate_fixture_layout(package)
    original_path = module.Path
    if module.BASE != original_path(OLD_BASE) or original_path is not Path:
        raise ValueError('Frozen legacy module dependency declarations unexpectedly differ')

    def relocated_path(*args, **kwargs):
        if len(args) == 1 and type(args[0]) is str and args[0] == OLD_SOURCE and not kwargs:
            return legacy / 'source_epoch09'
        return original_path(*args, **kwargs)

    record = dict(kind='dependency_path_migration_only', target_module=TARGET,
        producer_base=str(legacy), source_root=str(legacy / 'source_epoch09'),
        original_producer_base=OLD_BASE, original_source_literal=OLD_SOURCE,
        producers=list(PRODUCERS), assertions_modified=False, skipped_tests=0,
        scientific_functions_modified=False, other_modules_modified=False)
    module.BASE = legacy
    module.Path = relocated_path
    setattr(module, MARKER, record)
    return record


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item):
    # pytest's normal fixture setup occurs in a later implementation of this
    # hook, so the module-scoped old_actual_block fixture sees relocated paths.
    migrate_module(getattr(item, 'module', None), PACKAGE)
