# V25 bounded model correction implementation plan

**Goal:** Correct thermal output semantics, make finite-source return preparation timely, define a transparent sustained-critical reserve option, and validate representative normal/L1–L4 behavior without altering physical capacities or known delays.

**Architecture:** Continue V24 in its existing linked worktree on `sci-v25-model-correction`. Preserve legacy behavior explicitly for matched comparisons; add only bounded V25 hooks and a single shared physical implementation. Thermal identification/observation is independent and remains disabled online unless qualified. One writer owns shared execution/storage code; root owns registry, runner integration and serial source freezes.

**Stack:** Existing Python3.10/Gurobi13.0.2/EnergyPlus26.2, original28 inputs, E-drive data. Task package is fully staged in E:/hospital_ies_v25_20261026/task_package. No reinstall, baseline whole-suite rerun, plots/paper, MPC/ML, future failure/load/repair inputs, or remote changes.

## Baseline and authorization

- [x] Read task TXT20930bytes SHA b0a16e1424e6be3451551d3faebeaa7a196b22f75eca5ad7ef63cdc992df30d8, review and JSON in full; taskZIP SHA0c55f2e9d81107becb044d88b3ec42949f0dec5cc137608fd9011dd3e0ac39da. Retain user probe/CSV as independent reference.
- [x] Live clean V24 HEADc54f852120f57acf133609d81f35a4229b0f3d89, all758source/28input match c8fc844d/659ecc48. Existing linked Git common directory stays intact. New branch created, old branch unchanged.
- [x] Read probe and reproduce36row frozen diagnosis locally before changing thermal numerical code. Do not duplicate the accepted26run accounting audit.

## Design choices and boundaries

Thermal alternatives: Detailed endpoint targets are useful for state diagnostics but cannot replace original mean-temperature acceptance. Actual discrete HVAC-endpoint weighted reports are the preferred reference contract; continuous analytical means are diagnostic approximations requiring an explicit discrepancy bound. Keep two/three-state passive structures. Fit only after output reconstruction tests and a preregistered parameter/start budget; completed reference substeps are offline data, never future online schedules. Calibration failure triggers the mandatory limited physical thermal-port checks, not indefinite fitting or a total task stop.

Transfer alternatives: fixed30min waiting ignores remaining DG resource; global predictive scheduling would violate scope. Use a causal current-resource deadline with existing destination proof, arrived permission, preparation duration and actual/predeclared conservative shaft rate. Preserve powered RETURN_PREP, mandatory OPEN and paid boiler recovery; repeat eligibility consistently at preparation completion.

Reserve alternatives: hardware derating changes the physical comparison; output-recipient labels alone miss source substitution. Add one sustained-critical operating option with a declared research duration, explicit unmet reserve and a common-balance no-storage reference. Current P0/P1 and necessary actual restoration stay ahead of reserve; general service may use genuine surplus. Existing V24 short bridge remains the baseline, with at most one additional duration sensitivity justified by actual bottlenecks.

## P0 thermal ownership and gates

Owner thermal agent: new `tools/model_correction_v25/thermal*.py`, `tests/test_v25_thermal*.py`, thermal configs and E thermal evidence. Keep original M23/V24 outputs immutable; root alone edits pytest.ini and main registry. Official v26.2 source and installed version must be checked.

- [x] Reproduce user frozen probe and inspect original ESO/IDF/SQL/thermal functions.
- [x] Register exact output mapping, time contract and6original EP additions max initially (Jan22/Jul20 normal/2h/6h), all4OR once. Read original Detailed if sufficient. Verify1090sizing and forcing unchanged where applicable.
- [x] RED→GREEN tests: single/nonuniform substeps, rapid rises/falls, complete buckets/end stamps, aggregate normal/fault separately then subtract, online own-clock advancement without future reference schedules.
- [x] Prove Detailed Zone Air Temperature weighted-by-actual-HVAC-duration equals reported Timestep Zone Mean Air Temperature by normal/event/recovery windows. Preserve actual fractional times and state-end vs reported-output values.
- [x] Preregister positive/passive parameter bounds and limited starts, then actually fit original calibration across events with one parameter set per zone. Report all original metrics/gates; no selective target changes.
- [x] Only after qualifying calibration freeze, register two genuinely new summer dates/strong interventions. If unqualified, report unqualified and complete bounded cooling-limit/fan-path/restoration EP→netH-C port checks anyway. No default coupling without both interface and precision gates.

## P1/P2 sole common-core writer and gates

Owner core agent: V25 environment/storage modules plus necessary original execution/physical-balance hooks and corresponding `test_v25_transfer*`/`test_v25_reserve*`. Root owns common validation integration/configs to avoid conflicts. No concurrent common transition edits.

- [x] Trace real300case and reproduce delayed powered preparation with a regression before implementation; preserve original V24 execution under an explicit compatible option.
- [x] Minimal deadline/qualification fix with healthy-source/permission-loss/prep-recheck/nearly-empty/zero-rate tests. Verify OPEN remains0.25min, no free overlap, realGT only, paid1min boiler restart when readiness lost.
- [x] Register sustained design duration, physically correct per-branch demand/efficiencies/auxiliary accounting and reserve shortfall before implementing new rule. Keep existing bridge target and correct obsolete reserve-reference prose.
- [x] RED→GREEN substitution tests: storage→critical releasing another source→general must count; current critical shortages always unlock stock; no altered12carrier objective hierarchy or fictitious buildingUPS→station supply.
- [x] Root integrates explicit V25 environment into the existing common runner with legacy defaults unchanged. Add registered profiles/options, new model identity and consistent actual/native diagnostics.

## Representative execution and evaluation

Independent read-only scope agent inventories existing scenarios and proposes a small preregistered matrix, not a Cartesian search. Four-class regression is distinct from V24 staged GRID+exogenousGT compound diagnosis.

- [x] Register root events, endogenous consequences and acceptance before outputs: normal/L1single-fault/L2real dependency/L3demand-only/L4combined, using original supported definitions. Do not classify by outcome.
- [x] Freeze source/inputs only when writers stop. Run actual300legacy/new pair; retain matched150/600/short/normal with explicit influence justification. Representative four-class cases plus UPS20/40 and40power80/160, cold60power60/120 old/new option pairs; add R/M only for a specific unresolved question.
- [x] Independent actual-interval service/stock/DG/fees/CO2 review only for new runs. Preserve failed/prefix/unknown results, current-vs-terminal stock, initial preparation and actual recharge separately. No forced monotonic wins.
- [x] Default `python -B -m pytest -q` includes all new key tests, E basetemp, real logs/exit codes. Run affected targeted gates first; fix failures and rerun affected portions without threshold relaxation.

## One self-contained delivery

- [x] Brief Chinese report answers true corrections, rule changes, scope/gates and minimal next work; original failures/calibration metrics and known unknowns retained.
- Payload prepared for one ZIP strictly<100000000bytes: rootREADME, current necessary runnable code/28inputs/configs, report/assumptions/final acceptance inside, small original Detailed/Timestep data, original PROCESS/native intervals and final tables. Large local RAW/SQL/ESO/CAS have simple path/bytes/SHA omission list.
- Actual clean minimum replay completed before final sealing, add its actual receipt inside payload; no self-hashing acceptance/manifest/archive cycle. Final sealed CRC/memberSHA/size once, report ZIP ownSHA in final chat. Preserve any failure if encountered, no watcher/layered packaging development or unnecessary repeated physics.

## Actual execution closure (pre-seal source record)

Source03 science47345730795cc15eda9e5b15071ddbedb65e916e4341e561c1f51148c75ea278 and original inputs659ecc488bda87c9338afde283cbb74135ca2ebdf2769ce463d0e61f0c6288d8 remain frozen. Default tests1108passed/1optional-skipped, exit0; affected core112 and thermal63 passed. Final22 actual episodes/2948 native intervals independently checked once; 5 P1 source02 plus17 source03, five P2 pairs all source03. Source02 six superseded successes and two90min verified-prefix/180min unknown failures remain history, not final substitutes.

P0 output mapping qualified; both fitted structures summer1/8 and full thermal interface UNQUALIFIED. The dependent new-date gate was therefore not executed; default thermal_coupling=None. Completed mandatory bounded port evidence includes one rejected intervention. L4 root state/shock applied, active boiler path unexcited; no permanent-damage or clinical claim. Numerical near-full tiny intervals remain explicitly limited to minute-scale evaluation.

The single clean staged public UPS20/sustained15min replay and thermal readonly replay actually exited0. Final README/report/assumptions/acceptance are inside the one payload. The first DEFLATE estimate was103420548bytes before final tables, so only12 redundant CSV views for six superseded source02 P2 histories were omitted with local path/size/SHA; every final22 PROCESS and all30 published native prefixes remain. Original research files were not deleted.

This plan is frozen before archive sealing to avoid ZIP/self-hash cycles. One sealing command will enforce actual bytes<100000000 and one readonly CRC/member-SHA validation afterward; archive own SHA/actual size belong in the final user response. No additional model calculation or approval checkpoint remains.
