# V20 load accounting correction implementation plan

Submission-time scientific closure: core_gate02 passed463 tests with unchanged366 production /241 test /28 runtime-input bindings. All148 registered batches /406 pairs actually completed and strictly qualified; FINAL01 has406 eligible completed terminals and0 unknown/not-started. The independent pre-push candidate passed463 core tests and a full600-minute E replay (94 scalars exactly equal). Normal009's scientific result passed but its failed logger leaves its M20 resource peak unknown. Old normal03 intentional125, S3failed_unknown, idle RUN01stop15 and PLAN02waiting3 remain preserved. Deadline-corrected L72_RUN02 independently met the stable resource gate, launched008 once, drained exit0 and qualified all148/406. New analysis03 uniquely collected12 normal results after188 actual guards passed; old analysis02 remains prepared/unexecuted. Final publication-layer suite actually passed159 tests plus7 subtests. Git tree replacement and post-push clone verification occur after this submission snapshot; their actual commands/results are delivered separately in final publication_checks, not prefilled into this ordinary commit.

> For agentic workers: execute independent evidence tasks in parallel; serialize modifications to the physical grouping/core. The user's 2026-10-19 task authorizes continuous implementation, validation, reruns, publication and push without intermediate approval.

**Goal:** Correct source-field multiplier use, measure the impact under unchanged plant/control/service constraints, and replace the current main tree with an auditable V20 release.

**Architecture:** Work in `D:/hospital_ies_v20_20261019` from V19 commit `01baf68c3e12d0336a5d9d54026ca479a7023b6d`. Preserve V19 via `v19-model-closeout`; retain original CSV/IDF/EPW bytes and old evidence. Change field accounting at the actual grouping interface; use a separately named V20 model, policy registrations, configuration, freeze, result directory and test records. Reuse the existing execution/solver implementation and validate unchanged physical contracts. New evidence must distinguish known arithmetic, uncertain field boundaries, and actual rerun outcomes.

**Tech stack:** Windows Python 3.10, pandas/NumPy/SciPy, existing Gurobi backend, pytest, Git.

## 1. Baseline and evidence

- [x] Read user task, project instructions, V19 README/model/reproduction/entry points; compare remote HEAD.
- [x] Create isolated branch/worktree; save V19 tag before replacing main.
- [x] Record original input hashes and current configuration in `outputs/v20_load_accounting_20261019_run01/provenance/`.
- [x] Reproduce duplicate electrical multiplication on actual source rows. Write a failing regression against old `build_zone_groups` before implementing the correction; do not hard-code the user's example totals as pass conditions.
- [x] Trace each of four fields through original export script, SQL dictionary/data, IDF Zone/ZoneList/ZoneGroup definitions. Store source paths/hashes, exact query definitions and comparisons under V20 provenance.

## 2. Grouping implementation and diagnostics

- [x] Modify `src/hospital_ies/resilience.py` only after source evidence: electrical meter fields are already multiplied; heating and cooling use their independently demonstrated semantics. Keep the original four CSVs unchanged.
- [x] Add multiplier 1/4/5/6 regressions, priority and nonnegative/residual checks, raw-proxy excess/compression diagnostics, and representative-zone thermal scaling invariance tests. Keep solver tolerances and physical capacities unchanged.
- [x] Produce annual and authorized representative-hour before/after arithmetic tables, raw pre-compression margins and compression-hour ledger. Separate the annual data-accounting audit from scenario/strategy observations; sealed final12 are never used as evaluation or controls.
- [x] Explain any changed historical test assumptions; do not weaken assertions or silently exclude core failures.

## 3. New execution identity and freeze

- [x] Establish `tools/load_accounting/` entry points and `M20_load_accounting`, four M20 policy identities preserving E/K1/C19/C18 preferences. Register new source/input/config/test identities, rather than editing V19 freeze records.
- [x] Reuse M19 execution and planner logic with only explicit identity/routing adapters as needed; inspect actual differences in conversion, queue, contacts, protection, DG shaft budget and service criteria.
- [x] Run applicable original core tests plus new regressions. Record exact command/exit/raw logs and bind current production/tests. Freeze before comparative runs.

## 4. Registered reruns

- [x] Register priority panel of 379 pairs: main80×3=240, long8×3=24, normal4×3=12, seven additional C18 compatibility pairs, and fixed old eight-background32×3=96. These backgrounds are seen rechecks, not a new independent confirmation set.
- [x] Rerun propagation18, OPEN1 six and contact025 three; all27 are strictly qualified with registered unique clock/propagation differences. Keep all failure/unknown/not-started records and checkpoint every pair; the remaining normal windows run serially.
- [x] Use identical scenario definitions and physical configuration except evidenced field accounting. All406 qualified; no new policy search or capacity/threshold/shock/tolerance changes.

## 5. Results and publication

- [x] Export carrier/priority requested-delivered-ENS, disconnected vs connected-unserved, material minutes, P2/P3 service, source energy, DG budget/clocks, fees and conditional operating CO2 with complete denominators.
- [x] Compare only matching windows and source-bound V19 outcomes; explain arithmetic effects versus new dispatch consequences and strategy-ranking changes, including lost advantages.
- [x] Write data correction, engineering results, model/assumptions and reproduction documents. Replace outdated homepage/entry points and redundant old current-tree evidence; retain required inherited runtime dependencies until verified migrated or unnecessary.
- [ ] Prepare explicit release selection, scan staged additions/modifications/deletions, secrets and large artifacts. Preserve original inputs and local large raw.
- [ ] Review; create one ordinary replacement commit on main with existing history retained; push only after current checks. Re-clone from GitHub, verify exact contents and rerun core gate plus one full representative dispatch from that clone.
- [ ] Deliver actual engineering implications, run status, limitations/remaining paper requirements, main SHA and V19 tag. No claims based on prior version's test numbers.

The remaining release checkboxes are intentionally a pre-commit snapshot. The final stage/index audit, actual ordinary commit/push, fresh GitHub clone core/replay logs and strict sub200,000,000-byte archive receipt are time-later evidence retained in the final delivery; no second main commit is made just to backfill them.
