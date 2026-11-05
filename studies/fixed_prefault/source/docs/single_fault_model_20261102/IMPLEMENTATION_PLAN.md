# Single-fault model correction implementation plan

**Goal:** Complete the eight user-approved single-fault model correctness units in NEXT_MILESTONE_CODEX.md.
**Architecture:** Preserve old science at 81d34f8, trace actual runner wrappers, make each confirmed physical/state correction in a separately versioned entry, and validate the real production path. One scientific implementer at a time; independent analysts write only to assigned output directories.
**Stack:** Existing Python, pytest, Gurobi/HiGHS interfaces and original DOE inputs.

- [ ] S0 bind baseline/source/input/environment and run the unchanged full default suite with E-local TEMP/TMP and pytest cache disabled.
- [ ] S1 inspect the actual eight_fault_study runner through readiness/operating/V31/V30/V28/M25/M24/M23/M22 to source. Audit constitutive boundaries and original IDF/SQL provenance.
- [ ] S1 search original healthy normal dispatch support for real boiler steam; label search completeness and distinguish component witness from hospital-system exposure.
- [ ] S2 reproduce storage standing-loss/held-flow subdivision inconsistency with an independent continuous-balance oracle. Only if confirmed, add failing tests and make limits/boundary/commit use one consistent update.
- [ ] S2 reproduce command ready/stop/cancel/same-timestamp counterexamples on actual inherited core; fix only proved violations, preserving original timestamps and six-action contract.
- [ ] S2 check actual auxiliary, refrigeration, rejection and delivered-service ledgers; add executable independent conservation/availability tests for each device and fix reproduced root causes.
- [ ] S2 audit tower ton and pump topology sources, and thermal feedback. Implement only source-supported necessary corrections; record unidentifiable physical extensions.
- [ ] S2 use non-author specification review then independent quality review on exact final code and tests before production.
- [ ] S3 preregister normal/fault/repair cases for GT, positively exposed boiler if available, ABS, one EC, one defined tower object, CHW object, one GRID interruption and one same-accident recovery continuation. No compound external faults or repeated disturbances.
- [ ] S3 pin final source before actual runs; collect all priority/carrier service, actual flows, auxiliary demand, storage, state/queue/protection, event timing, solver fallback and cost.
- [ ] S3 keep 15-minute control fixed and compare physical maximum segment/half segment at relevant handover/storage boundaries; compare continuous quantities and event times, not only binary outcomes.
- [ ] S4 run full default checks on final code; exact source/input preservation checks; final two-role independent acceptance per topic; report remaining unknown qualifications without fabricating them.
- [ ] S4 seal changes, raw receipts, all failures, manifests and final Goal completion evidence; keep branch and worktree as local reviewable delivery.
