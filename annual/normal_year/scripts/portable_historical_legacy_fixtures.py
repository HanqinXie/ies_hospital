"""Relocate the two real historical fixture dependencies in the 7fa child only.

Original tests, assertions, expected source identities and producer bytes remain
unchanged. This plugin never imports or executes a scientific function itself.
"""
import os
from pathlib import Path
import pytest

OLD_BASE=r'E:\hospital_ies_annual_program_20261030_run01'
OLD_SOURCE=r'D:\hospital_ies_annual_runtime_epoch09_20261030'
TARGET='tests/test_annual_observer_source_compatibility.py'
PRODUCERS=('pilot14_epoch09_indexed_split','pilot16_epoch09_pending30_split')
MARKER='_portable_historical_dependency_path_migration'


def validate_fixture_layout(package_root):
    legacy=Path(package_root).resolve()/'legacy_fixtures'
    required=[legacy/'source_epoch09/tools/annual_program/witness.py']
    for name in PRODUCERS:
        directory=legacy/'runs'/name
        required.extend(directory/filename for filename in ('BINDINGS.json','CASE.json','RESULT.json'))
        if not (directory/'evidence').is_dir():
            raise ValueError('Missing genuine historical fixture evidence: '+str(directory))
    for path in required:
        if not path.is_file():
            raise ValueError('Missing genuine historical fixture dependency: '+str(path))
    return legacy


def migrate_module(module,package_root=None,frozen_root=None):
    package=Path(package_root if package_root is not None else Path(__file__).resolve().parents[1]).resolve()
    frozen=Path(frozen_root if frozen_root is not None else package/'legacy_context_7fa').resolve()
    if module is None or Path(getattr(module,'__file__','')).resolve()!=(frozen/TARGET).resolve():
        return None
    previous=getattr(module,MARKER,None)
    if previous is not None:return previous
    legacy=validate_fixture_layout(package)
    original_path=module.Path
    if module.BASE!=original_path(OLD_BASE) or original_path is not Path:
        raise ValueError('Frozen historical dependency declarations unexpectedly differ')
    def relocated_path(*args,**kwargs):
        if len(args)==1 and type(args[0]) is str and args[0]==OLD_SOURCE and not kwargs:
            return legacy/'source_epoch09'
        return original_path(*args,**kwargs)
    record=dict(kind='dependency_path_migration_only',target_module=TARGET,
        producer_base=str(legacy),source_root=str(legacy/'source_epoch09'),
        original_producer_base=OLD_BASE,original_source_literal=OLD_SOURCE,
        producers=list(PRODUCERS),assertions_modified=False,skipped_tests=0,
        scientific_functions_modified=False,other_modules_modified=False)
    module.BASE=legacy
    module.Path=relocated_path
    setattr(module,MARKER,record)
    return record


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item):
    frozen=os.environ.get('IES_HISTORICAL_CONTEXT_ROOT')
    migrate_module(getattr(item,'module',None),frozen_root=frozen)
