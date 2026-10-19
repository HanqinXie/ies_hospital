"""Read-only risk inference with the historical host-thread compatibility adapter.

The original P2B CPU weights used two intra-op threads; changing that count
could exceed their registered 1e-12 reload tolerance. This context retains the
host-thread contract for those weights. It does not select the device: v9 GRU
loading requires CUDA in the shared risk module, while RF remains on CPU.
The adapter does not refit, recalibrate, clip, or change thresholds.
"""
from contextlib import contextmanager


@contextmanager
def registered_torch_threads():
    import torch
    previous=torch.get_num_threads()
    torch.set_num_threads(2)
    try:
        yield
    finally:
        torch.set_num_threads(previous)


def reload_registered_risk(directory,raw_records):
    from tools.p2b_learning.risk import reload_risk_probabilities
    with registered_torch_threads():
        return reload_risk_probabilities(directory,raw_records)
