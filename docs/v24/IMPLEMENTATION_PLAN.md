# V24 engineering completion implementation plan

**Goal:** Correct the demonstrated cold-store mode omission, make the tower heat-rejection assumption explicit, verify a real existing propagation chain, and close bounded summer-temperature diagnosis plus matched resource boundaries.

**Architecture:** Continue this worktree from local V23 commit `732fd6bd1bdaae754cce930e1084fe2d849d0771` on `sci-v24-engineering-completion`. Existing M23 physics/default routes remain historical references. M24 uses one finite cold-mode search hook and the same queued control/execution core, a separate explicit tower kW parameter, and the existing actual-source-loss/paid-boiler-restart mechanism. Temperature remains an independent exploratory module; its default is still disabled.

**Stack:** Installed Python/Gurobi, original DOE/8760 inputs and existing EnergyPlus26.2 derived models. All new raw/SQL/temp on `E:/hospital_ies_v24_20261026`. No MPC/ML predictor, figures/paper, future information, changed thresholds to manufacture results, remote push or main merge. Final compact delivery keeps the user's 100,000,000-byte limit.

## Authority, baseline and ownership

- [x] Read the complete revised task:185 lines,20757 bytes, SHA256 `6ef7b3f8e0a82a6e9107e163200d4b1f98a5a6db102f9b7a909cd868f9b37c99`. The revised V24 user task supersedes older stage stops and unnecessary repetition.
- [x] Actual clean V23 worktree was at732fd6b; all741 scientific source and28 input files matched Source05 before implementation. Preserve the V21 Git common directory and every V23 source cohort/result/failed attempt.
- [x] Created V24 branch in the same existing worktree. The original Chinese-root v43 plan was read; its old P0-only stopping point is superseded by the current authorization.
- [x] No new blanket baseline rerun: exact original bytes and existing actual994/1skip evidence close the V23 baseline. First new execution is the actual targeted production counterexample, not a repeated41-run campaign.
- Core writer: `v24_cold_core` exclusively owns common state/physical hooks and new `{environment,cold_modes,__init__}.py` under `tools/engineering_completion_v24/`.
- Thermal writer: `v24_thermal` owns only independent `thermal*.py`, corresponding parameter files/tests and E temperature evidence. No shared core edits.
- Source/propagation reviewer: `v24_sources_propagation` reads original materials, official sources and actual V23 processes; writes E evidence, no plant/state edits.
- Root owns this plan/report, generic validation entry/export, case registration, serial full IES dispatch, independent numeric review, final tests and compact delivery. Root does not concurrently edit the physical transition functions.

## 1. Cold-store legal modes

- [x] Production Gurobi + actual-state RED reproduced empty and partial-stock failure to charge under unreachable P0 fan demand;3 failed/6 passed, no exception substitution.
- [x] Implement bounded idle/discharge no-charge candidates and guarded charge from one snapshot. Preserve original normalized per-priority hierarchy; the extra conservative charge guard uses no-charge winning service and a strict-service witness, not original cost-lock slack. No trial commits inventory, time or starts.
- [x] Actual one-minute proof:fan0,pump1,P0 E20/H0/C100,GRID ceiling100,EC ceiling100,GT/DG/boiler/ABS0. M24 charges100kW with GRID38.894573kW and actual store pump2kW; stock rises1.583333kWh. M23 remains idle under its own identity.
- [x] Negatives:no actual source, no pump, no arrived cold-source permission. Empty/partial states and original source-short depletion remain covered. Real tiny inventory is not deleted and empty thresholds/physical tolerances are unchanged.
- [x] Actual five-minute repeated sequence verifies fan failure→paid recharge→fan return and EC loss→54.15s delivery→empty→legitimate recharge/repeat. Raw receipts and failure logs retained.
- [ ] Independent review original hierarchy/guard constraints, objective comparison and fallback records. Optional probe failure must be visible; a failed required physical action must remain failed/unknown.
- [ ] Register and run affected full fan-path/recharge and cold_partial/no-pump/no-fan cases under one frozen M24 source. Hold the original15min decision clock and ordinary queue.

## 2. Tower units and static heat-flow boundary

- [x] Read original Brown/Dirks source and three user-specified official sources. Original hospital source says four1200-ton towers but does not specify tower-ton standard, wet bulb, temperatures or flow.
- [ ] Expose `tower_rejection_capacity_kW=4220.223408` as independent explicit M24 research kW, preserving current default physics. Do not silently replace with5275.27926 or couple it to an EC capacity change.
- [ ] Record 12000BTU refrigeration-ton vs conditional15000BTU nominal-tower-ton. Original0.77kWe/RT includes condenser pumps/tower fans; retain aggregate electric accounting and clearly label the static rejection proxy. No unsupported loop-temperature dynamics or duplicated auxiliaries.
- [ ] One matched C cold-path sensitivity at1.25 rejection kW only, same load/faults/cold stock/controls. It is a unit-ambiguity boundary, not measured hospital uprating or policy superiority.

## 3. L2: existing actual dependency/secondary functional exit

- [x] Source inspection and old native evidence confirm:after actual nonUPS source becomes0, an already-qualified boiler loses function/protective readiness; real source return plus arrived permission and continuous paid restart restore it. `flow_available` is not actually passed in this production call; do not claim measured flow-trip coverage.
- [ ] Register staged upstream failure:GRID loss while retaining the original onlineGT_2; after DG actually connects, remove remaining GT. Compare short vs long sufficient disturbance, normal negative, and one lawful policy/resource condition that delays/avoids actual source loss if supported by the executed data.
- [ ] Verify the primary source transfer did not already create a misleading lock before the intended DG-exhaustion chain. Report actual positive boiler output before source loss, DG EMPTY vs permission-induced zero separately, source0, readiness/latch, actual paid restart and returned steam.
- [ ] Keep matched L1/L2 label-only counterfactual under the same M24 rule; label changes alone do not modify physics. Correct new recipe canonical metadata without rewriting old results.
- [ ] No added thermal-loop state:the already implemented mechanism is preferred. A partial capacity limit is not a false temperature accumulator; no unmet-demand/GT-overcurrent proxy returns.

## 4. Summer thermal diagnosis and bounded stop

- [x] Reproduce original summer calibration error and time origin:OR1 six-hour actual recovery760min vs old prediction1890min; identify98.286h slow mode and near-zero pre-event differences. Native substep replay does not explain the tail offset.
- [x] Fixed-zone two-state diagnostic4fits retained; it improves one tail but leaves strong two-hour recovery bias. It is a failed developmental alternative, not new validation.
- [x] Predeclare one final physically bounded3state alternative (air/furniture/wall capacities from IDF, total capacity bound unchanged, one start per zone/no daily parameters),4fits. Original standards and all difficult cases retained.
- [ ] Finish4 specific derived EnergyPlus output additions (summer/winter normal/6h) only to distinguish flow/reheat/NoEconomizer mechanisms; verify original1090 sizing values and original thermal streams unchanged.
- [ ] If calibration remains materially unqualified, stop structural extension and do not create misleading new-date validation. Report why current passive low-order model is inadequate for quantitative summer recovery. Keep winter weak excitation separate and default supply comparison uncoupled.
- [ ] Only a genuinely qualifying frozen alternative may proceed to a few preregistered strong summer dates. Never fit back to their outcomes, use future wall truth or change20min to390min.

## 5. Small matched resource matrix and final verification

- [ ] Register a targeted matrix after core integration, not a Cartesian search:affected cold paths; staged L2 normal/short/long plus matched labels; tower1.25; DG150/300/600 on the same long background; UPS20/40 with fixed power plus one necessary power control; cold30/60/90 with fixed power plus one power control. Reuse identical baseline tasks instead of duplicate dispatch.
- [ ] Default C. Add R/M only for existing affected matched comparison or a concrete causal question. No A search. All formal policy pairs require identical actual source/input/physical config/window.
- [ ] Separate initial actual stock, energy capacity, charge/discharge power, paid preparation, terminal stock. Explain service/fee/general-service tradeoffs and whether source, power, inventory, permit or path is binding. Do not force closed-loop metric monotonicity.
- [ ] Stop all scientific writers before actual formal runs. Bind source/input/parameters per attempt; failures/prefixes/unknown tails are retained. Fix ordinary failures, refreeze and rerun only affected tasks.
- [ ] One centralized `python -m pytest -q` after integration, with E basetemp and actual command/stdout/stderr/exit. Preserve the old0.5min stock-band difference; no extra0.25min run unless a new conclusion depends on that magnitude.
- [ ] Independent process/resource/accounting audit using actual native intervals. No new scientific result inferred merely from hashes or test counts.
- [ ] Brief Chinese report answers all six task questions, with separate completion status for default supply, propagation and temperature. Current source/config or complete732fd6b-bound patch, small actual tables/processes and minimum command/input closure delivered under100MB. Reuse the existing compact approach; no packaging-system development, plots, remote publication or main merge.
