"""One administrative regression; the actual native writer stays exclusive."""
import importlib
from pathlib import Path


def test_worker_preserves_native_result_and_writes_distinct_r2_sidecar(tmp_path, monkeypatch):
    path = Path(__file__).resolve().parents[1] / 'tools/recovery_steam_20261104/run_worker.py'
    assert path.is_file(), 'R2 sidecar-only worker is missing'
    worker = importlib.import_module('tools.recovery_steam_20261104.run_worker')
    from tools.recovery_steam_20261104.policy import R2_CONFIG
    root = tmp_path / 'hospital_ies_recovery_steam_20261104_test'
    root.mkdir()
    worker.write(root / 'REGISTRATION.json', dict(producers=[dict(label='T05_R2', variant='responsive',
        duration_minutes=180, baseline_label='T05_R')], sampling_minutes=dict(load=1., physical_max=1., control=1.),
        intervention=R2_CONFIG))
    worker.write(root / 'SOURCE_BINDING.json', dict(test_fixture_only=True))
    original_bytes = []

    def native_exclusive_writer(root, label, attempt=1):
        target = root / 'runs' / (label + '_attempt' + str(attempt).zfill(2))
        target.mkdir(parents=True)
        worker.write(target / 'RESULT.json', dict(label=label, variant='responsive', status='completed',
            expected_end_minute=180., observed_until_minute=180.,
            source_binding=dict(path=str(root / 'SOURCE_BINDING.json'), sha256=worker.sha(root / 'SOURCE_BINDING.json'))))
        original_bytes.append((target / 'RESULT.json').read_bytes())
        return 0

    monkeypatch.setattr(worker.native, 'run', native_exclusive_writer)
    assert worker.run(root, 'T05_R2', 1) == 0
    target = root / 'runs/T05_R2_attempt01'
    assert (target / 'RESULT.json').read_bytes() == original_bytes[0]
    sidecar = worker.read(target / 'WRAPPER_COMPLETION.json')
    assert sidecar['native_result']['sha256'] == worker.sha(target / 'RESULT.json')
    assert sidecar['intervention'] == R2_CONFIG
    assert sidecar['native_return_code'] == 0
    assert sidecar['process_exit_code_is_recorded_by_supervisor'] is True
