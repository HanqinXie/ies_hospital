# V22 model closure implementation plan

> Agentic execution: use subagent-driven-development, with one writer for the shared physical transition. The user's 2026-10-25 task authorizes continuous implementation and bounded reruns; no intermediate approval, publication, figures or release packaging.

**Goal:** Close actual electricity/heat/cold, station auxiliaries, causal commands, storage and restoration, and retain physically unavoidable failures.
**Architecture:** Add an explicitly named M22 adapter over the preserved M21 execution foundation. Small opt-in hooks in the common physical balance and planner share the same storage/station capabilities; M21 remains a compatibility mode. Actual execution alone commits inventories and clocks. Temperature is an isolated, exploratory signed heat ledger; it cannot excuse cold ENS or decide critical control.
**Stack:** Existing Python 3.10/NumPy/SciPy/Gurobi/pytest, source DOE inputs. New raw/temp/results on E; no new dependencies, MPC or ML predictor.

## Baseline and authorization

- [x] Read all314 task lines (24487 bytes, SHA d2eb41c18927745cb9906c9f9b4ed5b81fc54f774b6f321fb29993fb78bb7016); attachment equals it after newline normalization.
- [x] Local/remote V21 both298ec4a, no dirty files; new D:/hospital_ies_v22_20261025 worktree on sci-v22-model-closure, inherited history retained.
- [x] Actual new-worktree baseline:692 passed,1 optional original-SQL skip,183.46s (184.078539s wall,exit0). Original logs E:/hospital_ies_v22_20261025/baseline.
- [x] Read-only source diagnoses: E:/hospital_ies_v22_20261025/{dispatch_diagnosis,station_source_diagnosis,temperature_diagnosis}.md. These diagnose; they are not repaired-trajectory evidence.
- [x] Keep a compact issue table with source-confirmed, observed/root-cause-pending and obsolete-line-of-code classifications.

## 1. Common storage and instantaneous power

Files: tools/model_closure_v22/{environment,storage,physical}.py; opt-in extensions of tools/operating_response/physical_balance.py and tools/sci_engineering/planner.py; tests/test_v22_physical_ports.py and focused trajectory regressions.

- [x] RED: physically ready DG can cover P0/P1 while old free UPS drains; test a critical-power deficit that must consume stock as the counterexample.
- [x] Define bridge energy from current critical building request, known observation/start/transfer clocks and efficiencies, capped by the actual40kWh store. No actual remaining outage length. Preserve min(current stock,target) after current P0/P1, before P2/P3; recharge only after all current service. Compare fuel and general service, not SOC alone.
- [x] Share port limits/modes with planning, feasibility and execution; no probe/enum may commit stock. Cold charge/discharge modes are mutually exclusive. Use real current cold deficit and path feasibility rather than GRID/TOWER Boolean selection; actual pump electricity remains mandatory.
- [x] Cold recharge hysteresis: begin at capacity minus max(1kWh,2%capacity), stop at full; report this operating assumption. Probe does not change the mode clock; actual commit does.
- [x] RED067/150 from original pre-shortage states. Replace the upper current-witness whole-step energy cap with explicitly instantaneous capability, retaining existing shaft-preview depletion splits and preparation-energy constraints. Test through normal command generation/arrival/execution; report earlier depletion if it occurs.

## 2. Station supply and startup

Files: tools/model_closure_v22/station.py, environment.py, physical.py; shared planner/balance hooks; tests/test_v22_station.py.

- [x] RED dead AC bus with full building UPS cannot run the boiler or AC GT starters; DG dedicated starting DC is separate.
- [x] Boiler station electricity =0.5kWe on +0.012kWe/kWsteam, using original steam enthalpy; sensitivity(0.25,.008)/(1,.016). These are research assumptions anchored to official Miura scale, not NMCSD OEM values. The original IDF boiler parasitic is0 and heating electricity is0; do not add the existing circulation pump or EC/ABS condenser/tower electricity twice.
- [x] Explicit station AC branch shares the actual source balance and excludes building UPS. Starting GT cannot supply its own starter. Qualify dedicated DC startup/control by explicit current availability, allowed starts and finite stated autonomy; expiry is an observable state, never silently infinite.
- [x] Boiler actual station-power interruption stops steam; restoration uses a declared continuous-power restart clock. GT startup requires arrived permission plus an already live external source and actual accounted starter power. Failed probes cannot start clocks/consume starts.
- [x] Revisit winter H0/H1 heat comparison and short10s versus old15min DG waits with identical source and load conditions.

## 3. GT interlock, policies, L2 and allocation

Files: bounded optional hook in tools/essential_fixes/execution.py; model_closure_v22/{policy,auxiliary,equivalence}.py; corresponding focused tests.

- [x] RED one failed all-GT floor candidate is not all combinations. Try at most7 nonempty subsets under the same arrived ports, dwell/ramp state and held-port proof; do not invent sinks or permissions. Preserve the actual324kW/200kW no-sink mandatory-stop negative.
- [x] Publish actual R/M/F/C/A configuration and objective differences, including the common local optimizer. Mechanism comparison is explicitly extra critical-reserve preference x preparation objective ordering, not true preparation-off. Same base and physical capabilities in all four cells.
- [x] A release additionally needs an actual current non-DG electric/heat/cold feasibility witness with existing paths, auxiliary electricity, effective permissions and no pending target credited as actual supply. Keep observed-time rules distinct from forecast of repair.
- [x] Before comparison register ENS equivalence separately per six critical carrier/group cells: max(0.001kWh,1e-6 of that cell's requested energy); interruption/longest/recovery equivalence1 second. These are selection equivalence bands, not feasibility or material-ENS thresholds. Preserve raw precision; a material critical deterioration is never traded for fees. Compare general carrier services after critical engineering equivalence; economy then conditional emissions; retain ties/conditional choices.
- [x] Default physical dependency is shared across L1-L4. Isolate the uncalibrated unmet-load GT stress proxy in an explicitly hypothetical sensitivity. Classify external fault, dependency loss, hard protection, proxy-triggered trip and ordinary interlock separately. Retain original1.05/15min assumptions and deterministic trigger/near/avoid/reset cases; do not claim real overcurrent protection.
- [x] Pump allocation exponent1 baseline and .75/1.25 feasible alternatives conserve each source component/hour and preserve zero-support shares; OR all-P0 fan cannot be multiplied beyond1. Actual auxiliary energy scaling is a separate explicit increment. Deliberately invalid shares fail before simulation, without clipping.

## 4. Bounded temperature correction

Independent files: tools/model_closure_v22/temperature.py, tests/test_v22_temperature.py, configs/model_closure_v22/temperature_acceptance.json. Implementation owner v22_temperature; no other shared-code edits.

- [x] Before correction register event RMSE<=1C, recovery6h RMSE<=.5C, max<=2C, first research crossing error<=10min, recovery error<=20min.
- [x] Correct signed actual air-system sensible forcing and retain actual input resolution/DST. Shared-coil total cooling is not representative-room sensible load. Do not fabricate10min heating from hourly input.
- [x] Continuous thermal state; pure recovery request; only actual supplied extra cold reduces it. Any diagnostic active recovery is a separately metered source-side cold/electric load, not free state reset or a change to original clinical request.
- [x] One bounded physical C/U calibration on the two original calibration cases, old four cases all seen regression. Add at most one thermal state only if input-complete evidence warrants it. If goals fail, keep exploratory status and continue the supply model; never use temperature to excuse ENS or choose a critical policy.

## 5. Targeted validation and compact delivery

- [x] A small CLI and explicit configurations, not a Cartesian replay of2778 old trajectories. Begin with discriminating C/needed counterexample policies; broaden only to cover changed interactions.
- [x] Winter/summer normal; first outage/DG handoff/second disturbance or return; healthy GRID cold deficit/partial tower; failed fan/pump/empty storage; station startup negative/positive;067/150; GT positive/negative; switches/release; L2/allocation; temperature recovery-energy case.
- [x] Per actual attempt: model/source/input/config identities, start/completion/failure and unknown prefix, actual event/power/stock/service/cost/conditional-emissions records. Distinguish historical counts and V22 actual counts. E-only raw traces; compact before/after CSV and validation report in docs/v22.
- [x] Once focused checks pass, one default full regression and necessary concentrated five-policy/hardware coverage. Documentation changes do not trigger physics reruns. Numerical errors receive minimal fixes and impacted reruns, never dropped assertions or widened physical tolerances.
- [x] Review physical/code/spec diffs, local ordinary commits on sci-v22-model-closure. No remote publication, main merge, paper figures or new delivery archive requested.

The user's current task overrides old stage stop points and prior final12 blind claims: V21 final12 is now seen regression only. Missing OEM data yields explicit finite assumptions, not permission to invent specifications. Completion requires actual engineering before/after outcomes, not merely test/commit counts.

## Execution closure

All implementation/checklist actions above were executed. Checked items mean work performed, not universal engineering success: temperature precision accepted0/16 and remains exploratory; mechanism recovery censoring and cold micro-pulse statistics stay explicit. Actual final source afd2d206dceb7923f8cbfcc64caa715e414ade4087e7297e0a120826e6a333a5 completed47 cases (original42 plus2 cold,2 release,1 exact allocation baseline); native audits1764+100+90+42 PASS, zero FAIL/UNKNOWN. Final default pytest832 passed/1optional SQL skip. Full results and remaining evidence limits are in MODEL_VALIDATION_REPORT.md; all original failures and intermediate versions remain on E. Ordinary local commit completes this checklist; no push/merge/archive.
