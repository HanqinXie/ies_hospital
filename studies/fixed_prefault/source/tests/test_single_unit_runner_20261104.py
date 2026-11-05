"""Focused runner lifecycle guards; no trajectory or optimization is started."""
from pathlib import Path


def test_native_result_exclusive_writer_and_completion_sidecar(tmp_path, monkeypatch):
    from tools.cooling_dynamics_20261104 import run_worker as native
    from tools.cooling_dynamics_20261104.extract_results import read, sha, write
    from tools.single_unit_20261104 import run_worker as study

    case = tmp_path / 'case.json'
    write(case, {'id': 'probe', 'scenario': {}})
    write(tmp_path / 'SOURCE_BINDING.json', {'source_files': {}})
    write(tmp_path / 'REGISTRATION.json', {'cooling_config': {}, 'producers': [
        {'label': 'TOWER_U', 'variant': 'unattended', 'study_variant': 'U',
         'case_file': str(case), 'case_sha256': sha(case), 'duration_minutes': 180}]})
    monkeypatch.setattr(native, 'verify_binding', lambda root: {'source_files': {}})

    def fail_before_reset(*args):
        raise RuntimeError('intentional pre-reset exclusive-writer regression probe')

    monkeypatch.setattr(native, 'build_runtime', fail_before_reset)
    assert native.run(tmp_path, 'TOWER_U') == 1
    output = tmp_path / 'runs/TOWER_U_attempt01'
    original = (output / 'RESULT.json').read_bytes()
    receipt = study.write_completion(output, label='TOWER_U', native_exit_code=1,
                                     source_binding_sha256=sha(tmp_path / 'SOURCE_BINDING.json'))
    assert receipt['native_result']['sha256'] == sha(output / 'RESULT.json')
    assert (output / 'RESULT.json').read_bytes() == original
    assert read(output / 'RESULT.json')['status'] == 'failed'
    assert read(output / 'COMPLETION.json')['status'] == 'failed'
    import pytest
    with pytest.raises(FileExistsError):
        study.write_completion(output, label='TOWER_U', native_exit_code=1,
                               source_binding_sha256=sha(tmp_path / 'SOURCE_BINDING.json'))
    assert (output / 'RESULT.json').read_bytes() == original


def test_admission_requires_memory_and_real_worker_progress():
    from tools.single_unit_20261104.run_campaign import admitted_limit
    import pytest
    assert admitted_limit(6, 4, 20., [4.99] * 4) == 4
    assert admitted_limit(6, 4, 11.99, [5.] * 4) == 4
    assert admitted_limit(6, 4, 12., [5.] * 4) == 6
    assert admitted_limit(4, 4, 20., [5.] * 4) == 4
    with pytest.raises(ValueError):
        admitted_limit(7, 4, 20., [5.] * 4)
