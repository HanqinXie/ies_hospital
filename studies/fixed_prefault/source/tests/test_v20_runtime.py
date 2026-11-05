"""V20 execution identities, original-input scope and immutable registration."""
from pathlib import Path
import copy
import json
from types import SimpleNamespace

import pytest

from tools.load_accounting import runner as r
from tools.load_accounting import data_design as d
from tools.load_accounting import environment as e
from tools.load_accounting import execution as x

ROOT = Path(__file__).resolve().parents[1]
V19 = ROOT / "outputs/v19_model_closeout_20261018_run01"


def original_proposal(name="MAIN_FINAL_R1_PROPOSAL.json"):
    return json.loads((V19 / "design" / name).read_text(encoding="utf8"))


def test_new_model_and_policy_preferences_are_explicit():
    from tools.model_closeout.runner import policy_specs as old_specs
    assert r.MODEL_ID == "M20_load_accounting"
    assert "v20_load_accounting" in r.RUN.as_posix()
    specs = r.policy_specs()
    assert set(specs) == {"E", "K1", "C19", "C18_M20"}
    for alias, old_alias in (("E", "E"), ("K1", "K1"), ("C19", "C19"), ("C18_M20", "C18_M19")):
        spec = specs[alias]
        assert "M20" in spec["policy_id"]
        assert spec["config"] == old_specs()[old_alias]["config"]
        assert spec["preference_config_sha256"] == d.digest(spec["config"])
        assert r.factory(spec).policy_id == spec["policy_id"]
        changed = copy.deepcopy(spec)
        changed["config"]["not_authorized"] = True
        with pytest.raises(ValueError):
            r.factory(changed)


def test_config_resolver_rejects_v19_but_preserves_physical_parameters():
    from tools.model_closeout.environment import model_contract as old_contract
    config = json.loads((V19 / "M19_MODEL_CONFIG.json").read_text(encoding="utf8"))
    old, resolved_old = old_contract("M19_model_closeout", config)
    contract, resolved = e.model_contract(r.MODEL_ID, config)
    assert resolved == resolved_old
    assert contract["name"] != old["name"]
    assert contract["load_accounting_contract_id"] == d.LOAD_ACCOUNTING_CONTRACT_ID
    with pytest.raises(ValueError):
        e.model_contract("M19_model_closeout", config)


def test_fixed_background_is_not_new_independent_confirmation():
    original = original_proposal("CONFIRMATION_FINAL_R1_PROPOSAL.json")
    panel = d.panel("fixed_background", original["scenarios"], evaluation_role="seen_fixed_background")
    assert panel["new_independent_background_count"] == 0
    assert panel["confirmation_background_count"] == 0
    assert panel["fixed_background_count"] == 8
    assert panel["original_final12_evaluated"] is False
    assert len(panel["scenarios"]) == 32
    for before, after in zip(original["scenarios"], panel["scenarios"]):
        assert after["physical_scenario_signature"] == before["physical_scenario_signature"]
        assert after["full_source_support"] == before["full_source_support"]
        assert after["new_independent_background"] is False
        assert after["fit_allowed"] is False
        assert after["selection_allowed"] is False
    with pytest.raises(ValueError):
        d.panel("incorrect", original["scenarios"], evaluation_role="independent_confirmation")


def test_runtime_inventory_excludes_unread_external_literature():
    files = d.runtime_input_inventory(ROOT)
    names = {row["path"] for row in files}
    assert {"inputs/loads/critical_zones_8760.csv", "configs/plant_configs.json", "inputs/reference/export_summary.json"} <= names
    assert sum(name.startswith("inputs/loads/") for name in names) == 4
    assert not any(name.endswith(".pdf") or name.startswith("inputs/literature/") for name in names)
    assert all(len(row["sha256"]) == 64 for row in files)


def test_manifest_has_own_input_and_load_contract():
    original = original_proposal()
    proposal = d.panel("probe", original["scenarios"][:1], evaluation_role="seen_regression")
    config = json.loads((V19 / "M19_MODEL_CONFIG.json").read_text(encoding="utf8"))
    manifest = d.build_manifest(proposal, {"probe.py": "a" * 64}, r.MODEL_ID, config, root=ROOT,
                                policies=[r.policy_specs()["E"]["policy_id"]])
    assert manifest["execution_model_id"] == r.MODEL_ID
    assert manifest["load_accounting_contract_id"] == d.LOAD_ACCOUNTING_CONTRACT_ID
    assert manifest["files"] == d.runtime_input_inventory(ROOT)
    assert len(manifest["splits"]) == 1
    assert not any("literature" in row["path"] for row in manifest["files"])
    assert manifest["source_sha256"] == {"probe.py": "a" * 64}


def test_freeze_rejects_changed_policy_input_or_source():
    binding = {"probe.py": "a" * 64}
    config = {"example": True}
    specs = [r.policy_specs()["E"]]
    inventory = [{"path": "inputs/loads/probe.csv", "sha256": "b" * 64, "bytes": 1}]
    frozen = dict(kind="v20_final_shared_model_freeze", source_sha256=binding, model_id=r.MODEL_ID,
                  model_config=config, policies={"E": specs[0]}, runtime_inputs=inventory)
    frozen["signature"] = d.digest(frozen)
    r.validate_final_freeze(frozen, binding, r.MODEL_ID, config, specs, runtime_inputs=inventory)
    with pytest.raises(ValueError):
        r.validate_final_freeze(frozen, binding, r.MODEL_ID, config, specs,
                                runtime_inputs=[dict(inventory[0], sha256="c" * 64)])
    with pytest.raises(ValueError):
        r.validate_final_freeze(frozen, {"probe.py": "d" * 64}, r.MODEL_ID, config, specs,
                                runtime_inputs=inventory)
    with pytest.raises(ValueError):
        r.validate_final_freeze(frozen, binding, r.MODEL_ID, config,
                                [dict(specs[0], policy_id="other_M20")], runtime_inputs=inventory)


def test_single_scene_registration_rebinds_subset_proposal(tmp_path, monkeypatch):
    binding = r.source_binding()
    monkeypatch.setattr(r, "source_binding", lambda: binding)
    monkeypatch.setattr(r, "RUN", tmp_path / "v20")
    monkeypatch.setattr(r, "check_output_path", lambda value: Path(value).resolve())
    monkeypatch.setattr(r, "_snapshot", lambda binding: tmp_path / "source_snapshot")
    original = original_proposal()
    proposal = d.panel("two", original["scenarios"][:2], evaluation_role="development")
    config = json.loads((V19 / "M19_MODEL_CONFIG.json").read_text(encoding="utf8"))
    plan = r.register(proposal, r.RUN / "experiments" / "one", [r.policy_specs()["E"]],
                      model_id=r.MODEL_ID, model_config=config,
                      scenario_ids=[proposal["scenarios"][1]["id"]])
    assert plan["registered_pairs"] == 1
    saved = r.read(r.RUN / "experiments" / "one" / "manifest.json")
    assert len(saved["scenarios"]) == 1
    assert saved["scenarios"][0]["id"] == proposal["scenarios"][1]["id"]
    tampered = copy.deepcopy(proposal)
    tampered["scenarios"][0]["duration_steps"] += 1
    with pytest.raises(ValueError, match="Proposal changed"):
        r.register(tampered, r.RUN / "experiments" / "tampered", [r.policy_specs()["E"]],
                   model_id=r.MODEL_ID, model_config=config,
                   scenario_ids=[proposal["scenarios"][1]["id"]])


def test_identity_adapter_changes_metadata_without_replacing_physics(monkeypatch):
    from tools.model_closeout.execution import ExecutionRecoveryCore as PreviousCore
    from hospital_ies.resilience import LOAD_ACCOUNTING_CONTRACT_ID
    assert d.LOAD_ACCOUNTING_CONTRACT_ID == LOAD_ACCOUNTING_CONTRACT_ID
    core = x.LoadAccountingCore.__new__(x.LoadAccountingCore)
    core.segments = []
    monkeypatch.setattr(PreviousCore, "observe", lambda self: dict(diesel_local_response=dict(model_id="M19"),
        execution_recovery=dict(contract_id="old"), model_closeout=dict(contract_id="old")))
    def advance(self, end):
        self.segments.append(dict(end_minute=end, execution_model_id="M19", unchanged_physics=17))
        return "unchanged_result"
    monkeypatch.setattr(PreviousCore, "advance", advance)
    monkeypatch.setattr(PreviousCore, "generate_command", lambda self, action: dict(action=action,execution_model_id="M19"))
    assert core.advance(15) == "unchanged_result"
    assert core.segments[0]["unchanged_physics"] == 17
    assert core.segments[0]["execution_model_id"] == r.MODEL_ID
    assert core.generate_command({"same": "request"})["execution_model_id"] == r.MODEL_ID
    value = core.observe()
    assert value["diesel_local_response"]["model_id"] == r.MODEL_ID
    assert value["execution_recovery"]["contract_id"] == x.DYNAMICS_CONTRACT_ID
    assert value["load_accounting"]["contract_id"] == d.LOAD_ACCOUNTING_CONTRACT_ID


def test_selective_environment_routes_to_corrected_grouping_without_dispatch():
    from hospital_ies.resilience import build_zone_groups
    original = original_proposal()
    proposal = d.panel("input_only", original["scenarios"][:1], evaluation_role="development")
    config = json.loads((V19 / "M19_MODEL_CONFIG.json").read_text(encoding="utf8"))
    env = e.OperatingHospitalIESEnv(ROOT, allowed_hours=d.allowed_hours(proposal), sealed_days=d.SEALED_DAYS,
                                  model_id=r.MODEL_ID, model_config=config)
    hourly = env.hourly.dropna(subset=["P_e_nonplant_kW"])
    expected = build_zone_groups(env.zones, hourly).set_index("hour_of_year")
    assert env.groups.loc[expected.index, expected.columns].equals(expected)
    assert env.model_id == r.MODEL_ID
    assert env.input_access_receipt["load_accounting_contract_id"] == d.LOAD_ACCOUNTING_CONTRACT_ID
    assert env.input_access_receipt["sealed_numeric_rows_parsed"] == 0
    assert env.input_access_receipt["final_release_implemented"] is False
    assert not env.history and env.scenario is None


def test_failed_registered_attempt_is_preserved_without_zero_service(tmp_path, monkeypatch):
    binding = r.source_binding()
    monkeypatch.setattr(r, "source_binding", lambda: binding)
    monkeypatch.setattr(r, "RUN", tmp_path / "v20")
    monkeypatch.setattr(r, "check_output_path", lambda value: Path(value).resolve())
    monkeypatch.setattr(r, "_snapshot", lambda value: tmp_path / "source_snapshot")
    proposal = d.panel("failure", original_proposal()["scenarios"][:1], evaluation_role="development")
    config = json.loads((V19 / "M19_MODEL_CONFIG.json").read_text(encoding="utf8"))
    directory = r.RUN / "experiments" / "failed"
    r.register(proposal, directory, [r.policy_specs()["E"]], model_id=r.MODEL_ID, model_config=config)
    monkeypatch.setattr(r, "OperatingHospitalIESEnv", lambda *a,**k: SimpleNamespace(model_id=r.MODEL_ID,core=SimpleNamespace(time=0)))
    def fail(*args, **kwargs):
        raise RuntimeError("deliberate synthetic failure, no physical episode")
    monkeypatch.setattr(r, "run_episode", fail)
    monkeypatch.setattr(r, "export_rows", lambda records,out: {"records":len(records)})
    result = r.run_batch(directory)
    assert result["registered_pairs"] == result["actual_attempts"] == result["unknown_full"] == 1
    assert result["complete_full"] == 0
    pair = next((directory / "pairs").iterdir())
    record = r.read(pair / "RESULT.json")
    assert record["analysis"] is None and record["raw_path"] is None
    assert record["status"] == "runner_unhandled_exception"
    assert "unknown" in record["error"]["physical_tail"]
    assert [json.loads(line)["event"] for line in (directory / "ATTEMPTS.jsonl").read_text(encoding="utf8").splitlines()] == ["started", "finished"]
    with pytest.raises(FileExistsError):
        r.run_batch(directory)


@pytest.mark.parametrize("route,module,base_name", [
    ("state_coordinated_joint", "planner", "state"),
    ("operating_joint", "operating_planner", "operating"),
    ("engineering_joint", "engineering_planner", "engineering"),
])
def test_m20_preserves_actual_m19_command_routes_and_output(route, module, base_name, monkeypatch):
    """Spy below the actual M19 routing layer; no optimizer or dispatch runs.

    The deliberately invalid legacy planner proves that M19's injection is
    required. Patching only ExecutionRecoveryCore would mask a missing layer.
    """
    import importlib
    from tools.model_closeout.environment import RecoveryCommandCore
    from tools.state_coordinated.environment import StateCoordinatedCore
    from tools.operating_response.environment import OperatingCommandCore
    from tools.engineering_model.environment import EngineeringCommandCore
    bases = dict(state=StateCoordinatedCore, operating=OperatingCommandCore, engineering=EngineeringCommandCore)
    def forbidden_legacy(*args, **kwargs):
        raise AssertionError("M19 command-route injection was bypassed")
    namespace = dict(planner=SimpleNamespace(solve_joint=forbidden_legacy),
                     policy=SimpleNamespace(validate_action=forbidden_legacy), route=route)
    exec("def generate_command(self, action):\n"
         "    if route == 'state_coordinated_joint':\n"
         "        policy.validate_action(action)\n"
         "    return dict(request=action, planned=planner.solve_joint('same-current-state', physical_input=17))\n", namespace)
    monkeypatch.setattr(bases[base_name], "generate_command", namespace["generate_command"])
    calls = []
    def actual_route_spy(*args, **kwargs):
        calls.append(dict(args=args, kwargs=copy.deepcopy(kwargs)))
        return dict(solver_module="tools.model_closeout."+module, args=list(args), kwargs=copy.deepcopy(kwargs))
    monkeypatch.setattr(importlib.import_module("tools.model_closeout."+module), "solve_joint", actual_route_spy)
    action = {route: {}}
    if route == "state_coordinated_joint":
        action[route]["coordination"] = r.policy_specs()["C19"]["config"]
    outputs = []
    for cls in (RecoveryCommandCore, x.LoadAccountingCore):
        core = cls.__new__(cls)
        core.execution_limits = lambda: {"same_limits": 42}
        core.bridge_wait_started_minute = 15.
        core.grid_bus_state = "ISLAND"
        outputs.append(core.generate_command(copy.deepcopy(action)))
    assert len(calls) == 2
    assert all(call["kwargs"]["execution_limits"] == {"same_limits": 42} for call in calls)
    if route == "state_coordinated_joint":
        assert all(call["kwargs"]["grid_bridge_request"] is True for call in calls)
    else:
        assert all("grid_bridge_request" not in call["kwargs"] for call in calls)
    assert outputs[0].pop("execution_model_id") == "M19_model_closeout"
    assert outputs[1].pop("execution_model_id") == r.MODEL_ID
    assert outputs[0] == outputs[1]
    assert action == outputs[1]["request"]
