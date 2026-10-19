from pathlib import Path
import hashlib
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]

def test_asset_verification_detects_missing_and_changed_bytes(tmp_path):
    from scripts.v20_repository import verify_rows
    p=tmp_path/'x.txt';p.write_bytes(b'original')
    rows=[dict(path='x.txt',bytes=8,sha256=hashlib.sha256(b'original').hexdigest())]
    assert verify_rows(tmp_path,rows)==[]
    p.write_bytes(b'changed!')
    assert verify_rows(tmp_path,rows)==['x.txt']
    p.unlink()
    assert verify_rows(tmp_path,rows)==['x.txt']

def test_verification_refuses_path_escape(tmp_path):
    import pytest
    from scripts.v20_repository import verify_rows
    with pytest.raises(ValueError):verify_rows(tmp_path,[dict(path='../outside',bytes=1,sha256='0'*64)])

def test_metric_comparison_preserves_fixed_tolerances_and_unknown():
    from scripts.v20_repository import compare_metrics
    expected={'energy.x':1.,'time.x':None,'fee.x':2.}
    assert all(x['passed'] for x in compare_metrics(expected,expected))
    actual=dict(expected);actual['energy.x']+=.01
    assert not all(x['passed'] for x in compare_metrics(actual,expected))
    actual=dict(expected);actual['time.x']=0.
    assert not all(x['passed'] for x in compare_metrics(actual,expected))
