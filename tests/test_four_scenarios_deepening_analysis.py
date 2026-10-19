"""Read-only v8 analysis semantics on small, explicit trajectories."""
from __future__ import annotations

from pathlib import Path
import json
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]


def segment(start, end, *, failed=False):
    return {"start_minute": start, "end_minute": end,
            "classification": {"p0_guarantee_failure": failed}}


def test_first_failure_rescue_excludes_never_failed_and_waits_for_confirmation():
    from tools.four_scenarios.deepening_analysis import first_failure_rescue

    never = first_failure_rescue([segment(0, 600)], observed_until=600,
                                 requested_end=600)
    assert never["first_p0_failure_minute"] is None
    assert never["rescue_eligible"] is False
    assert never["first_reach_minute"] is None

    observed = first_failure_rescue([
        segment(0, 90), segment(90, 105, failed=True),
        segment(105, 120), segment(120, 135, failed=True),
        segment(135, 165), segment(165, 600)],
        observed_until=600, requested_end=600)
    assert observed["first_p0_failure_minute"] == 90
    assert observed["first_reach_minute"] == 105
    assert observed["minutes_to_first_reach"] == 15
    assert observed["rescue_confirmed_minute"] == 165
    assert observed["rescue_confirmed"] is True


def test_first_failure_rescue_keeps_truncated_tail_unknown():
    from tools.four_scenarios.deepening_analysis import first_failure_rescue

    rescue = first_failure_rescue([segment(0, 90),
                                   segment(90, 100, failed=True)],
                                  observed_until=100, requested_end=600)
    assert rescue["rescue_eligible"] is True
    assert rescue["first_reach_minute"] is None
    assert rescue["rescue_confirmed"] is None
    assert rescue["rescue_status"] == "unknown_after_truncation"


def test_first_failure_rescue_tracks_recurrence_after_confirmation():
    from tools.four_scenarios.deepening_analysis import first_failure_rescue

    rescue = first_failure_rescue([
        segment(0, 90), segment(90, 105, failed=True),
        segment(105, 135), segment(135, 150, failed=True),
        segment(150, 600)], observed_until=600, requested_end=600)
    assert rescue["rescue_confirmed_minute"] == 135
    assert rescue["rescue_later_recurrence_minute"] == 135
    assert rescue["rescue_sustained_to_requested_end"] is False


def test_event_counts_separate_real_trips_from_interlocks():
    from tools.four_scenarios.deepening_analysis import event_detail

    counts = event_detail([
        {"kind": "protection_trip", "device": "GT_2", "time_minute": 105},
        {"kind": "minimum_output_interlock", "device": "GT_1", "time_minute": 110},
        {"kind": "no_export_interlock", "device": "GT_3", "time_minute": 120},
    ])
    assert counts["gt_protection_trip_count"] == 1
    assert counts["minimum_output_interlock_count"] == 1
    assert counts["no_export_interlock_count"] == 1
    assert counts["first_gt_protection_trip_minute"] == 105


def scene(name, *, fault=None, shock=None, fault_time=90, shock_time=90):
    events = []
    if fault:
        events.append({"kind": "device_fault", "device": "GRID",
                       "availability": 0.0, "start_minute": fault_time,
                       "end_minute": 270.0})
    if shock:
        events.append({"kind": "demand_shock", "start_minute": shock_time,
                       "end_minute": 270.0, "shock": {"e": 0.3}})
    result = {"id": name, "analysis_class": name, "root_family_id": "r",
            "base_state_id": "base", "start_hour": 100,
            "stage": 3 if name == "L3" else 4,
            "enable_propagation": True, "response_delay_minutes": 15.0,
            "grid_limit_kW": 850.0, "pre_event_grid_limit_kW": 850.0,
            "exogenous_events": events, "faults": {"GRID": 0.0} if fault else {},
            "shock": {"e": 0.3} if shock else {}, "shock_by_priority": {}}
    if fault:
        result["fault_start_minute"] = float(fault_time)
    if shock:
        result["shock_start_minute"] = float(shock_time)
    return result


def shadow_context(primary_scenes, primary_rows, primary_states, primary_profiles,
                   *, cell_id="cell1", clock=90.0, shadow_value=1.0,
                   shadow_action=100.0):
    """One independently registered normal clock control for a synthetic cell."""
    shadow = scene("shadow_N4")
    shadow.update(analysis_class="N4_clockmatched", reference_role=
                  "no_load_no_fault_clockmatched_control",
                  shadow_event_clock_only=True, shock_start_minute=clock,
                  shock_duration_minutes=180.0, fault_start_minute=clock,
                  fault_duration_minutes=180.0)
    row = {"scenario_id": shadow["id"], "policy_id": "secure_milp",
           "primary_full_horizon": False, "full_full_horizon": True,
           "full_P0_failure_minutes": 0.0,
           "full_P0_temperature_trigger_minutes": 0.0,
           "full_P0_e_ENS_kWh": shadow_value}
    group = {"design_cell_id": cell_id, "root_family_id": "r",
             "original_scene_ids": {k: k for k in primary_scenes},
             "clockmatched_scene_id": shadow["id"],
             "shock_start_minute": clock, "fault_start_minute": clock,
             "response_delay_minutes": 15.0,
             "shock_duration_minutes": 180.0,
             "fault_duration_minutes": 180.0}
    return {"groups": [group], "scenes": {shadow["id"]: shadow},
            "rows": {(shadow["id"], "secure_milp"): row},
            "states": {(shadow["id"], "secure_milp"): "same"},
            "profiles": {(shadow["id"], "secure_milp"):
                         legal_profile(90.0, gt=shadow_action)}}


def legal_profile(cutoff, *, gt=100.0, legal_feature=1.0):
    return {"segments": [{"start_minute": 0.0,
                          "end_minute": cutoff,
                          "action": {"gt": gt}}],
            "events": [],
            "decisions": [{"decision_time_min": float(t),
                           "version": "p2a.observation.v1",
                           "observation_time_min": float(t),
                           "history_start_min": 0.0,
                           "history_end_min": float(t),
                           "features": {"current_load": legal_feature},
                           "feature_metadata": {},
                           "known_command_queue": [],
                           "arrived_command_id": None,
                           "graph": {"nodes": []}}
                          for t in range(0, int(cutoff), 15)]}


def test_four_cell_interaction_uses_shared_controls_per_design_cell():
    from tools.four_scenarios.deepening_analysis import four_cell_interactions

    scenes = {k: scene(k, fault=k in ("L3", "L4b"),
                       shock=k in ("L4a", "L4b"))
              for k in ("N4", "L3", "L4a", "L4b")}
    group = {"design_cell_id": "cell1", "root_family_id": "r",
             "base_state_id": "base", "response_delay_minutes": 15.0,
             "fault_start_minute": 90.0, "shock_start_minute": 90.0,
             "scene_ids": {k: k for k in scenes}}
    rows = {(k, "secure_milp"): {"scenario_id": k, "policy_id": "secure_milp",
                                 "primary_full_horizon": False,
                                 "full_full_horizon": True,
                                 "full_P0_failure": 0,
                                 "full_P0_failure_minutes": 0.0,
                                 "full_P0_temperature_trigger_minutes":
                                     {"N4": 0.0, "L3": 15.0,
                                      "L4a": 30.0, "L4b": 60.0}[k],
                                 "full_P0_e_ENS_kWh": v,
                                 "full_P0_h_ENS_kWh": 0.0,
                                 "full_P0_c_ENS_kWh": 0.0}
            for k, v in (("N4", 100.0), ("L3", 3.0),
                         ("L4a", 4.0), ("L4b", 9.0))}
    profiles = {key: legal_profile(
                    90.0, gt=999.0 if key[0] == "N4" else 100.0)
                for key in rows}
    clockmatched = shadow_context(scenes, rows, {}, profiles)
    result = four_cell_interactions([group], scenes, rows,
                                    {key: "same" for key in rows},
                                    ["secure_milp"],
                                    profiles=profiles,
                                    clockmatched=clockmatched)
    assert len(result) == 1
    assert result[0]["matching_status"] == "matched"
    assert result[0]["gate_semantics_mismatch"] is False
    assert result[0]["pre_event_legal_observation_equal"] is True
    assert result[0]["original_N4_gate_semantics_mismatch"] is True
    assert result[0]["normal_N4_scenario_id"] == "N4"
    assert result[0]["clockmatched_N4_scenario_id"] == "shadow_N4"
    assert result[0]["full_P0_e_ENS_kWh_interaction"] == pytest.approx(3.0)
    assert result[0]["full_P0_e_ENS_kWh_N4"] == 1.0
    assert result[0]["full_P0_failure_minutes_interaction"] == 0.0
    assert result[0]["full_P0_temperature_trigger_minutes_L4b"] == 60.0
    assert result[0]["full_P0_temperature_trigger_minutes_interaction"] == 15.0


def test_four_cell_without_audited_shadow_keeps_interaction_unknown():
    from tools.four_scenarios.deepening_analysis import four_cell_interactions

    scenes = {k: scene(k, fault=k in ("L3", "L4b"),
                       shock=k in ("L4a", "L4b"))
              for k in ("N4", "L3", "L4a", "L4b")}
    group = {"design_cell_id": "cell1", "root_family_id": "r",
             "base_state_id": "base", "response_delay_minutes": 15.0,
             "scene_ids": {k: k for k in scenes}}
    rows = {(k, "secure_milp"): {"full_full_horizon": True,
                                 "primary_full_horizon": True,
                                 "full_P0_e_ENS_kWh": 1.0}
            for k in scenes}
    result = four_cell_interactions([group], scenes, rows,
                                    {key: "same" for key in rows},
                                    ["secure_milp"])[0]
    assert result["matching_status"] == "invalid"
    assert result["invalid_reason"] == "clockmatched_N4_missing"
    assert result["normal_N4_scenario_id"] == "N4"
    assert result["N4_scenario_id"] is None
    assert result["full_complete_four_cell"] is False
    assert result["full_unknown_cell_count"] == 1
    assert result["gate_semantics_mismatch"] is None
    assert result["full_P0_e_ENS_kWh_interaction"] is None


def test_full_shadow_row_is_counted_even_if_registration_invalid():
    from tools.four_scenarios.deepening_analysis import four_cell_interactions

    scenes = {k: scene(k, fault=k in ("L3", "L4b"),
                       shock=k in ("L4a", "L4b"))
              for k in ("N4", "L3", "L4a", "L4b")}
    group = {"design_cell_id": "cell1", "root_family_id": "r",
             "base_state_id": "base", "response_delay_minutes": 15.0,
             "fault_start_minute": 90.0, "shock_start_minute": 90.0,
             "scene_ids": {k: k for k in scenes}}
    rows = {(k, "secure_milp"): {"full_full_horizon": True,
                                 "primary_full_horizon": True,
                                 "full_P0_e_ENS_kWh": 1.0}
            for k in scenes}
    profile = {key: [{"start_minute": 0.0, "end_minute": 90.0,
                      "action": {"gt": 100.0}}] for key in rows}
    shadow = shadow_context(scenes, rows, {}, profile, clock=80.0)
    result = four_cell_interactions([group], scenes, rows,
                                    {key: "same" for key in rows},
                                    ["secure_milp"], profiles=profile,
                                    clockmatched=shadow)[0]
    assert result["matching_status"] == "invalid"
    assert result["invalid_reason"] == "clockmatched_time_registration_differs"
    assert result["full_complete_four_cell"] is True
    assert result["full_unknown_cell_count"] == 0
    assert result["gate_semantics_mismatch"] is None
    assert result["full_P0_e_ENS_kWh_N4"] == 1.0
    assert result["full_P0_e_ENS_kWh_interaction"] is None


def test_four_cell_rejects_mismatched_absolute_fault_clock():
    from tools.four_scenarios.deepening_analysis import four_cell_interactions

    scenes = {k: scene(k, fault=k in ("L3", "L4b"),
                       shock=k in ("L4a", "L4b"),
                       fault_time=60 if k == "L4b" else 90)
              for k in ("N4", "L3", "L4a", "L4b")}
    group = {"design_cell_id": "bad", "root_family_id": "r",
             "base_state_id": "base", "response_delay_minutes": 15.0,
             "scene_ids": {k: k for k in scenes}}
    with pytest.raises(ValueError, match="fault"):
        four_cell_interactions([group], scenes, {}, {}, ["secure_milp"])


def test_four_cell_marks_mismatched_pre_event_execution_invalid():
    from tools.four_scenarios.deepening_analysis import four_cell_interactions

    scenes = {k: scene(k, fault=k in ("L3", "L4b"),
                       shock=k in ("L4a", "L4b"))
              for k in ("N4", "L3", "L4a", "L4b")}
    group = {"design_cell_id": "cell1", "root_family_id": "r",
             "base_state_id": "base", "response_delay_minutes": 15.0,
             "scene_ids": {k: k for k in scenes}}
    rows = {(k, "secure_milp"): {"full_full_horizon": True,
                                 "primary_full_horizon": True,
                                 "full_P0_e_ENS_kWh": 1.0}
            for k in scenes}
    profiles = {key: [{"start_minute": 0.0, "end_minute": 90.0,
                      "action": {"gt": 200.0 if key[0] == "L4b" else 100.0}}]
                for key in rows}
    clockmatched = shadow_context(scenes, rows, {}, profiles)
    result = four_cell_interactions([group], scenes, rows,
                                    {key: "same" for key in rows},
                                    ["secure_milp"], profiles=profiles,
                                    clockmatched=clockmatched)[0]
    assert result["matching_status"] == "invalid"
    assert result["invalid_reason"] == "pre_event_raw_profile_differs"
    assert result["full_complete_four_cell"] is True
    assert result["full_unknown_cell_count"] == 0
    assert result["full_P0_e_ENS_kWh_interaction"] is None


def test_four_cell_legal_online_snapshot_difference_blocks_interaction():
    from tools.four_scenarios.deepening_analysis import four_cell_interactions

    scenes = {k: scene(k, fault=k in ("L3", "L4b"),
                       shock=k in ("L4a", "L4b"))
              for k in ("N4", "L3", "L4a", "L4b")}
    group = {"design_cell_id": "cell1", "root_family_id": "r",
             "base_state_id": "base", "response_delay_minutes": 15.0,
             "scene_ids": {k: k for k in scenes}}
    rows = {(k, "secure_milp"): {"full_full_horizon": True,
                                 "primary_full_horizon": True,
                                 "full_P0_e_ENS_kWh": 1.0}
            for k in scenes}
    profiles = {key: legal_profile(
        90.0, legal_feature=2.0 if key[0] == "L4b" else 1.0)
        for key in rows}
    shadow = shadow_context(scenes, rows, {}, profiles)
    result = four_cell_interactions([group], scenes, rows,
                                    {key: "same" for key in rows},
                                    ["secure_milp"], profiles=profiles,
                                    clockmatched=shadow)[0]
    assert result["pre_event_raw_profile_equal"] is True
    assert result["pre_event_legal_observation_equal"] is False
    assert result["invalid_reason"] == "pre_event_legal_observation_differs"
    assert result["full_P0_e_ENS_kWh_interaction"] is None


def test_real_window_payload_schema_covers_every_interaction_and_policy_target():
    from tools.four_scenarios import analysis as previous
    from tools.four_scenarios import deepening_analysis as deep

    raw_segment = {"start_minute": 0.0, "end_minute": 600.0,
                   "classification": {
                       "p0_guarantee_failure": False,
                       "material_unserved_by_carrier": {"e": False,
                                                         "h": False,
                                                         "c": False},
                       "p0_temperature_violation": False},
                   "unserved_kW": [[0.0, 0.0, 0.0] for _ in range(4)]}
    score = {"full_horizon": True,
             "targets": [0.0] * len(previous.TARGET_NAMES)}
    for prefix, start, end in (("primary", 60.0, 420.0),
                               ("full", 0.0, 600.0)):
        fields = previous._window_payload([raw_segment], score,
                                          prefix, start, end)
        assert prefix + "_P0_failure_minutes" in fields
        assert prefix + "_observed_prefix_P0_failure_minutes" in fields
        assert all(prefix + "_" + name in fields
                   for name in deep.INTERACTION_NAMES)
        assert all(prefix + "_" + name in fields
                   for name in previous.TARGET_NAMES)


def test_sensitivity_pairs_main_to_one_axis_same_root_class_mode_stratum():
    from tools.four_scenarios.deepening_analysis import sensitivity_paired_deltas

    names = ("N4", "L3", "L4a", "L4b")
    main = {"design_cell_id": "main1", "design_axis": "main",
            "root_family_id": "r", "base_state_id": "base",
            "shock_mode": "e", "stratum": "ordinary",
            "response_delay_minutes": 15.0,
            "shock_duration_minutes": 180.0,
            "fault_duration_minutes": 240.0,
            "fault_availability": 0.5,
            "scene_ids": {k: "base_" + k for k in names}}
    variant = {**main, "design_cell_id": "delay1", "design_axis": "delay",
               "design_level": "response_delay_minutes=30",
               "response_delay_minutes": 30.0,
               "scene_ids": {k: "delay_" + k for k in names}}
    scenes, rows, states = {}, {}, {}
    for prefix in ("base", "delay"):
        for name in names:
            sid = prefix + "_" + name
            scenes[sid] = {"id": sid, "analysis_class": name,
                           "root_family_id": "r", "base_state_id": "base",
                           "start_hour": 100, "grid_limit_kW": 850.0}
            states[(sid, "secure_milp")] = "same"
            rows[(sid, "secure_milp")] = {
                "primary_full_horizon": True, "full_full_horizon": True,
                "primary_P0_failure": 0, "full_P0_failure": 0,
                "full_P0_e_ENS_kWh": (3.0 if prefix == "delay" and
                                      name == "L4b" else 1.0),
                "gt_protection_trip_count": (1 if prefix == "delay" and
                                             name == "L4b" else 0),
                "observed_until_minute": 600.0,
                "final_diesel_energy_remaining_kWh": (
                    2000.0 if prefix == "delay" and name == "L4b" else 2100.0)}
    result = sensitivity_paired_deltas([main, variant], scenes, rows,
                                       states, ["secure_milp"])
    assert len(result) == 4
    l4b = next(row for row in result if row["analysis_class"] == "L4b")
    assert l4b["matching_status"] == "matched"
    assert l4b["main_scene_id"] == "base_L4b"
    assert l4b["sensitivity_scene_id"] == "delay_L4b"
    assert l4b["parameter_main_value"] == 15.0
    assert l4b["parameter_sensitivity_value"] == 30.0
    assert l4b["full_P0_e_ENS_kWh_sensitivity_minus_main"] == 2.0
    assert l4b["gt_protection_trip_count_sensitivity_minus_main"] == 1
    assert l4b["final_diesel_energy_remaining_kWh_sensitivity_minus_main"] == -100.0
    rows[("delay_L4b", "secure_milp")]["full_full_horizon"] = False
    rerun = sensitivity_paired_deltas([main, variant], scenes, rows,
                                      states, ["secure_milp"])
    l4b_unknown = next(row for row in rerun if row["analysis_class"] == "L4b")
    assert l4b_unknown["full_complete_pair"] is False
    assert l4b_unknown["full_P0_e_ENS_kWh_sensitivity_minus_main"] is None


def test_interaction_denominator_counts_valid_and_invalid_separately():
    from tools.four_scenarios.deepening_analysis import _denominators

    row = {"scenario_id": "s", "root_family_id": "r",
           "analysis_class": "L4b", "policy_id": "secure_milp",
           "full_full_horizon": True, "full_P0_failure": 0,
           "rescue_eligible": False, "rescue_confirmed": None,
           "recovery_30min": True, "event_end_minute": 270,
           "gt_protection_trip_count": 0,
           "minimum_output_interlock_count": 0,
           "no_export_interlock_count": 0}
    interactions = [{"matching_status": "matched",
                     "gate_semantics_mismatch": False,
                     "full_complete_four_cell": True},
                    {"matching_status": "invalid",
                     "gate_semantics_mismatch": None,
                     "full_complete_four_cell": True}]
    counts = _denominators([row], [{"design_cell_id": "c1"}], interactions)
    assert counts["full_complete_four_cell_policy_count"] == 2
    assert counts["full_valid_four_cell_policy_count"] == 1
    assert counts["invalid_four_cell_policy_count"] == 1
    assert counts["unverified_gate_semantics_count"] == 1
    assert counts["gate_semantics_mismatch_count"] == 0


def test_four_cell_marks_different_propagation_gate_clocks_invalid():
    from tools.four_scenarios.deepening_analysis import four_cell_interactions

    scenes = {k: scene(k, fault=k in ("L3", "L4b"),
                       shock=k in ("L4a", "L4b"), fault_time=60,
                       shock_time=90)
              for k in ("N4", "L3", "L4a", "L4b")}
    group = {"design_cell_id": "cell2", "root_family_id": "r",
             "base_state_id": "base", "response_delay_minutes": 15.0,
             "scene_ids": {k: k for k in scenes}}
    rows = {(k, "secure_milp"): {"full_full_horizon": True,
                                 "primary_full_horizon": True}
            for k in scenes}
    profiles = {key: [{"start_minute": 0.0, "end_minute": 60.0,
                      "action": {"gt": 100.0}}] for key in rows}
    clockmatched = shadow_context(scenes, rows, {}, profiles,
                                  cell_id="cell2", clock=90.0)
    result = four_cell_interactions([group], scenes, rows,
                                    {key: "same" for key in rows},
                                    ["secure_milp"], profiles=profiles,
                                    clockmatched=clockmatched)[0]
    assert result["matching_status"] == "invalid"
    assert result["invalid_reason"] == "propagation_gate_clock_differs"


def test_post_event_service_reports_prefix_and_terminal_state_without_declaring_recovery():
    from tools.four_scenarios.deepening_analysis import post_event_service

    def physical(start, end, p3_e):
        return {"start_minute": start, "end_minute": end,
                "unserved_kW": [[0.0] * 3, [0.0] * 3, [0.0] * 3,
                                [p3_e, 0.0, 0.0]]}

    observed = post_event_service([physical(0, 100, 0),
                                   physical(100, 130, 60),
                                   physical(130, 600, 0)],
                                  event_end=100, observed_until=600,
                                  requested_end=600)
    assert observed["post_event_observed_P3_e_ENS_kWh"] == pytest.approx(30)
    assert observed["post_event_terminal_P3_e_unserved_kW"] == 0
    assert observed["post_event_observation_complete"] is True
    assert "P3_recovered" not in observed

    truncated = post_event_service([physical(0, 100, 0),
                                    physical(100, 130, 60)],
                                   event_end=100, observed_until=130,
                                   requested_end=600)
    assert truncated["post_event_observed_P3_e_ENS_kWh"] == pytest.approx(30)
    assert truncated["post_event_observation_complete"] is False


def test_policy_event_count_difference_is_unknown_for_unequal_observation():
    from tools.four_scenarios.deepening_analysis import policy_deltas

    def row(pid, observed):
        return {"scenario_id": "s", "root_family_id": "r",
                "analysis_class": "L3", "policy_id": pid,
                "observed_until_minute": observed,
                "primary_full_horizon": False, "full_full_horizon": False,
                "primary_P0_failure": None, "full_P0_failure": None,
                "gt_protection_trip_count": 1,
                "minimum_output_interlock_count": 0,
                "no_export_interlock_count": 0,
                "requested_decision_count": 5,
                "arrived_command_count": 5,
                "executed_command_count": 5}

    result = policy_deltas([row("secure_milp", 600),
                            row("priority_milp", 300)],
                           ["secure_milp", "priority_milp"])[0]
    assert result["gt_protection_trip_count_candidate"] == 1
    assert result["gt_protection_trip_count_delta"] is None
    assert result["event_count_delta_comparable"] is False


def test_policy_deltas_keep_temperature_and_original_secondary_trip_metric():
    from tools.four_scenarios.deepening_analysis import policy_deltas

    rows = []
    for policy, temp, secondary in (("secure_milp", 25.0, 0),
                                    ("priority_milp", 26.5, 2)):
        rows.append({"scenario_id": "s", "root_family_id": "r",
                     "analysis_class": "L4a", "policy_id": policy,
                     "observed_until_minute": 600,
                     "primary_full_horizon": True, "full_full_horizon": True,
                     "primary_P0_failure": 0, "full_P0_failure": 0,
                     "full_P1_max_temperature_C": temp,
                     "full_secondary_GT_trips": secondary,
                     "gt_protection_trip_count": 0,
                     "minimum_output_interlock_count": 0,
                     "no_export_interlock_count": 0,
                     "requested_decision_count": 40,
                     "arrived_command_count": 40,
                     "executed_command_count": 40})
    result = policy_deltas(rows, ["secure_milp", "priority_milp"])[0]
    assert result["full_P1_max_temperature_C_delta"] == pytest.approx(1.5)
    assert result["full_secondary_GT_trips_delta"] == 2


def test_execution_detail_uses_executed_source_and_command_arrival():
    from tools.four_scenarios.deepening_analysis import execution_detail

    scene_spec = {"initiating_device": "GT_2",
                  "exogenous_events": [{"kind": "device_fault",
                                         "start_minute": 90, "end_minute": 270}]}
    def physical(start, end, gt, grid, diesel):
        return {"start_minute": start, "end_minute": end,
                "action": {"gt_electric_kW": [gt, 0, 0],
                           "grid_import_kW": grid, "diesel_kW": diesel,
                           "boiler_steam_lbph": 0, "hrsg_steam_lbph": 0,
                           "absorption_cooling_kW": 0},
                "post_state": {"diesel_energy_remaining_kWh": 2200,
                               "extra_faulted": ["GT_2"],
                               "gt_prev": [gt, 0, 0],
                               "overload_minutes": [0, 6, 0],
                               "diesel_status": "CONNECTED"},
                "availability": {"GT_1": 1.0, "GT_2": 1.0,
                                 "GT_3": 1.0}}
    payload = {"segments": [physical(0, 90, 120, 500, 0),
                            physical(90, 105, 120, 0, 0),
                            physical(105, 600, 200, 0, 400)],
               "events": [{"kind": "command_generated", "time_minute": 90},
                          {"kind": "command_arrived", "time_minute": 105},
                          {"kind": "command_executed", "time_minute": 105}],
               "summary": {"projection_changed_steps": 2,
                           "strategy_fallback_steps": 1}}
    detail = execution_detail(scene_spec, payload)
    assert detail["pre_event_executed_gt_kW"] == 120
    assert detail["first_event_executed_diesel_kW"] == 0
    assert detail["first_post_event_command_arrived_minute"] == 105
    assert detail["max_executed_diesel_kW"] == 400
    assert detail["projection_changed_steps"] == 2
    assert detail["final_observed_at_minute"] == 600
    assert detail["final_initiating_device_availability"] == 1.0
    assert detail["final_initiating_device_extra_faulted"] is True
    assert detail["final_GT_availability_json"]["GT_2"] == 1.0
    assert detail["final_GT_overload_minutes_json"] == [0, 6, 0]
    assert detail["final_diesel_status"] == "CONNECTED"


def test_independent_shadow_batch_needs_its_own_passed_sha_bound_audit(
        tmp_path, monkeypatch):
    from tools.four_scenarios import deepening_analysis as deep
    from tools.four_scenarios import analysis as previous

    manifest_path = tmp_path / "manifest.json"
    plan_path = tmp_path / "plan.json"
    audit_path = tmp_path / "AUDIT.json"
    results_dir = tmp_path / "results"
    scene_spec = {"id": "shadow", "analysis_class": "N4_clockmatched",
                  "experiment_role": "train", "is_final_blind": False}
    manifest_path.write_text(json.dumps({"signature": "signed-manifest",
                                         "scenarios": [scene_spec]}),
                             encoding="utf-8")
    plan = {"scenario_ids": ["shadow"], "policy_specs": [
        {"policy_id": "secure_milp"}], "phase": "train",
        "registered_pair_count": 1,
        "manifest_signature": "signed-manifest",
        "manifest_sha256": previous._sha(manifest_path)}
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    audit = {"passed": True, "matched_initial_state_and_stream": True,
             "plan_sha256": previous._sha(plan_path),
             "manifest_sha256": previous._sha(manifest_path),
             "registered_pair_count": 1, "terminal_pair_count": 1,
             "recomputed_pair_count": 1,
             "pair_ledger": [{"scenario_id": "shadow",
                              "policy_id": "secure_milp"}]}
    audit_path.write_text(json.dumps(audit), encoding="utf-8")
    monkeypatch.setattr(previous, "_read_verified_pair", lambda *args: (
        {"initial_state": {"value": 1}, "identity": {
            "exogenous_stream_id": "stream"}, "segments": [], "events": []},
        {"status": "completed"}, tmp_path / "unused_raw.gz"))
    monkeypatch.setattr(deep, "trajectory_row", lambda scene, pid, *args: {
        "scenario_id": scene["id"], "policy_id": pid})
    loaded = deep._load_audited_batch(manifest_path, plan_path,
                                      results_dir, audit_path)
    assert loaded["rows"][("shadow", "secure_milp")]["scenario_id"] == "shadow"
    assert loaded["states"][("shadow", "secure_milp")]

    audit["passed"] = False
    audit_path.write_text(json.dumps(audit), encoding="utf-8")
    with pytest.raises(ValueError, match="Passed raw audit"):
        deep._load_audited_batch(manifest_path, plan_path,
                                 results_dir, audit_path)

    audit["passed"] = True
    plan["manifest_signature"] = "wrong-signature"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    audit["plan_sha256"] = previous._sha(plan_path)
    audit_path.write_text(json.dumps(audit), encoding="utf-8")
    with pytest.raises(ValueError, match="Passed raw audit"):
        deep._load_audited_batch(manifest_path, plan_path,
                                 results_dir, audit_path)


def test_fault_cross_l3_minus_l1_is_descriptive_and_keeps_real_trip_separate():
    from tools.four_scenarios.deepening_analysis import fault_cross_deltas

    l1 = scene("L1", fault=True)
    l3 = scene("L3", fault=True)
    l1.update(stage=1, enable_propagation=False)
    l3.update(stage=3, enable_propagation=True)
    scenes = {x["id"]: x for x in (l1, l3)}
    group = {"design_cell_id": "fc1", "root_family_id": "r",
             "base_state_id": "base", "initiating_device": "GRID",
             "availability": 0.0, "fault_start_minute": 90.0,
             "fault_duration_minutes": 180.0,
             "scene_ids": {"L1": "L1", "L3": "L3"}}
    raw_fault = [{"kind": kind, "time_minute": minute,
                  "details": {"GRID": 0.0}}
                 for kind, minute in (("exogenous_fault_start", 90.0),
                                      ("exogenous_fault_end", 270.0))]
    rows = {("L1", "secure_milp"): {
                "full_full_horizon": True, "primary_full_horizon": True,
                "full_P0_failure": 0, "full_P0_e_ENS_kWh": 2.0,
                "gt_protection_trip_count": 0, "observed_until_minute": 600,
                "observed_fault_events_json": raw_fault},
            ("L3", "secure_milp"): {
                "full_full_horizon": True, "primary_full_horizon": True,
                "full_P0_failure": 1, "full_P0_e_ENS_kWh": 5.0,
                "gt_protection_trip_count": 1, "observed_until_minute": 600,
                "observed_fault_events_json": raw_fault}}
    profiles = {key: legal_profile(90.0) for key in rows}
    result = fault_cross_deltas([group], scenes, rows,
                                {key: "same" for key in rows},
                                ["secure_milp"], profiles=profiles)[0]
    assert result["full_P0_e_ENS_kWh_L3_minus_L1"] == 3.0
    assert result["full_P0_failure_known_L3_minus_L1"] == 1
    assert result["real_GT_trip_count_L1"] == 0
    assert result["real_GT_trip_count_L3"] == 1
    assert result["raw_fault_event_trace_status"] == "matched"
    assert result["pre_event_legal_observation_equal"] is True
    assert result["contrast_interpretation"] == "descriptive_propagation_mode_difference"


def test_fault_cross_truncated_pair_retains_unknown_delta():
    from tools.four_scenarios.deepening_analysis import fault_cross_deltas

    l1 = scene("L1", fault=True)
    l3 = scene("L3", fault=True)
    l1.update(stage=1, enable_propagation=False)
    l3.update(stage=3, enable_propagation=True)
    scenes = {x["id"]: x for x in (l1, l3)}
    group = {"design_cell_id": "fc1", "root_family_id": "r",
             "base_state_id": "base", "initiating_device": "GRID",
             "availability": 0.0, "fault_start_minute": 90.0,
             "fault_duration_minutes": 180.0,
             "scene_ids": {"L1": "L1", "L3": "L3"}}
    raw_fault = [{"kind": kind, "time_minute": minute,
                  "details": {"GRID": 0.0}}
                 for kind, minute in (("exogenous_fault_start", 90.0),
                                      ("exogenous_fault_end", 270.0))]
    rows = {("L1", "secure_milp"): {
                "full_full_horizon": True, "primary_full_horizon": True,
                "full_P0_failure": 0, "full_P0_e_ENS_kWh": 2.0,
                "gt_protection_trip_count": 0, "observed_until_minute": 600,
                "max_executed_diesel_kW": 100.0,
                "observed_fault_events_json": raw_fault},
            ("L3", "secure_milp"): {
                "full_full_horizon": False, "primary_full_horizon": True,
                "full_P0_failure": None, "full_P0_e_ENS_kWh": 5.0,
                "gt_protection_trip_count": 1, "observed_until_minute": 300,
                "max_executed_diesel_kW": 200.0,
                "observed_fault_events_json": raw_fault}}
    profiles = {key: legal_profile(90.0) for key in rows}
    result = fault_cross_deltas([group], scenes, rows,
                                {key: "same" for key in rows},
                                ["secure_milp"], profiles=profiles)[0]
    assert result["full_complete_pair"] is False
    assert result["full_P0_e_ENS_kWh_L3_minus_L1"] is None
    assert result["full_P0_failure_known_L3_minus_L1"] is None
    assert result["real_GT_trip_count_L3"] == 1
    assert result["event_count_delta_comparable"] is False
    assert result["max_executed_diesel_kW_L3_minus_L1"] is None


def test_fault_cross_pre_event_legal_difference_invalidates_delta():
    from tools.four_scenarios.deepening_analysis import fault_cross_deltas

    l1 = scene("L1", fault=True)
    l3 = scene("L3", fault=True)
    l1.update(stage=1, enable_propagation=False)
    l3.update(stage=3, enable_propagation=True)
    group = {"design_cell_id": "fc1", "root_family_id": "r",
             "base_state_id": "base", "initiating_device": "GRID",
             "availability": 0.0, "fault_start_minute": 90.0,
             "fault_duration_minutes": 180.0,
             "scene_ids": {"L1": "L1", "L3": "L3"}}
    fault = [{"kind": kind, "time_minute": t, "details": {"GRID": 0.0}}
             for kind, t in (("exogenous_fault_start", 90.0),
                             ("exogenous_fault_end", 270.0))]
    rows = {(name, "secure_milp"): {
        "full_full_horizon": True, "primary_full_horizon": True,
        "full_P0_failure": 0, "full_P0_e_ENS_kWh": 1.0,
        "observed_until_minute": 600, "observed_fault_events_json": fault}
        for name in ("L1", "L3")}
    profiles = {("L1", "secure_milp"): legal_profile(90.0),
                ("L3", "secure_milp"): legal_profile(90.0,
                                                     legal_feature=2.0)}
    result = fault_cross_deltas([group], {"L1": l1, "L3": l3}, rows,
                                {key: "same" for key in rows},
                                ["secure_milp"], profiles=profiles)[0]
    assert result["pre_event_raw_profile_equal"] is True
    assert result["pre_event_legal_observation_equal"] is False
    assert result["matching_status"] == "invalid"
    assert result["full_P0_e_ENS_kWh_L3_minus_L1"] is None


def test_fault_cross_rejects_descriptive_delta_when_raw_fault_events_differ():
    from tools.four_scenarios.deepening_analysis import fault_cross_deltas

    l1 = scene("L1", fault=True)
    l3 = scene("L3", fault=True)
    l1.update(stage=1, enable_propagation=False)
    l3.update(stage=3, enable_propagation=True)
    group = {"design_cell_id": "fc1", "root_family_id": "r",
             "base_state_id": "base", "initiating_device": "GRID",
             "availability": 0.0, "fault_start_minute": 90.0,
             "fault_duration_minutes": 180.0,
             "scene_ids": {"L1": "L1", "L3": "L3"}}
    rows = {}
    for name, when in (("L1", 90.0), ("L3", 105.0)):
        rows[(name, "secure_milp")] = {
            "full_full_horizon": True, "primary_full_horizon": True,
            "full_P0_failure": 0, "full_P0_e_ENS_kWh": 1.0,
            "observed_until_minute": 600,
            "observed_fault_events_json": [
                {"kind": "exogenous_fault_start", "time_minute": when,
                 "details": {"GRID": 0.0}},
                {"kind": "exogenous_fault_end", "time_minute": 270.0,
                 "details": {"GRID": 0.0}}]}
    result = fault_cross_deltas([group], {"L1": l1, "L3": l3}, rows,
                                {key: "same" for key in rows},
                                ["secure_milp"])[0]
    assert result["raw_fault_event_trace_status"] == "invalid"
    assert result["full_P0_e_ENS_kWh_L3_minus_L1"] is None


def test_fault_cross_report_requires_actual_seen_source_manifest_sha(
        tmp_path, monkeypatch):
    from tools.four_scenarios import deepening_analysis as deep

    source = tmp_path / "source_v8.json"
    source.write_text(json.dumps({"signature": "source_signature"}), encoding="utf-8")
    monkeypatch.setattr(deep, "_load_audited_batch", lambda *args: {
        "manifest": {"source_v8_manifest_sha256": "wrong_sha",
                     "source_v8_manifest_signature": "source_signature",
                     "matched_fault_groups": []}})
    with pytest.raises(ValueError, match="source v8 manifest"):
        deep.analyze_fault_cross("manifest", "plan", "results", "audit",
                                 source_v8_manifest_path=source)


def test_full_grade_scope_requires_all_registered_primary_and_shadow_raw():
    from tools.four_scenarios.deepening_analysis import _analysis_scope_status

    classes = {"N2": 12, "N4": 24, "L1": 12, "L2": 144,
               "L3": 24, "L4a": 156, "L4b": 156}
    scenes = {f"{name}_{i}": {"analysis_class": name}
              for name, count in classes.items() for i in range(count)}
    groups = [{"design_cell_id": f"g{i}",
               "design_axis": ("main" if i < 144 else
                               ("delay", "duration", "availability")[(i-144)//4])}
              for i in range(156)]
    primary = {"scenes": scenes, "scenario_ids": list(scenes),
               "policy_ids": ["secure_milp", "priority_milp"],
               "plan": {"phase": "train", "policy_specs": [
                   {"policy_id": "secure_milp", "kind": "secure"},
                   {"policy_id": "priority_milp", "kind": "priority"}]},
               "manifest": {"matched_four_cell_groups": groups}}
    shadow_scenes = {f"shadow_{i}": {"analysis_class": "N4_clockmatched"}
                     for i in range(24)}
    shadow = {"scenes": shadow_scenes,
              "scenario_ids": list(shadow_scenes),
              "groups": [{"design_cell_id": f"g{i}"} for i in range(156)],
              "plan": {"phase": "train", "policy_specs":
                       primary["plan"]["policy_specs"]}}
    assert _analysis_scope_status(primary, shadow) == ("full_grade", [])
    primary["scenario_ids"].pop()
    status, reasons = _analysis_scope_status(primary, shadow)
    assert status == "partial"
    assert "primary_plan_does_not_cover_528_scenes" in reasons
    primary["scenario_ids"] = list(scenes)
    status, reasons = _analysis_scope_status(primary, None)
    assert status == "partial"
    assert "clockmatched_batch_missing" in reasons
