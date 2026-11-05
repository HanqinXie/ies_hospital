# Literature-grounded fault experiments Implementation Plan

> **For agentic workers:** Use subagent-driven-development for independent source
> and data tasks; root alone owns physical execution and integration.

**Goal:** Implement the user's approved first step without replacing the plant.

**Architecture:** Add opt-in parameter/schedule modules; reuse original case
registration, execution and accounting. Keep public hospital observations and
shared-dependency evidence separate from assumed experiment parameters.

**Tech Stack:** Existing Python, NumPy, pytest, PyMuPDF, requests and Gurobi stack.
No new installation, controller, training, database or service is required.

## Task 1: Read-only source and evidence contracts

- [x] Verify clean parent HEAD and create a separate D-drive source worktree.
- [x] Start the unchanged default test entry through E:/.../run_step.py.
- [x] Re-read ECM PDF pp7/8/14/15, verify SHA and save a source contract.
- [x] Audit active shared-support ports and save code line/SHA references.
- [ ] Acquire exact public Figshare v3 hospital data, preserve hashes and failures.
- [ ] Audit raw time keys, missingness and labels; preserve year inconsistencies.
- [ ] Record public UPS data availability without inventing private work orders.

## Task 2: Explicit parameter profiles and interval schedules

Create tools/literature_basis/__init__.py, parameters.py and schedules.py.
Create tests/test_literature_basis_parameters.py and
tests/test_literature_basis_schedules.py. Do not edit the physical core.

- [ ] Write tests first; save the failing command and logs before implementation.
- [ ] Implement `profile(name, allow_gt_proxy=False)` returning a fresh structure.
  Main assets: three gas-boiler ports, hourly probability 0.0039/100 and duration
  24 h; one ABS port, 0.0220/100 and 48 h. An explicit GT proxy adds three GT
  ports at 0.0335/100 and 34 h with an incompatible-equipment warning.
- [ ] Preserve raw percent units, source SHA, Table2 and source-chain limitations.
  Unspecified failure mechanisms and residual common cause remain null.
- [ ] Implement `unavailable_intervals(draws, probability, duration_hours)`.
  Enumerate 0-based source hours. Strictly `u < probability` triggers. Every
  provided hour is sampled, even while down; keep raw triggers and merge overlap
  or touching half-open intervals. Preserve endpoints beyond the sample horizon.
- [ ] Implement `annual_schedule(profile, seed)` for exactly 8760 draws per
  asset, deterministic per-asset streams, raw trigger trace, interval unions,
  in-year/carryout durations, and availability change events in minutes.
- [ ] Implement `calendar_events(intervals, observation_hours)` with initial-up
  default, year-end clipped observation and uncropped source carryout. Events at
  the same time combine by asset; unknown probabilities never become zero.
- [ ] Validate finite numbers, integer seed, correct horizon, u in [0,1), positive
  duration, distinct supported ports, and no automatic GT proxy acceptance.
- [ ] Run green tests and independent edge-case review before generating outputs.

Required numerical tests include:

```python
p = 0.0039 / 100
# source hours 1,2,3,4 correspond to starts 0,1,2,3
assert raw_starts([0, p/2, p, .5], p) == [0, 1]
assert merge([(0, 24), (1, 25)]) == [(0, 25)]
assert split_year_tail([(8759, 8783)], 8760) == (1, 23)
# Restores at a shared endpoint must not create a spurious up interval.
assert merge([(0, 24), (24, 48)]) == [(0, 48)]
```

Generate 12 predetermined seeds (2026103001 through 2026103012) for each of the
main profile and separately labeled opt-in proxy. Report generated schedules and
their own trigger/down-hour summaries only; never estimate hospital reliability.

## Task 3: Public data and dependency interpretation

- [ ] Preserve all raw CSV rows, including ambiguous or conflicting duplicate keys.
- [ ] Test conservative label-run handling on synthetic contiguous, gap, duplicate,
  conflicting-label and edge cases before applying it to the actual CSV.
- [ ] Separate raw row counts, unique time support and eligible observed label runs.
- [ ] Persist the paper/file year discrepancy and all unacquired private material.
- [ ] Bind the dependency register and ECM source contract in a source manifest.
- [ ] Review source compatibility and unknowns independently; do not modify any
  physical capacity or adopt optional external data as a new DOE load series.

## Task 4: Registered matched physical checks

Create tools/literature_basis/study.py and tests/test_literature_basis_study.py.
Save every case outside the source tree under this E directory.

- [ ] Write failing tests for fixed cases, pairs, tail completeness, source support,
  immutable parameters, and no future-script observation. Use the existing
  canonical annual-readiness validation; do not duplicate its physical model.
- [ ] Build winter boiler cases using source_id b07_L4_GT_ER_disturbed_M,
  start_hour33/H0/M_ready, duration1620 min; fault BOILER_1=0 at60 and 1 at1500.
- [ ] Build absorption cases using source_id b03_L2_tower_disturbed_M,
  start_hour4137/H2/M_ready, duration3060 min; fault ABS=0 at60 and 1 at2940.
- [ ] Build shared-tower cases using the same summer source/H2/M_ready,
  duration180 min; fault TOWER=0 at60 and 1 at120.
- [ ] Build shared-CHW cases using the same summer source/H0/M_ready,
  duration180 min; fault AUX_CHW_PUMP=0 at60 and 1 at120.
- [ ] Each healthy case has no event. Each fault is a conditional mechanism
  check. No extra constituent cold-machine fault is added to the shared cause.
- [ ] Keep each pair fixed in source/configuration/rule/initialization/wait.
- [ ] Run all new tests and the existing annual-readiness tests; save exact logs.
- [ ] Commit and freeze source/input/plan/case/evidence identities.

## Task 5: Bounded serial execution and independent accounting

Use a new E-only dispatcher with the existing SHA-verified Windows OwnedProcess
implementation; never reuse the previous goal's closed attempt ledger or budget.
Root registers the helper and real process/attempt/resource limits before launch.

- [ ] Test admission/rejection and failure-preserving terminal status using fake
  command processes; keep approvals, mutations and native execution sequential.
- [ ] Run the 3 h matched pairs first, with a new append-only attempts log.
- [ ] Analyze each original producer with tools.annual_readiness.analysis using
  this source root; preserve original metrics and all unknown checks.
- [ ] Re-estimate time, RAM and disk before admitting 27 h and 51 h pairs.
- [ ] Launch only one owned native process tree, preserve partial/failure outputs,
  and never silently turn missing data into a completed zero row.
- [ ] Use the frozen independent engineering readout against the new case view,
  correct source root and new native analysis; check all statuses explicitly.
- [ ] Report each P0/P1 carrier, P2/P3 tradeoff, actual inventory chain, fees,
  hardware state, observed horizon and unknown tail; no superiority claim is needed.

## Task 6: Close the approved goal

- [ ] Run current original `python -m pytest -q` once after implementation and
  meaningful new helper checks; do not cite parent test totals as current results.
- [ ] Verify original 899 source files, original inputs, parent workspaces, prior
  receipts and all source PDFs are unchanged. New additive files remain isolated.
- [ ] Confirm all physical attempt keys have terminal receipts and no owned native
  process or active lock remains; do not kill unrelated system processes.
- [ ] Preserve any system-process visibility exception with independent identity,
  not a rewritten pass. Do not repeat the prior overly broad scanner as a new fix.
- [ ] Save a compact scoped result with source/evidence/run/log references. Keep
  failed, unexecuted, unknown and private-unavailable entries visible.
- [ ] Return engineering-Chinese results directly in chat, including annotation1.
  No archive, publication, original final12 evaluation or full8760 run is added.
