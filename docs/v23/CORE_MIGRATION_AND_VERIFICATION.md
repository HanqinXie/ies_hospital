# M23 shared physical-core migration

The new opt-in entry points are `tools.model_correctness_v23.environment` and
`tools.model_correctness_v23.policy`. `reset(scenario=..., seed=...)`, `step`,
`decision_snapshot`, `run_episode`, the original four-priority service ledger,
and actual M21 storage receipts remain available. Completed native prefixes
and generated command metadata carry `M23_model_correctness`; original M22
results are not modified.

## Conditional permissions and actual execution

Only M23 changes `boiler_steam_lbph` from the M22 fixed setting-or-trip to an
**arrived conditional ceiling**. The ordinary requested target is preserved.
With preparation enabled, a newly generated command also retains the ceiling
needed for the currently observed P0/P1 heat demand. EC ceilings retain current
critical cold preparation and can include currently observed extra thermal
recovery demand. These commands use the existing decision clock and queue;
pending permissions are never supplied as actual energy or execution rights.

Actual boiler output can modulate below its arrived ceiling. It requires its
real station branch, qualified boiler hardware, continuous paid restart, fixed
and variable electric auxiliary power, and an actual heat or ABS steam sink.
The original unused-steam port can dispose of HRSG steam but cannot justify
boiler output. Positive standby permission contributes no fixed boiler sink to
the actual balance or no-export interlock. M23 removes the old fixed-setting
continuity preference; holding a ceiling does not prefer burning extra gas.

The execution compiler solves its small off/run domains independently from
one input snapshot. A run solution with zero steam is rejected without an
invented positive minimum. Each candidate's own backend evidence remains in
`m23_boiler_domains`. The compiler uses the original solver objectives and
lock widths to compare candidates, without rounding service objectives or
putting a running flag ahead of cost. Startup requests remain actual paid
ports in either applicable domain. The command compiler re-solves its full
priority hierarchy after rejecting any zero-output station-on candidate;
prior candidate locks are not reused. Candidate rejection, registered-domain
exhaustion and compiler/solver errors are separate. No global physical or
global nonlinear optimum claim is made.

## Empty cold store and consistent actual endpoints

The reproduced M22 pulse source was the empty-plus-continuing-critical-deficit
fallback to charge. Its late charge objective used the original normalized
ENS lock allowance to put a tiny real energy packet into the store, then the
next interval discharged it at full power for microseconds. It was not free
energy through the electric balance tolerance. M23 selects idle in that empty
deficit state. Actual replenishment after adequate supply can still reopen
discharge; the original inventory availability threshold is unchanged.

The M23 endpoint helper repeatedly solves from the same uncommitted snapshot
and closes actual powers, duration and inventory together, bounded by the next
real event/command/native integration boundary. It allows both shortening and
extension inside that boundary. The final interval alone commits stock,
source work and clocks. Trial receipts state `inventory_committed=false`.
Original conservation tolerances, 15-minute upper decisions and command
arrival clocks are unchanged. The active inherited execution fork is
`tools/essential_fixes/execution.py`; the shared M15 path has the same opt-in
hook. Diesel split trials re-close the storage endpoint before settlement.

## Thermal coupling and restart/DC lifecycle

The optional `model_correctness_v23.thermal_coupling` configuration requires
`reference_csv` and `parameters_json`, with optional `max_recovery_kW` and
`recovery_horizon_h`. Relative paths resolve from the project root. The CSV
uses native `elapsed_interval_start_minute`, `zone`, `heating_kW` and
`cooling_kW`; the registered small study also binds its source date/hour and
SHA outside the online controller. No room/wall truth or future accident
state enters this interface. The four ORs and OTHER account for all original
P0 demand; the actual P0 H/C deliveries and separately paid extra cold advance
the diagnostic state. Actual segments split at native 10-minute boundaries.
Each reset rebuilds the frozen initial thermal state, and no thermal state
offsets critical ENS or acts as a safety controller.

Defaults are `preparation=true`, `storage_fix=true`,
`boiler_domain_search=true`, `thermal_coupling=null`. Feature-off runs are
explicit mechanism counterfactuals, not same-model strategy comparisons.
Boiler restart/DC finite rights retain their inherited registered quantities.
Actual tests cover continuous timer reset during a power loss and a full
second restart after restoration. An arrived zero electric command does not
cancel an already triggered local DG crank; failed starts consume finite DC
qualifications, hardware restoration does not refund them, and a third actual
start is blocked after two spent attempts.

## Actual targeted evidence before final root-owned freeze

Logs are retained under `E:/hospital_ies_v23_20261025/`; the root review package
must include their relevant contents instead of relying on local paths alone.
Every pytest invocation used project root, E-drive TEMP/TMP/pycache and a fresh
explicit E-drive `--basetemp`. No old result or original input was written.

| Raw log | Actual outcome |
|---|---|
| core_red.log | Initial missing M23 features plus real positive/no-sink failures: 13 failed. Shell printed the log and did not retain pytest exit separately. |
| core_green01.log | 11 passed, 2 new-fixture mistakes; exit 1. One exact-boundary fixture needed no iteration; the other incorrectly fixed boiler auxiliary power despite original joint E/H priorities. |
| core_green02.log | 32 passed, 47.39 s, exit 0. |
| core_extra_red.log | 4 passed, 3 failed, exit 1; real active fork still over-drafted storage, plus two isolated fixture setup issues. |
| core_extra_green01.log | 7 passed, 6.09 s, exit 0; real depletion, legal recharge, positive execution and 1/.5-minute convergence. |
| core_thermal_reset_red.log | Repeat reset retained thermal revision: 1 failed, exit 1. |
| core_final01.log | 40 passed, 53.75 s, exit 0. |
| core_recovery_headroom_red.log | Missing ordinary queued extra-cold headroom: 1 failed, exit 1. |
| core_thermal_coupling_green.log | 2 passed, 4.05 s, exit 0. |
| core_lifecycle_actual.log | 3 passed, 5.31 s, exit 0; actual second restart and DG failed-start/DC lifecycle. |
| core_run_preference_red02.log | Actual upper compiler burned 350.877 lb/h boiler steam despite adequate HRSG: 1 failed, exit 1. |
| core_objectives_green.log | 21 passed, 38.39 s, exit 0 after original-objective and continuity correction. |
| core_identity_red.log | Inherited outer wrapper overwrote native segment model ID: 1 failed, exit 1. |
| core_final02.log | Final affected set: 44 passed, 59.64 s, exit 0. Source/test writers stopped after this run; root owns the complete matrix and default regression. |

New tests are `test_v23_recovery.py`, `test_v23_depletion.py` and
`test_v23_domains.py`. Existing M22 assertions and tolerances were retained.
Final full regression and complete registered scenario matrix are root-owned;
these short tests do not replace them or demonstrate a globally correct model.

The isolated `grid_not_connected` test has GRID hardware unavailable and no DG.
It verifies absence of a connected source, but is not independently the healthy
hardware/still-open contact case. The latter must be audited in the complete
067 recovery prefix between hardware repair and actual grid connection, with
the hardware and connection ledgers kept separate.

## Source02 release qualification and resident evidence correction

The final read-only review found an unfinished Task8.5 boundary in inherited
M22 `non_diesel_witness`: a currently connected source plus 0.001 kWh UPS
inventory could produce a nominally ready instantaneous witness while the
actual 10 kW UPS draw supported only 0.342 seconds. The inherited A rule used
that ready flag directly. An analogous emptying cold-store witness also
passed. This is not sufficient evidence for continued non-DG support.

M23 now reports three distinct blocks: `instantaneous`, `sustained_non_DG`
and `finite_bridge`. The independent sustained solve disables UPS/cold-store
discharge and charging sinks and excludes transient start/recovery sinks,
while retaining current connected source ports, current hardware, arrived
converter permissions, original priorities and numerical constraints.
Top-level `ready` uses only that sustained current-condition proof. It does
not claim future loads, faults or natural-gas supply are guaranteed.

Finite bridges retain actual draw, inventory, efficiencies, original standing
losses and time to the first store boundary. Required support clocks come
only from an observed grid transition or an already triggered, currently paid
GT start. Unacknowledged command arrival and true remaining outage duration
are not used. Idle inventory has a null support duration, not infinite support.
These clocks remain conditional diagnostics; target capacity is explicitly
unproven and bridge support never independently authorizes reserve release.
The E-only RED set failed five cases against source01; the candidate passed
five after runtime-only method injection before the D-tree change was allowed.

The resource diagnosis separately found a successful 40-layer upper candidate
duplicated into the new command-domain diagnostic, then command/decision and
event records. In one current C-command probe with no clock advance, the three
retained roots occupied 115,613,808 bytes, including 153,180 validation-row
occurrences. M23 now keeps successful domain `validation.rows` once in SHA
named gzip blobs under an explicit runtime `solver_evidence_directory`; the
regular ordered solver-call sidecar remains independent. Layer/attempt
metadata and lossless references stay in place. Failed layers and rejected
candidates retain their complete inline evidence. No mathematical input,
objective, step, threshold or physical tolerance changes for this correction.

With both sidecars enabled, the same probe retained 6,884,338 bytes and zero
inline successful validation-row lists in these roots. The domain directory
contained 43 closed blobs totaling 660,362 compressed bytes; the original
ordered sidecar contained 52 entries/51,425 rows and 623,730 compressed bytes.
The row restoration tests check exact JSON-equivalent recovery and hash/row
counts. These are one-command diagnostics, not a long-run resource guarantee.
Evidence: `release_witness_fix/RESIDENT_EVIDENCE_PROFILE.json` and
`RESIDENT_EVIDENCE_FIXED.json` under the E evidence root.

`OperatingHospitalIESEnv` also accepts the optional runtime
`actual_segment_observer`. It runs only on the outermost `advance` unwind,
after original native service ledgers, physical/thermal settlement and M23
identity completion. Nested advances do not repeat segments. This enables
the runner to flush small actual-prefix records without exposing anything to
the controller or including solver rows in each prefix.

Source02 final affected regression was actually run after these changes:
52 passed in 65.02 seconds, exit 0, with `git diff --check` exit 0. Raw log:
`E:/hospital_ies_v23_20261025/release_witness_fix/CORE_RELEASE_EVIDENCE_FINAL.log`.
The root process owns the new full default regression, resource monitoring,
formal CLI cases and final source binding; old source01 results remain separate.

## Source03 evidence I/O and zero-duration boundary correction

A later source02 fault-injection review found that the execution-domain
`try` block also included writing the successful OFF candidate's evidence.
An injected first-write OSError could therefore reject valid OFF and select
RUN, changing the outcome for a logging failure. The actual formal source02
runs had no such I/O error and remain valid under their own source binding.
The new test set reproduced four failures plus one already-correct ordered
sidecar propagation path. Source03 separates successful evidence writing
from the solver exception boundary and passes OSError through domain and
M23 instantaneous/sustained witness probes. A native wrapper records the
original OSError and aborts without advancing the clock or inventory; a
logging failure cannot become a physical candidate rejection or ready=false
fallback. The M22 default behavior remains unchanged through an opt-in hook.

The registered zero-minute boiler restart sensitivity exposed a second
independent defect: inherited scheduling repeatedly registered
`time+1e-6 minute` while the boiler was unready, even with no station power.
This produced millions of false boundaries during a finite outage. The
source02 sensitivity attempt was terminated with its actual 15-minute prefix
and remaining 75 minutes unknown; it is not a completed zero-ENS trajectory.

Source03 registers no restart-duration boundary for the zero-minute ideal
bound. Its immediate qualification uses only current arrived permission,
current boiler/branch availability and a currently connected non-UPS source.
Healthy grid hardware while the contact remains open does not qualify it.
There is no zero-duration startup energy sink; actual positive boiler output
still pays all fixed/variable operating auxiliaries in the original balance.
The original 1- and 5-minute continuously paid restart clocks are unchanged.

The actual targeted source03 set passed 14 tests in 11.12 seconds, exit 0,
including five I/O paths, three zero-duration positive/negative processes,
the original positive 1/5-minute duration checks and four boiler-domain cases.
One earlier fixture correctly could not reconnect because R had issued zero
grid permission during hardware outage; the hardware/OPEN-contact test now
explicitly holds a lawful already-arrived grid ceiling independent of return
time. No physical threshold or assertion tolerance was changed. Logs:
`domain_io_fix/RED.log`, `ZERO_RESTART_RED.log`, `SOURCE03_TARGETED01.log` and
`SOURCE03_TARGETED02.log` under the E evidence root. Source/test writers stopped
after the final targeted run, and `git diff --check` returned exit 0. Root owns
the source03 full regression, registered sensitivities and same-final-source
formal strategy comparisons; no source02 record is relabeled as source03.

## Source04 export closure only

The source03 wrapper omitted the M22 post-export `m21_initial_storage` field.
The actual initial-stock event remained intact: in the H2 cold example it
recorded 63.15789473684211 kWh paid charging input for 60 kWh stored cold.
Because downstream accounting reads the top-level field, the omission dropped
the conditional preparation purchase from its cost and CO2 boundary. The
independent source03 audit retained under `pulse_metrics/` calculated the
missing 15.091310837118254 kWh grid purchase, 1.3968517310836657 USD2001 and
2.9329660969788285 kg conditional CO2. Old RAW/RESULT files remain unchanged.
Source04 attaches the same actual `initial_charged_input_kWh` properties as
the M22 wrapper; it does not reconstruct or change physical stock.

The other source03 failure occurred after the complete 720-minute thermal
process: the legacy exporter recursively called `clean` over each entire
subtree, then repeated that operation at child nodes. Source04 replaces only
that output-normalization function with a single traversal in the M23 route.
An isolated clone retains the original `run_trajectory.__code__` and all
controller/queue/step logic; a module proxy supplies only the new `json_clean`
binding. The original `dataset_export.json_clean` and old-model defaults stay
unchanged. Dictionary keys, tuples/sets, ordinary NumPy arrays/scalars and
nonfinite-to-null semantics are retained; unsupported JSON primitives retain
the legacy error behavior.

The E-only probe checked 20 datatype cases and a nested validation tree with
identical canonical JSON SHA
`bf91d7ec6001bd807ed8541e6724dc195b20df980cccc078c1661074a3dd5fd1`.
For this small synthetic export tree only, normalization peak allocation was
15,068,225 versus 3,277,942 bytes and measured normalization time was 0.359
versus 0.055 seconds. These are not whole-episode resource guarantees.
The full probe is `source04_export/EQUIVALENCE_AND_ALLOCATION.json` under the
E evidence root.

Actual R and C integration tests each completed one original 15-minute
interval with original loads, capacities and service constraints. They check
the new paid-input field against the original event, distinguish the 60 kWh
physical initial stock, and independently calculate initial grid purchase,
fee and CO2 increments from original EC/pump/tariff/factor inputs. Actual
recharging remains included only once in the original segment cost. The
first fixture placed a fault exactly at its observation end and was rejected
by the existing schema; it was corrected to an explicit normal identity
availability case, without changing a physical model or tolerance.

Source04's bounded affected set passed 11 tests in 14.76 seconds, exit 0,
including datatype/one-traversal tests, the two actual R/C exports and the
existing I/O/sidecar checks. `git diff --check` returned exit 0. Logs are
`source04_export_RED.log`, `source04_fixture_RED.log`,
`source04_initial_paid_RED.log` and `source04_export/GREEN01.log` under the
E root. Source and test writers then stopped. Root owns the final same-source
matrix and centralized regression; source03 technical results are preserved
under their original bindings and do not stand in for source04 runs.

## Source05: optional recovery lock presentation and runtime all-row evidence

Source04 winter37 reached actual minute470 before a true native solver
validation failure. The original current source hour1194, original building
auxiliary row, arrived ports, runtime state and P0-P3 loads were reproduced in
E without launching an episode. Both boiler domains' cost layers returned
native OPTIMAL with physical residual1.1368683772161603e-13, but their original
recovery lock was violated by9.565482024440257e-10 kW. This is not evidence of
physical infeasibility. The later resource guard was a separate export
failure; it must not replace the original solver failure identity.

The true current recovery request was1.0095654820244402e-7 kW. Its original
maximum-recovery lock has width1e-7 kW, leaving the lawful lower bound
q>=9.565482024440257e-10 kW at the cost layer. Native feasibility1e-9 can
accept q=0, whereas original-unit posterior lock acceptance1e-10 correctly
rejects it. Source05 only multiplies this recovery lock row and both bounds
by16 in solver coordinates, the smallest binary power at least the ratio
1e-9/1e-10. Its original coefficients, bounds, objective, lock width, current
request and posterior checks remain unchanged. No recovery deadband,
clipping, inventory removal, extra solve or changed integration boundary is
introduced. The existing solve_layer implementation is isolated with only
its row-scaling callback replaced; original_problem and the collector retain
the unchanged original and actual solver matrices. This presentation is
registered for this unit-coefficient optional recovery lock only, not a
general guarantee of numerical stability.

The physical_balance hook selects the helper only for M23 with actual
positive recovery-port capacity. With zero recovery request, all12 native
layers' original/solver matrices, bounds, objectives, integrality, row_scales,
raw native vectors and actual actions were bitwise identical to the prior
route. At the failing winter input, all14 original-layer arrays were
identical, and only the two cost-layer solver rows39 changed presentation.
Selected actual recovery9.565482024440257e-10 kW respects the unchanged raw
lock. The exact current-port regression fixture records the original source
row and ports under tests/fixtures/v23_winter470_current_port.json.

The independent resource diagnosis of the source04 normal-winter C RAW found
382,770,261 of436,883,561 uncompressed bytes in1068 repeated inline validation
row tables, all in rejected candidates or unaccepted native attempts. The
default successful-row-only externalizer correctly retained those failures,
but copying them through command/decision/event evidence dominated memory.
Source05 adds runtime enable_all_validation_rows=False to the M23 environment
and DomainEvidenceStore. The old default keeps rejected/failed row tables
inline. Explicit True requires a real solver_evidence_directory; the M23
worker opts in, and the receipt records this mode.

In this mode only validation.rows and existing *_validation.rows aliases are
saved in the independent SHA content-addressed gzip store. Failure/rejection
status, accepted flags, raw x, phase, residual summaries and all other
metadata stay in place. Previous-incumbent and refinement validation aliases
are included; unrelated user data named rows stays inline. Accepted solver
calls retain the old ordered sidecar traversal and record sequence; other
row tables use CAS without inserting records into that ordered stream.
restore_domain_evidence recognizes both the old success schema and the new
hospital_ies.m23.validation_rows.reference.v1 schema and verifies SHA and row
count. Consumers restore ordered references and CAS references with their
separate schema-aware readers. Domain logging I/O remains outside solver
candidate catches or in exception handling bodies, so an I/O failure aborts
the run instead of rejecting a candidate and selecting another action.

Actual verification and retained E evidence:

- recovery_numerics_fix/RED03.log:3 failed,10 passed before the presentation;
  GREEN01.log:13 passed in2.23s. Independent review reran13 passed in2.10s.
- all_rows_fix/RED02.log:8 failed before the optional logger;
  GREEN01.log:8 passed in0.15s with actual winter native-failure and normal-C
  rejected-layer evidence, exact restoration, dedup, integrity rejection and
  fail-closed writes. The first E extraction lacked the already-local ijson
  path; that failure log remains retained. The corrected extraction uses the
  existing E dependency and never loads the complete RAW.
- all_rows_fix/SOURCE05_FINAL.log:56 passed in17.08s, exit0. This includes13
  numerical regressions,12 all-row/default/alias/I/O tests, prior domain and
  ordered-reader tests, genuine no-sink negatives and paid-stock exports.
  git diff --check returned0. No full suite or long matrix was launched by
  this core task.
- all_rows_fix/ACTUAL_LOGGING_EQUIVALENCE.json:two actual15-minute C episodes
  with the runtime logger off/on have identical action, inventory, service
  ledger, availability and command-data SHA
  942274762ad94c6c280c926a9d35aa945c93a767fe44f06fdffacd0b92d215db.
  Both ordered sidecar SHA values match; all ordered/CAS references restore.
- all_rows_fix/FULL_ACTUAL_ROWS_ROUNDTRIP.json:all1068 actual normal-winter
  tables,1,284,664 rows, were streamed through the new CAS and restored with
  identical canonical values.293 unique gzip blobs total4,876,577 bytes.
  The source RAW SHA94083abbd24494998719c35c7a1cf158ebb306b594cecfd48a4c22938e609b25
  remained unchanged. The initial proof compared unsorted JSON key order;
  its assertion/log is retained, and the corrected proof compares canonical
  sorted JSON while separately checking object equality. This was a pure
  evidence transformation, not a physical replay or resource guarantee.

Core/source/test freeze followed the final independent read-only review.
Root owns the affected31/32 output reruns and unified source05 thermal cases.
The35 successful source04 base trajectories retain their original bindings;
zero-request matrix/action identity supports only the explicitly restricted
unaffected-physics statement, not pretending that those trajectories were
executed from new source bytes. Public reset/step and the command queue are
unchanged. Additional source05 files are numerics.py, the two regression test
modules and the small current-port fixture; shared code changes are the
two-line optional physical_balance hook and M23 logging interfaces.
