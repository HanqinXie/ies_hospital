"""Synthetic accounting fixtures; no hospital trajectories are generated."""
from pathlib import Path
import sys
import numpy as np
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def segment(a,b,power=10,failed=False):
    return dict(start_minute=a,end_minute=b,unserved_kW=np.full((4,3),power).tolist(),
        cost_USD_step=(b-a)*2,classification={'p0_guarantee_failure':failed},
        energy_balance_residuals=[0,0,0],steam_balance_residual_lbph=0,
        temperature={'P0':23,'P1':23,'P2':23,'P3':23},
        violation={'P0':False,'P1':False,'P2':False,'P3':False})


def test_score_clips_segments_excludes_history_and_tail():
    from tools.p2b_experiment.audit import score_payload
    p=dict(summary={'status':'completed','observed_until_minute':480},
           segments=[segment(0,90),segment(90,450,20),segment(450,480)])
    r=score_payload(p)
    assert r['P0_e_ENS_kWh']==pytest.approx(10*.5+20*5.5)
    assert r['cost_USD']==pytest.approx(720)
    assert r['score_outcome_valid'] and r['score_P0_failure']==0


def test_incomplete_score_stays_unknown_not_zero():
    from tools.p2b_experiment.audit import score_payload
    r=score_payload(dict(summary={'status':'model_constraint_infeasible','observed_until_minute':75},segments=[segment(0,75,0)]))
    assert r['P0_e_ENS_kWh'] is None
    assert r['observed_P0_e_ENS_kWh']==0
    assert r['score_P0_failure'] is None
    assert not r['score_outcome_valid']


def test_later_error_does_not_erase_completed_score():
    from tools.p2b_experiment.audit import score_payload
    r=score_payload(dict(summary={'status':'solver_error','observed_until_minute':450},segments=[segment(0,450,0)]))
    assert r['score_outcome_valid'] and r['P0_e_ENS_kWh']==0


def test_pair_unknown_never_counts_as_policy_benefit():
    from tools.p2b_experiment.audit import pair_category
    a={'score_outcome_valid':True,'score_P0_failure':1}
    b={'score_outcome_valid':False,'score_P0_failure':None}
    assert pair_category(a,b)=='any_unknown'


def test_integrity_rejects_time_gap_and_false_negative_label():
    from tools.p2b_experiment.audit import check_payload
    p={'segments':[segment(0,10),segment(15,30)],'decisions':[]}
    with pytest.raises(AssertionError):check_payload(p)
    p={'segments':[segment(0,15)],'decisions':[dict(decision_time_min=0,
        feature_metadata={},known_command_queue=[],labels={'60':{'value':0,'valid':True}})]}
    with pytest.raises(AssertionError):check_payload(p)
