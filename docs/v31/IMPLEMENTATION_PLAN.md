# V31 simple preparation closeout implementation plan

Authority: USER_TASK_20261027.txt, supplied by the user as a complete implementation specification. Routine design/implementation and phase transitions are already authorized; no additional design approval is required. This scope supersedes older V30 stop points only for this bounded experiment. No paper, figure, parameter search, MPC, ML, new literature controller, broad C/M replay or default full1363 test run.

Goal: four actual180min trajectories (b05 normal/disturbed M_cooling_permission; b07 normal/disturbed M_steam_permission), matched to eight immutable V30 fix1/C02 N02/D02 C/M runs, then evidence-based engineering closeout and one verified compact ZIP.

Architecture: new tools/model_closeout_v31 modules and a four-case registry. All858 original C02 source files,36 inputs, physical functions, original M/C defaults and V30 evaluation formula remain byte-identical. Cooling changes only the expanded M cooling_preparation bit. Steam uses the same M23 SciPolicy class with a current-observation mailbox; an opt-in core mixin calls the original common preparation first, then raises only queued boiler_steam_lbph to max(common_permission, healthy observed rated capacity), never beyond the original total rating. No writes to actual state, stock, arrived commands or physical outputs. Receipt provenance contains only three currently observable boiler availabilities, their feature clocks and immutable plant rating. Normal queue generation/arrival events provide actual command timing.

Workspace: existing linked D:/hospital_ies_v22_20261025, new sci-v31-simple-preparation branch from clean documentation HEAD e635b919dae9e1c89793a4c29eecbab4e27a05ec; original science producer3252727135645037cedc57ebc188a48d2aa34ccb. Protect D:/hospital_ies_v21_20261023/.git. New raw/temp/evidence E:/hospital_ies_v31_20261027. One physical worker, no C large outputs, respect actual memory. Original V30 evidence and accepted ZIP readonly.

## Tasks and gates

- [x] G0: hash all original858/36 files,8 exact N02/D02 reused runs and GT3 lock. Save user task, old freezes, full method configuration, phase ledger and actual environment/commands. Baseline fixtures are not new trajectories.
- [x] G1: write meaningful failing tests for exact M config, isolated cooling toggle, steam only-one-field change after common prep, observation clock/range/missing rejection, frozen M23 factory and default adapter behavior. Then implement new rules.py, environment.py, validation.py and four-case configs only. Registry retains original180min scenarios, source hours3416/3128,H0, localER+.25 and fixedGT3 fault. Tests explicitly invoked; no shared planner/environment edit.
- [x] G2: run targeted green/default-path regressions and verify every original C02 source/input SHA. Record complete configs, code diff and true new producer. Freeze FROZEN_SOURCE_V31_P01 before any formal run. Save EXPERIMENT_REGISTRATION.json with12rows; old8 are historical_reuse, new4 are new_v31. No outcome-based rule change.
- [x] G3: serial actual four180min public-entry runs from original t0, fresh directories. Log all failures/retries with observed prefix and unknown tail. Native events retain original generation/arrival/actual execution; no new fast lane. If a concrete core defect appears, preserve counterexample and stop dependent reuse until impact is resolved.
- [x] G4: independently read native segments. V30 original formulas for each priority/carrier, pure fee, resources, true terminal and initial preparation charge once. Normal/disturbed prefix compare[0,60), state/core t0 equivalence, separate cost/state preparation window, signed disturbance-minus-own-normal deltas. Raw tiny ENS retained; material thresholds unchanged; no cross-carrier sum or zero-baseline percentages. Mechanism traces join generated/arrived commands and actuals, explicitly check b07 GT3 endogenous exposure and both boiler ready states.
- [ ] G5: Chinese engineering report,12-row full-precision summary and comparisons, all rule/config/failure/command/validation evidence inside one ZIP. Standalone source/input and lossless native evidence; readonly replay does not launch simulation. Verify ZIP CRC/size/SHA plus an independent extracted readonly replay; keep final exact-ZIP checksum outside as necessary. Aim strict100000000byte cap consistent with prior delivery preference. Final explicitly state whether this bounded model development can end, then stop expanding.

## Ownership and source changes

Root only: all new D modules/config/tests, environment/controller composition, registration, physical dispatch, integration and packaging. No existing source file is planned to change. baseline_audit agent owns only E:/.../baseline_audit; evaluation agent only E:/.../evaluation; interface reviewer only E:/.../interface_review. One implementation writer for the physical/request path; agents do not edit shared state transitions.

Source delta policy: allowed_changed_source_files=[]; added files listed separately. Strategy metadata lives in CASE/RESULT and diagnostic receipts, not in physical resolved_model_config. Existing public reset/step/log schema does not change; additive V31 receipts and independent method IDs are documented in MIGRATION.md.

## Verification commands

Targeted RED then GREEN: python -B -m pytest -q tests/test_v31_preparation.py --basetemp E:/hospital_ies_v31_20261027/temp/<fresh-name>.
Default/shared interfaces: explicit tests/test_v30_public.py tests/test_v30_terminal.py tests/test_v30_start_readiness.py tests/test_sci_policy.py tests/test_v23_validation.py as justified by exact imports; extend only for a reproduced issue. Do not relabel historical1363 as a new result.
Formal entry: python -B -m tools.model_closeout_v31.validation --case <registered-id> --output E:/hospital_ies_v31_20261027/runs/<fresh-name>.
Readonly audit: python -B evaluation/audit_v31.py --root <data-root> --manifest EXPERIMENT_REGISTRATION.json --support audit_support --output <fresh-directory>.

Design self-review: four-case scope and fixed default rule satisfy all task clauses; no missing physical parameters are invented. Current health means model's current policy-observable availability fraction, not an OEM certification. Permits are ceilings only; common actual startup/auxiliary/source limits remain unchanged. V30 backgrounds are already observed mechanism-development cases, not blinded evaluation. Full continuous storage/reference control remains outside scope.
