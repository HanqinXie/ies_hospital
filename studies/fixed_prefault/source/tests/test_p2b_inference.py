def test_release_inference_explicitly_restores_training_threads(monkeypatch,tmp_path):
    import torch
    from tools.p2b_experiment.inference import reload_registered_risk
    from tools.p2b_learning import risk
    original=torch.get_num_threads()
    try:
        torch.set_num_threads(3)
        monkeypatch.setattr(risk,'reload_risk_probabilities',lambda path,records: (torch.get_num_threads(),path,records))
        result=reload_registered_risk(tmp_path,[{'legal':'record'}])
        assert result==(2,tmp_path,[{'legal':'record'}])
        assert torch.get_num_threads()==3
    finally:
        torch.set_num_threads(original)
