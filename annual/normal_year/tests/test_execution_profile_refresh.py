"""New pure orchestration regressions; never import the IES runtime."""
import importlib.util
import json
from pathlib import Path
import sys
import pytest

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE))
MODEL = 'annual_responsive_dynamic_bridge_20261104.v1'
OWNER = 'hospital_ies.annual_dynamic.accepted_engineering_owner.v1'


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, PACKAGE / relative)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def test_normal_counts_are_configurable_with_a_finite_total():
    runner = load('refresh_runner', 'server_runner.py')
    limits = dict(max_producers=2,max_accounts=1,max_total_processes=3,threads_per_producer=1,
        max_recoveries_global=1,max_wall_seconds=100,rss_limit_bytes=1,min_available_ram_bytes=1,
        min_free_disk_bytes=1,initial_free_disk_required_bytes=1,grace_seconds=1)
    assert runner.validate_limits(limits)['max_producers'] == 2
    assert runner.validate_limits(dict(limits,max_producers=3,max_accounts=2))['max_producers']==3
    for field, value in [('max_producers',0),('max_accounts',0)]:
        with pytest.raises(ValueError): runner.validate_limits(dict(limits, **{field:value}))


def test_old_completed_producer_cannot_qualify_new_execution(tmp_path, monkeypatch):
    validator = load('refresh_validator', 'scripts/server_validate.py')
    monkeypatch.setattr(validator, 'verify_package', lambda _: {'source_tree_sha256':'same'})
    tmp_path.joinpath('RESULT.json').write_text(json.dumps(dict(status='completed',observed_until_minute=60.,
        reset_count=1,source_and_inputs_unchanged=True,source_sha256='same')))
    with pytest.raises(ValueError): validator._completed(tmp_path,60.)


def test_new_comparison_schema_is_required():
    helper = load('refresh_execution_helper', 'scripts/execution_profile.py')
    with pytest.raises(ValueError): helper.require_match(dict(status='exact_match',difference_count=0,
        maximum_absolute_numeric_difference=0.,physical_elapsed_fields_retained=True))
