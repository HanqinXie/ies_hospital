# V21 SCI engineering implementation plan

> For agentic workers: execute with subagent-driven-development; keep the physical state transition under one writer. The user authorizes continuous work through internal gates without repeated approval.

**Goal:** Explain hospital critical energy continuity, endurance, recovery, tradeoffs and hardware limits under four disturbances with five fairly matched policies.

**Architecture:** Extend the actual M20 executed model under `tools/sci_engineering/`; preserve inherited queues, GT protection, finite DG rotor/shaft accounting and source-field contracts. Add optional shared constraints to the existing physical balance, causal policy adapters, independent scenario design, streamable results and actual run evidence.

**Tech stack:** Windows Python 3.10; NumPy/pandas/SciPy, Gurobi 13.0.2, pytest, psutil; final editable vector figures from actual exported tables.

## Gate 0: baseline and preservation
- [x] Read all 407 task lines, current instructions and V20 specification/field/source documents.
- [x] Verify remote current main and create independent clone/branch; retain dirty complete V20 worktree.
- [ ] Run `python scripts/v20_repository.py verify`, `python -m pytest -q` and one full `publication/reproduce_v20.py --name sci_baseline01` under `tools/p0_audit/run_logged.py`.
- [ ] Save source/input hashes, Git/local differences, software/resource inventory and baseline timings in new evidence.

## Gate 1: source and common physical model
- [ ] `tools/sci_engineering/auxiliary.py`: read source-derived building fan/distribution categories and IDF links; allocate existing power without changing annual total; retain raw proxy excess fields.
- [ ] `tests/test_sci_auxiliary.py`: fail on missing dependency/reallocation behavior; verify total demand preservation, no duplicate shared plant power, absent/recovered auxiliaries and carrier traces.
- [ ] `tools/operating_response/physical_balance.py`: optional M21 constraints on delivered cold/heat and associated electric service; H0 leaves all historical defaults unchanged.
- [ ] `tools/sci_engineering/storage.py`, `execution.py`, `environment.py`: explicit finite UPS and separate cold-storage configuration; debit/charge energy by actual intervals and add depletion boundaries. Tests must cover exhaustion, recharge, no simultaneous free charge/discharge, restoration continuity and policy-identity invariance.
- [ ] Run meaningful old physical regressions plus new red/green regressions; audit an actual normal and fault trajectory before scaling.

## Gate 2: policies and causal interfaces
- [ ] `tools/sci_engineering/policy.py`, `planner.py`: retain C19 migration, add M/F/A and separate reserve/preparation switches; R emits ordinary direct commands through the shared queue.
- [ ] `tests/test_sci_policy.py`: verify independent four mechanism orders, R not replanned, causal identical-observation actions, same command physics independent of identity, delayed permission preserved.
- [ ] Preregister a small reserve/candidate grid; pilot on development data only; freeze selected F/A parameters before confirmation.

## Gate 3: experimental protocol and runner
- [ ] `tools/sci_engineering/design.py`: balance fault/severity/shock/delay/duration/resource independently; map paper L1/L2/L3/L4 to native L1/L3/L2/L4b explicitly.
- [ ] `tools/sci_engineering/runner.py`, `scripts/sci_campaign.py`: append-only attempts, source/input/config/policy hashes, checkpointed actual errors and raw outputs on E; resource measurement and compact summaries.
- [ ] `tests/test_sci_design.py`, `test_sci_runner.py`: no overlapping development/confirmation blocks including tails; frozen inputs reject drift; failed/unknown attempts retained; future event differences do not change current policy action.
- [ ] Fix protocol with actual pilot cost, at least 24 fresh disjoint seasonal source blocks and complete difficult-case coverage; do not claim raw trace counts are independent accidents.

## Gate 4: actual comparison and boundary evidence
- [ ] Execute five-policy main comparison and independent confirmation, paired mechanism 2x2, normal/fault/shock/combined interactions under identical protection, and finite supported sensitivities.
- [ ] Execute duration x initial reserve and demand x cooling/rejection scans; derive necessary power/energy/path conditions, separately identify offline optimistic diagnostics.
- [ ] Execute H1 and bottleneck-selected H2 individually against H0 with C/A (and representative R/F where useful).
- [ ] Audit service requests/delivery/ENS by carrier and priority, disconnection/connected shortage, union interruption durations, initial/endurance/recovery losses, resources, conditional cost/emissions and decision/fallback timings.

## Gate 5: engineering delivery
- [ ] Write SCI_ENGINEERING_REPORT, MODEL_SPECIFICATION, PARAMETER_AND_SOURCE_REGISTER.csv, SCENARIO_AND_POLICY_PROTOCOL and REPRODUCTION with actual results/limits.
- [ ] Export actual source tables and editable vector/process/comparison/mechanism/boundary/hardware figures; visually inspect artifacts.
- [ ] Run supported core and new tests, independently review specification and quality, create a clean runnable directory and execute one complete representative case without original-workspace imports.
- [ ] Preserve ordinary history, review changes and compact release; no credentials/licenses/third-party originals/raw traces in Git. Final archive must remain under 400,000,000 bytes and be member-verified.

Each gate records modified files/diff, actual commands and exits, raw logs, passed/failed/unexecuted checks, assumption changes and limits. An unfinished requirement remains unfinished; a successful solver/test count is not a hospital service result.
