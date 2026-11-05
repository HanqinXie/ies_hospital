"""Synthetic checks that the existing GRU classes use CUDA in v9 source."""
from __future__ import annotations

import hashlib
import importlib
import json
from pathlib import Path

import numpy as np
import pytest
import torch

from tools.p2b_learning.risk import SequencePreprocessor


def candidate(package: str, filename: str):
    return importlib.import_module(f'tools.{package}.{Path(filename).stem}')


def observed_gru_factory(original, calls):
    def create(*args, **kwargs):
        model = original(*args, **kwargs)

        def record(module, inputs):
            calls.append((next(module.parameters()).device.type, inputs[0].device.type))

        model.register_forward_pre_hook(record)
        return model

    return create


def rows(n):
    return [
        {'sequence': [{'load': float(i + j) / 5} for j in range(4)], 'padding_mask': [1, 1, 1, 1]}
        for i in range(n)
    ]


def arrays():
    train = rows(12)
    valid = rows(6)
    pre = SequencePreprocessor().fit(train)
    x = pre.transform(train)
    vx = pre.transform(valid)
    return train, valid, pre, x, vx


def test_bc_existing_gru_fit_reload_and_predict_run_on_cuda(tmp_path, monkeypatch):
    module = candidate('integrated_learning', 'models.py')
    calls = []
    monkeypatch.setattr(module, 'make_gru', observed_gru_factory(module.make_gru, calls))
    _, _, pre, x, vx = arrays()
    y = np.ones((len(x), 6), dtype=float)
    vy = np.ones((len(vx), 6), dtype=float)
    folder = tmp_path / 'bc'
    result = module.fit_candidate(
        {'class': 'GRU', 'id': 'synthetic_existing_BC_GRU', 'hidden_size': 2, 'epochs': 1, 'patience': 1},
        4101, x, y, vx, vy, folder, pre.to_dict(), np.ones(6),
    )
    predictor = module.load_predictor(folder)
    prediction = predictor.predict_encoded(vx)
    assert np.all(np.isfinite(prediction)) and prediction.shape == (len(vx), 6)
    assert result['reload_max_abs_diff_kW'] == 0
    assert calls and set(calls) == {('cuda', 'cuda')}
    assert next(predictor.model.parameters()).device.type == 'cuda'
    with monkeypatch.context() as m:
        m.setattr(torch.cuda, 'is_available', lambda: False)
        with pytest.raises(RuntimeError, match='CUDA'):
            module.load_predictor(folder)
        with pytest.raises(RuntimeError, match='CUDA'):
            module.fit_candidate(
                {'class': 'GRU', 'id': 'synthetic_existing_BC_GRU', 'hidden_size': 2, 'epochs': 1, 'patience': 1},
                4101, x, y, vx, vy, tmp_path / 'bc_no_cuda', pre.to_dict(), np.ones(6),
            )


def test_risk_existing_gru_fit_and_raw_reload_run_on_cuda(tmp_path, monkeypatch):
    module = candidate('p2b_learning', 'risk.py')
    calls = []
    monkeypatch.setattr(module, 'make_gru', observed_gru_factory(module.make_gru, calls))
    _, valid, pre, x, vx = arrays()
    y = np.asarray([i % 2 for i in range(len(x))], dtype=int)
    vy = np.asarray([i % 2 for i in range(len(vx))], dtype=int)
    config = {'class': 'GRU', 'hidden_size': 2, 'epochs': 1, 'patience': 1}
    folder = tmp_path / 'risk'
    pred, diag = module.fit_risk_candidate(config, 4101, x, y, vx, vy, folder)
    pre_path = tmp_path / 'preprocessor.json'
    pre_path.write_text(json.dumps(pre.to_dict()), encoding='utf-8')
    provenance = {
        'config': config, 'input_size': x.shape[-1],
        'preprocessor_path': '../preprocessor.json',
        'preprocessor_sha256': hashlib.sha256(pre_path.read_bytes()).hexdigest(),
        'files': [{'path': 'state_dict.pt', 'sha256': hashlib.sha256((folder / 'state_dict.pt').read_bytes()).hexdigest()}],
    }
    (folder / 'provenance.json').write_text(json.dumps(provenance), encoding='utf-8')
    reloaded = module.reload_risk_probabilities(folder, valid)
    assert pred.shape == reloaded.shape == (len(valid),)
    assert np.all(np.isfinite(reloaded))
    assert np.array_equal(pred, reloaded)
    assert diag['serialization_max_abs_error'] == 0
    assert calls and set(calls) == {('cuda', 'cuda')}
    with monkeypatch.context() as m:
        m.setattr(torch.cuda, 'is_available', lambda: False)
        with pytest.raises(RuntimeError, match='CUDA'):
            module.reload_risk_probabilities(folder, valid)
        with pytest.raises(RuntimeError, match='CUDA'):
            module.fit_risk_candidate(config, 4101, x, y, vx, vy, tmp_path / 'risk_no_cuda')


def test_legacy_offline_risk_gru_reload_uses_cuda_too(tmp_path, monkeypatch):
    module = candidate('integrated_experiment', 'offline.py')
    calls = []
    monkeypatch.setattr(module, 'make_gru', observed_gru_factory(module.make_gru, calls))
    _, _, _, x, _ = arrays()
    model = module.make_gru(x.shape[-1], 2)
    torch.save(model.state_dict(), tmp_path / 'state_dict.pt')
    (tmp_path / 'model_descriptor.json').write_text(json.dumps({
        'algorithm': 'GRU', 'input_size': x.shape[-1], 'hidden_size': 2,
    }), encoding='utf-8')
    result = module.reload_model_probabilities(tmp_path, x)
    assert np.all(np.isfinite(result)) and result.shape == (len(x),)
    assert calls and set(calls) == {('cuda', 'cuda')}
    with monkeypatch.context() as m:
        m.setattr(torch.cuda, 'is_available', lambda: False)
        with pytest.raises(RuntimeError, match='CUDA'):
            module.reload_model_probabilities(tmp_path, x)
