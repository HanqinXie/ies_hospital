# V23 model correctness implementation plan

> For agentic workers: use subagent-driven implementation with independent file ownership. The user has authorized continuous repair, bounded new EnergyPlus runs and actual verification; no intermediate approval is required. One writer modifies the shared physical/execution core.

**Goal:** Remove avoidable restoration delay, false storage recoveries and bounded-domain omissions while preserving causal permissions, physical balances and recorded failures.

**Architecture:** Continue in the existing V22 worktree on `sci-v23-model-correctness`, inherited local commit `08eb3fbfec190cb19dc1a66a6a9162eb00cde872`. Add M23 adapters and explicit opt-in hooks over the same M22 physics; old results and M22 behavior remain reference evidence. Preparation permission, arrived permission, actual paid execution and offline evaluation remain separate. Independent metrics and thermal modules have no access to control futures.

**Stack:** Existing Python/Gurobi/pytest and installed EnergyPlus26.2.0. Original22.1 IDF/EPW and 8760 loads unchanged. New raw, SQL, temp and failed attempts only E:/hospital_ies_v23_20261025. No MPC, ML predictor, figures, strategy search or remote publication. Final lightweight review package contains necessary actual content, not E-only pointers.

## Baseline and design

- [x] Read all235 task lines,20726 bytes, SHA89c5f87c6100644ce521fecf9d0299328786c0ff0906d5ed7fc6747624705c34. Read original v43 rules and current V22 report, changes, assumptions, README, actual configs/records; current task supersedes older stop/budget limits.
- [x] Verify clean local V22 and all698 source/28 input hashes, not only aggregate strings; preserve V22 branch08eb3fb and Git common directory in V21. Actual fetch sees V21 remote298ec4a; do not replace local V22 with it.
- [x] Start new branch in the same worktree, as explicitly requested. No new full worktree/copy.
- [x] Actual default baseline831passed/1skip/1source-drift failure,248.55s. The new modules were being written during the old runner's source freeze. Pause writers and rerun that exact failure:1passed,7.54s,exit0. Preserve both records; final full default check runs only after all writers freeze.
- [x] Preserve compact actual baseline recovery/stock/command slices and bind the inherited source. Register acceptance, comparison and numerical rules before new comparisons.

## 1. Common preparation and legal domains (root owns)

Files: tools/model_correctness_v23/{environment,policy,domains}.py, bounded opt-in hooks in shared sci planner/operating execution/balance only where required; tests/test_v23_recovery.py and test_v23_domains.py.

- [x] RED through actual command generation/arrival: C/A zero steam permit at780 despite present critical heat demand; actual bus return780.5 and later paid restart. Separate current delivered power from a conditional preparation permission held in the original queue. Apply the common interface to all strategies, not labels or067 IDs.
- [x] Implement current-demand-only preparation for constrained heat/cold paths, respecting existing capacities, dispatch intent, ordinary queue delay, actual station paths and restart power. Pending permission is not actual load or a GT sink. No event-triggered fast path is needed unless the queue approach is demonstrably insufficient.
- [x] Negative actual processes: hardware repaired but grid not connected; station branch still lost; permission not arrived; interrupted restart; repeated loss. Test cold/converter/pump paths as well as boiler. Preserve V22 complete trajectory and rerun affected C/R/A under the same input.
- [x] RED compile/domain case: zero subdomain unavailable while another currently legal off/start/positive domain is feasible. Enumerate only the small supported domains from one snapshot, fully reoptimize original service priorities; do not reuse candidate locks or commit inventory/clocks. Preserve pending-vs-arrived permissions. Real positive steam must have paid station power and a legal heat/steam destination. Report checked-domain exhaustion, candidate rejection and implementation errors separately.
- [x] Keep genuine no-sink GT exit negative and inherited ramp/dwell/subset constraints. Do not invent minimum boiler capacity, free dump or export.

## 2. Depletion source and event consistency (root owns)

Files: M23 environment/event helpers; minimal opt-in hook in tools/operating_response/execution.py; tests/test_v23_depletion.py.

- [x] Trace actual V22 first depletion and solver layers: empty fallback selected charge despite continuing critical-cold deficit. Recharge objective used the normalized service-lock allowance, creating1.88e-7kWh input, then full-power microsecond discharge. A later split re-solved at a new power without consistent depletion re-location. Evidence in E pulse_metrics/.
- [x] RED empty+continuing deficit must idle, while real post-restoration surplus may recharge then discharge. Preserve true tiny stock and original physical tolerances; no arbitrary stock threshold increase or unrecorded energy deletion.
- [x] Resolve actual powers and inventory boundary consistently before committing a segment. Fresh same-snapshot trials may shrink/extend only within the next physical/command/observation boundary. Failed trial cannot settle energy, starts or clocks. Convergence comparisons change only necessary integration/event precision (e.g. original1min vs.5min); upper15min clock/queue stays fixed.
- [x] Actual partial/total cold-source cases and no-pump/fan/empty counterexamples, recharge and repeat fault; retain native traces, losses, original ENS and old event statistics.

## 3. Recovery metrics and engineering comparisons (v23_pulses_metrics owns)

Files: tools/model_correctness_v23/{metrics,reporting,comparison}.py and their tests.

- [x] First register carrier-specific recovery: electrical immediate positive-duration restoration preserves real10s/15s events; heat/cold require60s continuous supplied service, justified by sustained thermal flow and original1min native bookkeeping, independent of the old1s comparison band. Report30/120s diagnostics without tuning. Keep candidate and confirmation clocks separate.
- [x] Keep raw material intervals/ENS unchanged; engineering episode envelopes join unconfirmed thermal gaps, with observed/censored/not_applicable/missing/pending statuses. Union uses each component's own confirmation, not an arbitrary common timer.
- [x] Export actual absolute recovery, after external repair wait and after actual usable-source return wait, including status and observation end. Existing V22 RUNS blanks were a missing producer/field mapping, not absence of recovery. Offline references never enter the policy.
- [x] Register costs max(.01USD2001,1e-6*scale), CO2 max(.01kg,1e-6*scale), each terminal UPS/cold/DG shaft inventory max(.001kWh,1e-6*capacity/initialbudget); retain old per-critical ENS bands and noncompensating service/stock hierarchy. These are reporting/selection precision, not physical tolerances or clinical standards.

## 4. Station topology and existing DC/protection (v23_station_topology owns pure modules)

Files: station_paths.py/protection.py and SUPPLY_CONNECTIONS.md; root integrates current state.

- [x] Actual stationAC branch only feeds boiler/GT startup; alias accurately named BOILER_GT_START_AC, no arbitrary full-plant outage. Algebra and real port negative test show UPS cannot indirectly fund EC/ABS because UPS<=actual key building supply plus total electric balance implies plant/general/charge<=nonUPS source. Preserve this valid physics.
- [x] Integrate explicit current functional loss/paid continuous restart status using existing boiler1min/.5kWe. No fake permanent damage or new EC OEM latch. Default physical dependencies shared by all canonical classes; matched L1/L2 equivalence is legitimate and must be stated.
- [x] Actual DC lifecycle tests preserve existing local autonomous start permissions: canceled pending electric commands do not cancel an already triggered engine crank; actual start spends one qualification, failure does not refund, repeat token cannot spend twice, source/DC return cannot recharge spent starts, third request/expiry blocked. Keep1440min/2starts as a finite functional qualification, not battery energy.
- [x] Run small registered0/5min restart and5/20kWe GT start cases (0min ideal bound), including insufficient source and restarted supply; record real clocks/energy.

## 5. New thermal inputs/model and complete IES (v23_thermal owns independent files)

Files: thermal_data.py/thermal_model.py, temperature_protocol.json, their tests; root owns IES coupling.

- [x] Pilot local26.2 on existing compatible derived baseline, original22.1 input read-only. Preserve bad schedule pilot and sizing discovery:5min re-autosizes318/1090 fields. Switch before validation to actual original10min output (<=10min target); all1090 sizing keys/values now exactly original. Never interpolate hourly inputs.
- [x] Before inspection register winter/summer calibration1/22,7/20 and new check2/19,8/22, each baseline/2h/6h, prior2days/tail2days, shared coil scope and multiplier proof. Freeze one justified2-state fit with at most3 initialization/convergence checks before generating check dates. Preserve all original precision targets and bad/no crossing/censoring outcomes.
- [x] Run frozen new dates and report event/recovery6h/max/crossing/recovery time separately. Measured wall/true surface flows only for diagnosis/calibration; runtime low-order states cannot use future walls/temperatures. Stop bounded structural exploration after this one mass state if targets still fail.
- [x] Couple a few actual full IES processes: complete priority/zone allocation including OTHER, multiply/divide each zone once, current room cold deficiency drives thermal state, recovery requests are extra metered plant/pump/fan loads, and actual extra delivery alone pays active recovery. Test source limitation, unavailable fan/pump, restoration and repeated loss. Do not double count original demand or paired reference forcing.

## 6. Final common matrix and content-bearing review delivery

- [x] Register a small targeted same-source/config matrix over normal winter/summer, short/long outages, finite budgets, cold-path failure, restoration/repeat, representative canonicalL1-L4 and R/M/F/C/A. Cover affected pathways; no Cartesian replay or parameter search for A.
- [x] A witness separately reports sustained nonDG sources and finite bridge stock; release uses current support over known switching/start clocks, not actual remaining outage. Preserve optional A if no material advantage.
- [x] Freeze final code/config/parameters and stop concurrent writers before formal IES. Run required cases, preserve each failure/prefix, fix and rerun affected cases in fresh directories when needed. Then one default full regression plus independent raw balance/state/event audit.
- [x] Compact report answers all7 final questions, with exact before/after data and known limits. Deliver source/config/dependency closure or complete baseline-bound patch; small actual PROCESS/native slices, service/storage/recovery/commands, derived thermal IDFs/actual input samples/parameters/error table, source-bus table and run list inside a lightweight review ZIP under400000000bytes. Verify extraction/integrity and clean-directory minimal reproduction; no E-only links as substitute content. No push or main merge.

The source/task freeze protects old results only. A necessary model repair receives a new source identity and affected rerun; it is not blocked by the old V22 freeze. Checked boxes mean executed actions, not universal acceptance of temperature or clinical safety.

## Executed terminal evidence (2026-10-25)

The 41 selected main processes are complete: 35 actual Source04 and six actual Source05, never relabelled. All different-policy formal pairs retain the same actual source. Source05 full default regression: 994 passed, 1 skipped, exit0, 333.42s. The one-mass-state thermal study and frozen new-date validation were executed, but the original precision acceptance FAILED (9/16 room rows pass all targets); the corresponding checked task means work completed, not temperature qualification. Two matched-label native processes and all three binding boiler sensitivities are complete. Final archive integrity and clean-extraction runtime are separate release checks, recorded in PACKAGE_BUILD_RECEIPT.json outside the immutable scientific snapshot; publication remains local only. See MODEL_CORRECTNESS_REPORT.md and the complete attempt/command ledgers for actual outcomes and failure histories.
