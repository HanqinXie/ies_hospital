# Single-unit runner and native-result ownership

Source root: `D:/hospital_ies_single_unit_20261104` (base `911d758`). Raw root:
`E:/hospital_ies_single_unit_20261104_run01`. The final delivery code remains
dependent on this frozen source root and the explicitly pinned original minute
inputs. Copying the two runner files alone does not make a standalone simulator.

The separate `prepare`, `freeze` and `run` commands prevent registration and byte
freezing from starting trajectories. The runner creates exactly six new producers:
`TOWER_U`, `TOWER_R`, `TOWER_U0`, `PUMP_U`, `PUMP_R`, `PUMP_U0`. Native variants are
`unattended`/`responsive`; `study_variant=U0` is the healthy no-dispatch diagnostic.
Both U and U0 use the unchanged native post60 upper/lower optimization suppression.
All episodes are continuous minute0..180 with native one-minute stepping.

The original `FAMILY_SELECTION.json` is authenticated together with its accepted
`FAMILY_BINDING.json`. `NORMAL_REUSE_PROOF.json` checks all 1075 original native
source files against the accepted fixed source, original runtime inputs, original
normal CASE/RESULT and every file pinned by that RESULT. Original N initial and
resolved physical/configuration states must equal those of both original U/R
bases, including every minute-input digest. The accepted original source-family
call closure supports the sole old/native forward.py difference for N. Reused
`TOWER_N`/`PUMP_N` retain their original producer paths and source identities; the
new source SHA is never assigned to old output. A failed proof is a blocker;
this runner does not silently manufacture or relabel a new normal result.

`run_worker.py` imports the registered runtime factory before temporarily binding
it to the unchanged native worker's factory name. `native.run` retains its entire
reset/step/control/queue/dynamics/logging/audit/failure lifecycle. Its exclusive
`RESULT.json` writer remains the only owner of that file. `COMPLETION.json` is a
separate exclusive sidecar with the exact native result SHA and original source
reference. A sidecar cannot convert a failed native result into a completed one.
The binding is restored even when native execution raises. No `_solve`, local
interval action or physical commit is replaced.

Admission starts with at most four workers and permits at most six. Growth needs
at least 12 GiB available and all initial active workers to have reached minute5.
Every launch needs 10 GiB available; 6 GiB is the owned boundary-stop threshold.
Native disk guard remains 30 GiB free. Solver and BLAS threads are one per worker,
with output/temp on E. Every attempt has independent raw output, stdout/stderr,
process identity and completion/failure receipts. Resource/user/peer-failure
stops use owned stop files; unrelated applications are untouched. Prepare accepts
pre-existing `component_checks` and `control` only, never previous cases or runs.

Commands (run only after the root agent's explicit start decision):

```powershell
python -B -m tools.single_unit_20261104.run_campaign prepare --root E:/hospital_ies_single_unit_20261104_run01
python -B -m tools.single_unit_20261104.run_campaign freeze --root E:/hospital_ies_single_unit_20261104_run01
python -B -m tools.single_unit_20261104.run_campaign run --root E:/hospital_ies_single_unit_20261104_run01 --max-workers 6
```

Focused regression evidence, all actual executions with `--noconftest`:

| Log | Command/result |
|---|---|
| `runner_RED.log` | `python -B -m pytest -q --noconftest tests/test_single_unit_runner_20261104.py`; exit1, two expected failures for the absent new module |
| `runner_GREEN01.log` | Same selection with explicit E `--basetemp .../runner_pytest01`; exit1, one test passed and temp-parent setup failed before the writer test |
| `runner_GREEN02.log` | After creating the E component-check parent, `--basetemp .../runner_pytest02`; exit0, two passed |
| `runner_GREEN03.log` | Final runner code, `--basetemp .../runner_pytest03`; exit0, two passed |
| `runner_PREPARE02.log` | Actual prepare command above; exit0, six registered new producers, two authenticated old N aliases, zero formal experiments started |
| `runner_INTERFACE02.log` | Pre-freeze validation; exit1, correctly rejected prepared CASE after the generator's final causal metadata amendment |
| `runner_PREFREEZE_REFRESH01.log` | Preserved old prepared CASE/REGISTRATION bytes under `component_checks/prefreeze_registration01`, regenerated current CASE and rebound their SHA before freeze; exit0 |
| `runner_INTERFACE03.log` | Final six CASE validation/SHA plus exactly two original N aliases and empty runs/no source freeze; exit0 |
| `runner_PREFREEZE_REFRESH02.log` | Preserved previous prepared bytes under `component_checks/prefreeze_registration02`; regenerated six CASE after final legacy-metadata clarification; exit0 |
| `runner_INTERFACE04.log` | All six final CASE validated, exact registration generator SHA checked, two original N aliases and no freeze/formal runs; exit0 |

The writer regression invokes the actual native run failure path before reset
(no optimization or physical trajectory), then verifies the native result's
bytes remain identical after sidecar creation and duplicate-sidecar rejection.
The admission regression covers pre-minute5, below12 GiB and authorized ramp.
An initial interactive prepare attempt stopped on the strict fresh-root guard
because the root already held the separately created probe `control` directory;
that directory was then explicitly allowed along with `component_checks`.
No old tests, tolerance or old source file was changed. Full historical suite is
outside this focused run. Physical-model qualification remains that of the
registered research tower aggregate / primary-pump functional interlock.

The pre-freeze metadata amendment changes only the explicit U0 cause count from
one to zero and records the at-most-one contract. It changes no fault calendar,
initial state, controller or physics. The actual refresh script is retained at
`control/refresh_prefreeze_cases01.py`; `inputs/PREFREEZE_CASE_REFRESH01.json`
binds all previous/current CASE and registration hashes. A preceding ad-hoc
interface command omitted the repository `src` path and failed to import
`hospital_ies`; the actual runner sets this path before importing runtime code.
The second refresh, authorized by the root review, labels inherited static
fault target/availability/duration fields as legacy metadata and explicitly binds
the authoritative declared target/calendar. It changes no event or physical
parameter. Its separate script/receipt use suffix `02`; both previous prepared
versions are retained rather than overwritten as evidence.
