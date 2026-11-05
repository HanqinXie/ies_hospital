# V29 Same-version mechanism acceptance implementation plan

> For agentic workers: use subagent-driven-development with exclusive file ownership, actual RED/GREEN for new runtime behavior, independent spec/code and native-math review. The V29 task explicitly authorizes continuous execution; routine design/phase approvals are already provided. Older closed-stage stops and skill handoff questions do not suspend this scope.

**Goal:** Finalize the existing prototype hospital IES model for its declared service/dispatch research scope, using V28 adopted demand and limited actual grid/shared-facility/stock mechanisms.

**Architecture:** Reuse V28 demand/environment and M25 physical/control core without numerical changes unless a concrete defect is reproduced. A small V29 public entry registers existing H0/H1/H2 and only adopted thermal input. Offline source-quality/scope metadata is written after execution and never supplied to the controller. Reuse V28 B_N/D/FD producer-bound evidence. New reports separate hardware fault, functional restriction, protection/interlock and second scripted external event.

**Stack and locations:** Existing Python3.10/NumPy/Pandas/SciPy/Gurobi13.0.2. Reuse linked D:/hospital_ies_v22_20261025 worktree, new sci-v29-mechanism-closeout branch from clean cf45de8cdf3f1713cdab1e337086eaf344579ea0. Scientific baseline09f922840b7cfaef209d693ebfdda3492e16e24a, source837 9cb3ea825662324cf7b718ffca7c581066506907859a5739c7d5a3c366fc18f9 and inputs33 5c6fea468008561acefd81b16d4cd57a8524f7897f861ccb436f1a32adaaeae8. New raw/temp/results E:/hospital_ies_v29_20261027. Preserve commonGit D:/hospital_ies_v21_20261023/.git and prior branches/raw/inputs. No extra full historical copy, C large output, main merge or push.

## Ownership

- Root: sole possible shared-physics writer, V29 __init__/validation/configs/public tests, actual dispatch/freezes/native receipts and final docs/package. No shared-core edit merely for version numbering.
- v29_baseline_audit: read-only task/837/33/archive/reusedB/remote audit, no production edits/model/test/EP.
- v29_mechanism_plan: read-only original scenarios/hardware/physical routes and concise coverage/registration recommendation, no production edits/model/test/EP.
- Source-scope implementer: exclusive new tools/model_review_v29/source_scope.py, tests/test_v29_source_scope.py, inputs/model_review_v29 quality files and extraction receipt. No __init__/validation/config/common core modification. Root integrates only after its actual focused GREEN.
- Later independent reviewer: no scientific writes/models/tests; verify produced records and portable native arithmetic, never approve future ZIP checks in advance.

## 0. Verify baseline and register the scope

- [x] Read full V29 task, V28 review and all three thematic reviews; verify22 indexed payload sizes/SHA and original task ZIP.
- [x] Independently verify current scientific837/33, old31 bytes, old ZIP99,268,822B/c3b116..., commonGit, clean HEAD and remote refs. Reuse already independently extracted V28; do not unzip it again.
- [x] Run only focused unchanged public baseline once: python -B -m pytest -q tests/test_v28_public_contract.py. Keep full old1278 as historical, not V29 result.
- [x] Complete coverage table with exact code/old case/producer/demand/hardware identity; distinguish actual B_D healthy hardware and activeGT2 FD from parser labels. Preserve any failed audit assumptions with corrected exact diagnostic-field definitions.

## 1. Minimal adopted-version public entry and quality/scope metadata

Create tools/model_review_v29/{__init__,validation,source_scope}.py; configs/model_review_v29/cases.json; inputs/model_review_v29/{SOURCE_COOLING_UNMET_HOURS.csv,SOURCE_QUALITY_METADATA.json,ALL55_ZONES_NATIVE_UNMET_SUMMARY.csv}; tests/test_v29_{public_contract,source_scope}.py. No legacy test assertion/tolerance deletion or capacity/price/threshold/control change.

- [x] Add meaningful public RED tests: adopted default mandatory; explicit A/unknown/profile mismatch rejected; H0/H1/H2 inventory matches exact inherited profiles; source fault/time/amplitude and after_cost/thermal_coupling/research_stress_proxy remain frozen; actual parser encodings validate. Tests fail for missing V29 behavior before implementation.
- [x] Implement V29 resolve_case by calling V28 resolve_case then requiring V28_adopted and exact bound formal thermal files. Keep C/M policies and existing operating rule; use public reset/step unchanged. V29 worker calls common.worker(model_version='v25', environment_adapter=V28 with_boundary) with original policy identity and a separate V29 study identity. No new simulator/controller.
- [x] Source quality RED tests cover raw26rows/25uniquehours/55-screen/9.5zoneh, no intersection and a known intersection, partial-hour endpoints, invalid/missing/nonfinite source rejection, input immutability, no deletion/scaling, and unknown preheat remainsnull.
- [x] Implement small offline load_quality(root) and window_scope(root,scenario). Preserve original qualityCSV bytes/zone-hour rows and actual55-zone screening evidence/units. Window is [(start_hour-1)*60, +duration_steps*step_minutes), intersect hourly source support and label actual affected regions. State source cooling is delivery proxy even with no overlap. Integrate local source-reference Laundry/Booster/preheat only from already-bound formal hourly input; upstream energy/cost/CO2 unknownnull, no extra actual IES addition.
- [x] Add source_scope to RESULT and SOURCE_SCOPE.json only after worker completion, including failed/unknown prefix status; never put source future quality or scope into observations/actions. A metadata failure is explicit failure, not a fake completed/zero record. Run only affected new tests, keep original public tests unchanged. Update pytest.ini explicit default file list.

## 2. Actual finite mechanism validation

Freeze source/input/registration before experiments. Initial plan7 main new episodes, with exactly one preregistered optional matched transfer pair (total9) only for an uncovered finite-diesel boundary claim. No quota exhaustion or broad matrix. All windows use original registered background, seed, shocks/fault amplitudes/events, after_cost, current_service_bridge and V28_adopted.

|Pair/purpose|Original source and hardware|Window and events|
|---|---|---|
|Grid normal/fault2|V25 four_L1_grid_winter; exact H1 UPS40kWh/160kW, eta.95/.95|h211,360min; GRID0 at60–180; normal strips events only|
|Shared tower normal/fault2|V24 cold_partial_tower; exact H2 cold60kWh/120kW, eta.95/.95,loss.01/h|h4001,90min; TOWER.01 at15–75; normal strips events only; extend matched tail only if recovery remains unobserved|
|Heat-pump normal/fault2|V28 A_aux_pump_fault migrated to adopted; exactH0|h211,180min; AUX_HEAT_PUMP.5 at60–120; no relabeling A result as B|
|M representative normal1|Same grid-normal H1 window and rules except originalM policy|one supported adopted route check, no global C/M superiority claim|
|Conditional compound pair2|V25 transfer_l2_long_300; exactH0 and300kWh shaft budget|h4001,270min; GRID60–210 and allGT90–210 scripted separately, not endogenous secondary damage|

- [x] Register purpose, exact parent CASE/config/hash and hardware identity before dispatch. First normal pair confirms active relevant facilities and available margins; raw actual proof determines supported claims. Do not vary capacities to provoke startup/failure.
- [x] Root dispatch normally at most2 own IES, >=3GB available physical memory and <=85% commit start gate; retain existing10GB ownRSS/1800s command guard. Resource gate pauses dispatch only, while independent evidence work continues; do not terminate unrelated apps. All attempts/new output directories separate, unknown metricsnull.
- [x] Grid: record current source/output, command generated/arrival/execution, actual startup completion and positiveGT/DG, paid start/station/building power, actual zero grid while absent, UPS power/efficiency/charge/discharge/stock and finite shaft budget/electrical conversion. Grid return/ATS/device availability/actual service/confirmed recovery are distinct.
- [x] Tower: prove normal tower-dependent ABS/EC function, follow event→capacity→actual heat/cold→building fan/pump→P0–P3, preserve cold-store power/energy/loss/recharge and service deficits. No required second device damage/protection; stress_proxy staysfalse. Keep recovery tail actual or unknown, never zero-fill.
- [x] Pump: report adopted request/service and actual/confirmed recovery; compare matched normal, not old heat A fees.
- [x] Decide optional transfer pair once based on remaining study claim: if normal grid does not exerciseDG or if finite budget exhaustion/stock-boundary remains a retained research claim, run original300 pair. Otherwise explicitly limit diesel evidence to actual expenditure and omit pair with reason. Do not rerun150/600 matrix.
- [x] Reuse B_N/D/FD evidence with explicit source09f and same core/input, source quality scope; if real shared-core fix occurs freeze new producer and rerun only affected matchedB group. No unconditional repetition.

## 3. Independent actual arithmetic and completion

- [x] Use small replay over native segments plus unchanged native math. Verify D/A/E, original request and connected/served/short, electric/steam/cold/start/fuel/storage/fees/CO2; no early output, negative inventory, unavailablegrid purchase or hidden future info. Review exact command/state differences, only documented pure solver timing diagnostics excluded in paired content comparisons.
- [x] Run one final default python -B -m pytest -q and clean public normal entry. Record temporary SCI_SOURCE_ROOT if used for optional source test; otherwise honest skips. No full suite repeat for late docs/storage-only changes.
- [x] Write MODEL_REPORT/ASSUMPTIONS/MIGRATION, concise MECHANISM_COVERAGE, adopted/reused provenance, failed/rejected/unexecuted ledger, filediff/real commands/exits. Recommend scoped model finalization only if no required implementation/physics defect remains. Actual unserved service is valid result, not automatically a software bug.
- [x] Stage one self-contained portable code/input/new-native+selected reused-native+minimal replay payload. Reuse lossless compression, existing small pack helpers only; no new packaging/monitor framework. Preserve large sourceSQL and duplicate/deep matrices locally bySHA/omission ledger. Do not include full V28 ZIP/whole annual sources again.
- [ ] One final HOSPITAL_IES_V29_REVIEW_under100MB.zip strictly<100000000bytes, streamallCRC/size/SHA, independent extractonce, clean public entry and readonly replay from extracted paths. External postarchive receipts avoid recursive hash. Final original/source/history checks and no owned model processes.

Self-review: explicit adoption prevents silentA fallback; matching hardware and eventstream prevents inventory/capacity advantage; observation and actual execution remain V28 unchanged; source-quality overlap and localnull boundaries are evaluation metadata; useful functional propagation is distinguished from absent secondary damage; unqualified source details do not block scoped closure. Actual task authorization controls the old-stage/workflow approvals.

## Actual closeout

7new main+3reused actual records passed independent native math;3newmatchedpairs passed. Default1320passed/0skip/431.20s/exit0. Portable843/36 normal entry15min exit0; three readonly replays new7/reused3/source10 exit0,22CSV originalSHA verified. Conditionaltransfer pair intentionally unexecuted with explicit no diesel-endurance claim. No science code changes after freeze8926/default suite. The oneZIP and independent extraction are the external postarchive gate; consult actual final receipts rather than treating this prearchive document as future success.
