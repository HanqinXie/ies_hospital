"""Outcome-free v5 source registration, grouping, and final-background seal."""
from __future__ import annotations

from collections import Counter, defaultdict
import copy
import importlib
import json
from pathlib import Path

import pytest

from tools.control_improvement.data_design import CONDITIONS
from hospital_ies.dataset_manifest import digest


ROOT = Path(__file__).resolve().parents[1]
SEALED_FINAL_DAYS = (21, 26, 59, 77, 92, 127, 140, 196, 264, 284, 344, 359)


def design():
    return importlib.import_module("tools.policy_search.data_design")


def synthetic_proposal(*, days=range(200, 267), sealed=(21, 26, 59), excluded=()):
    return design().build_proposal_from_days(
        list(days), seed=20261016, sealed_final_days=list(sealed),
        excluded_support=list(excluded), workspace_root=ROOT,
    )


def registered_fixture():
    free_days = [day for day in range(200, 270) if day not in SEALED_FINAL_DAYS][:67]
    assert len(free_days) == 67
    blocked = set(range(365)) - set(free_days) - set(SEALED_FINAL_DAYS)
    excluded = [{"raw_start_minute": day * 1440,
                 "raw_end_minute": (day + 1) * 1440,
                 "purpose": "synthetic_test_exposure"}
                for day in sorted(blocked)]
    metadata = {"sealed_final_days": list(SEALED_FINAL_DAYS),
                "no_outcome_or_success_filter": True,
                "registry_meaning": "test fixture of attempted source support"}
    return excluded, metadata


def registered_proposal():
    excluded, metadata = registered_fixture()
    return design().build_proposal_from_days(
        range(365), seed=20261016,
        sealed_final_days=metadata["sealed_final_days"],
        excluded_support=excluded, workspace_root=ROOT,
        exposure_registry_metadata=metadata,
    )


def resign(proposal):
    body = copy.deepcopy(proposal)
    body.pop("signature", None)
    proposal["signature"] = digest(body)


def test_v5_proposal_is_deterministic_balanced_and_root_disjoint():
    proposal = synthetic_proposal()
    assert proposal == synthetic_proposal()
    assert design().validate_proposal(proposal, workspace_root=ROOT)
    assert len(proposal["blocks"]) == 54
    assert len(proposal["fixed_scenarios"]) == 270
    assert len(proposal["reserved_unselected_days"]) == 13
    assert Counter(block["role"] for block in proposal["blocks"]) == {
        "train": 30, "selection": 6, "calibration": 6, "development": 12,
    }
    per_condition = defaultdict(Counter)
    per_root = defaultdict(list)
    for block in proposal["blocks"]:
        per_condition[block["condition_id"]][block["role"]] += 1
    for scenario in proposal["fixed_scenarios"]:
        per_root[scenario["root_family_id"]].append(scenario)
        assert scenario["experiment_role"] in {
            "train", "selection", "calibration", "development"
        }
        assert scenario["is_final_blind"] is False
    assert set(per_condition) == set(CONDITIONS)
    assert all(counts == {"train": 5, "selection": 1,
                          "calibration": 1, "development": 2}
               for counts in per_condition.values())
    assert len(per_root) == 54
    assert all({s["pressure_point_index"] for s in rows} == set(range(5))
               and len({s["experiment_role"] for s in rows}) == 1
               for rows in per_root.values())
    blocks = {block["time_block_id"]: block for block in proposal["blocks"]}
    for scenario in proposal["fixed_scenarios"]:
        block = blocks[scenario["time_block_id"]]
        support = scenario["full_source_support"]
        assert support["raw_start_minute"] - 60 >= block["raw_start_minute"]
        assert support["raw_end_minute"] + 60 <= block["raw_end_minute"]


def test_v5_proposal_excludes_exposed_days_and_rejects_resigned_role_tamper():
    excluded = [{"raw_start_minute": 230 * 1440 + 10,
                 "raw_end_minute": 231 * 1440 + 10}]
    proposal = synthetic_proposal(days=range(200, 270), sealed=(210, 220), excluded=excluded)
    selected = {block["day_index"] for block in proposal["blocks"]}
    assert not selected & {210, 220, 230, 231}
    assert not set(proposal["reserved_unselected_days"]) & selected
    changed = copy.deepcopy(proposal)
    changed["fixed_scenarios"][0]["experiment_role"] = "development"
    resign(changed)
    with pytest.raises(ValueError, match="role|scenario|spec"):
        design().validate_proposal(changed, workspace_root=ROOT)


def test_v5_manifest_binds_proposal_bytes_roles_and_source(tmp_path):
    proposal = registered_proposal()
    path = tmp_path / "source_proposal.json"
    path.write_text(json.dumps(proposal, ensure_ascii=False), encoding="utf-8")
    manifest = design().build_manifest_from_proposal(
        ROOT, proposal, proposal_path=path
    )
    assert design().verify_manifest(ROOT, manifest)
    assert manifest["manifest_version"] == "policy_search.v5.new270.v1"
    assert len(manifest["scenarios"]) == len(manifest["splits"]) == 270
    assert manifest["policy_search_proposal"]["sha256"]
    assert "tools/policy_search/data_design.py" in manifest["policy_search_source_sha256"]
    assert Counter(split["experiment_role"]
                   for split in manifest["splits"].values()) == {
        "train": 150, "selection": 30, "calibration": 30,
        "development": 60,
    }
    assert not any(split["is_final_blind"] for split in manifest["splits"].values())
    changed = copy.deepcopy(manifest)
    changed["scenarios"][0]["grid_limit_kW"] += 1.0
    resign(changed)
    with pytest.raises(ValueError, match="scenario|proposal|manifest"):
        design().verify_manifest(ROOT, changed)


def test_v5_manifest_rejects_unregistered_or_resigned_contract(tmp_path):
    with pytest.raises(ValueError, match="registry|source"):
        design().build_manifest_from_proposal(ROOT, synthetic_proposal())
    proposal = registered_proposal()
    proposal["observation_contract"]["embargo_minutes_each_side"] = 0
    resign(proposal)
    with pytest.raises(ValueError, match="contract|embargo"):
        design().validate_proposal(proposal, workspace_root=ROOT)


def test_v5_freeze_preserves_first_attempt_and_rejects_overwrite(tmp_path, monkeypatch):
    monkeypatch.setattr(design(), "exposure_registry", lambda _root: registered_fixture())
    out = tmp_path / "v5_run"
    receipt = design().freeze(ROOT, out, seed=20261016)
    assert receipt["registered_source_roots"] == 54
    assert receipt["registered_specifications"] == 270
    assert receipt["final_12_backgrounds_unopened"] is True
    manifest = json.loads((out / "new_data" / "global_manifest.json").read_text(encoding="utf-8"))
    assert design().verify_manifest(ROOT, manifest)
    with pytest.raises(FileExistsError):
        design().freeze(ROOT, out, seed=20261016)
