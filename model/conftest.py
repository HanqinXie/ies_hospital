"""Exact historical-profile context routing for this minute worktree only.

No old assertion is changed or skipped. The explicit 323-node manifest routes
those original tests to the real frozen-source subprocess wrapper. All other
collected tests retain their current-worktree context.
"""
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import sys

import pytest

ROOT=Path(__file__).resolve().parent
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
MANIFEST=ROOT/'tests/minute1_historical_routes.json'
MANIFEST_SHA256='cfa563fe9518d66e4e54427b130120873985ecdf57ce8f84bb28a86ce473391a'


def historical_path(value):
    """Resolve a source-relative dependency; never borrow a packing-machine root."""
    path=Path(value)
    if path.is_absolute() or PureWindowsPath(value).is_absolute():
        raise ValueError('Historical dependency must be package-relative: '+str(value))
    return (ROOT/path).resolve()


def historical_output_root(value):
    work=os.environ.get('IES_HISTORICAL_WORK_ROOT')
    return (Path(work).resolve()/'historical_profiles') if work else historical_path(value['output_root'])


def historical_manifest():
    raw=MANIFEST.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=MANIFEST_SHA256:
        raise pytest.UsageError('Historical route manifest differs from the source-pinned identity')
    value=json.loads(raw)
    if ROOT!=historical_path(value['active_root']):
        raise pytest.UsageError('Historical route hook active root differs from its package source')
    return value


def pytest_collection_modifyitems(config,items):
    value=historical_manifest()
    routes={r['nodeid'] for r in value['routes']}
    if len(routes)!=value['expected_nodes'] or len(routes)!=323:
        raise pytest.UsageError('Historical route manifest node count or uniqueness drifted')
    for module,pin in value['original_test_sha256'].items():
        if hashlib.sha256((ROOT/module).read_bytes()).hexdigest()!=pin:
            raise pytest.UsageError('Original historical test bytes changed: '+module)
    selected={item.nodeid for item in items}
    routed=selected&routes
    wrapper='tests/test_minute1_historical_profiles.py::test_frozen_historical_module'
    has_wrapper=any(item.nodeid.startswith(wrapper+'[') for item in items)
    if routed and not has_wrapper:
        raise pytest.UsageError('Historical identity nodes need their real frozen context; include '
            'tests/test_minute1_historical_profiles.py or run unchanged tests in the frozen 7fa root')
    if routed:
        # Validate all original IDs for every selected module; a renamed or
        # removed parameter cannot silently disappear from the route.
        modules={node.split('::',1)[0] for node in selected}
        required={node for node in routes if node.split('::',1)[0] in modules}
        if routed!=required:
            raise pytest.UsageError('Historical route collection is partial or drifted; use full modules with wrapper')
        items[:]=[item for item in items if item.nodeid not in routes]
    config._minute1_historical_routed=sorted(routed)


def pytest_terminal_summary(terminalreporter,exitstatus,config):
    routed=getattr(config,'_minute1_historical_routed',[])
    if routed:
        terminalreporter.write_sep('-',f'{len(routed)} original historical identity nodes routed to verified 7fa subprocesses; see wrapper results')
