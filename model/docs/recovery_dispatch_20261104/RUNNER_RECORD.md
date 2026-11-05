# R1 runner preparation record

This record covers runner scaffolding and read-only identity checks only. No physical trajectory was started, no source freeze was created, and the new `runs` directory is empty at this handoff.

## Files and runtime interface

- `tools/recovery_dispatch_20261104/run_campaign.py`: explicit `prepare`, `freeze`, and `run` commands; two producers only; fresh attempt directories; maximum two processes and one solver/BLAS thread per process; E-space/RAM admission and boundary-stop guards; per-worker TEMP/TMP; owned-process receipts; retained failed attempts.
- `tools/recovery_dispatch_20261104/run_worker.py`: invokes the original `tools.cooling_dynamics_20261104.run_worker.run` function. The original worker remains byte-identical. Its `SOURCE` resolves to the new recovery checkout. Only its `build_runtime` and binding-check callables are temporarily wrapped within this new worker process.
- `tests/test_recovery_dispatch_runner_20261104.py`: two small checks for exact case copying and the real recovery hook attached around a stubbed original factory, preserving original policy/model identities. It does not create/reset/step a physical environment.

The wrapper calls the original `build_runtime(case, source_freeze, cooling_config)`, then calls `attach_recovery_policy(env, controller, config=registered_R1_config)` before the original worker calls `env.reset`. `RESOLVED_CASE.json.policy` remains the original native policy. A separate `recovery_dispatch_intervention` records the named config and attachment. The original raw optimizer diagnostic remains original; active selection receipts are at `command.joint_dispatch.diagnostic.recovery_dispatch_selection`. `RESULT.json` retains the original execution identifier in `base_execution_model_id` and identifies the new intervention separately.

## Prepared identity

Data root: `E:/hospital_ies_recovery_dispatch_20261104_run01`.

Registration SHA-256: `4dc71790f001be9c99beeed7aed4ea9dea715772253f80dd37ffb26cad64ce04`.

Baseline binding SHA-256: `99ec1dd4dbf49b1a0cbc3418d104ced21a4c5adf77ad393675be9102ec6e922a`.

| New producer | Original R0 selection | Variant | Minutes | Exact copied original CASE SHA-256 |
|---|---|---|---:|---|
| T05_R1 | MAIN FAMILY_SELECTION T05_R | responsive | 180 | 41900739cb68ad36e85a901011f8d85920943015c30bfbada51dc6b8f66003c5 |
| T06_R1 | MAIN FAMILY_SELECTION T06_R | responsive | 180 | 683de25f5199926cac12c9d6f146a77030c86a4a6288fc83080aea6d9ec3fe3a |

Both byte comparisons passed. The case's original policy metadata is unchanged. The separate registration carries `recovery_converter_continuity_R1` and full `R1_CONFIG`. T08 aliases T05; it is not a third simulation.

`inputs/DEPENDENCY_PINS.json` pins 26 existing external dependencies totaling 1,091,923,910 referenced bytes, including the existing E minute input files, accepted R0 selection/source identities, actual R0 case/initial/resolved/state/decision/segment/event files. No minute input or historical raw output was copied. The deleted `E:/hospital_ies_minute1_20261103_run01` is not accessed. Only two small original case files and new manifests/registration were written.

Fresh static checks found all 1,075 original source files and all 36 original runtime input files equal between `D:/hospital_ies_noopt_boilerfix_20261104` and the new checkout. Base commit is `5d6c21d28f04890d0c1d673774775bf5cd686a43`. Freeze checks ancestry and all original source/input bytes, then pins all actual completed source files, runtime inputs, external dependencies, new cases, registration, resource policy and baseline manifests. Exact source bytes are authoritative, including uncommitted new files. Prepare's `BASE_CORE_UNCHANGED.json` is an earlier preparation receipt; the source freeze records the complete final source/input identity.

## Executed commands and outcomes

Working directory for every command below: `D:/hospital_ies_recovery_dispatch_20261104`. Raw logs are under `E:/hospital_ies_recovery_dispatch_20261104_run01/diagnosis/runner_checks`.

| Command | Exit | Raw log / result |
|---|---:|---|
| `python -m pytest -q tests/test_recovery_dispatch_runner_20261104.py` | 4 | `runner_red.log`: pre-existing root conftest rejects the new worktree path before collection |
| `python -m pytest --noconftest -q tests/test_recovery_dispatch_runner_20261104.py` (before implementation) | 1 | `runner_red_focused.log`: expected three missing-runner assertion failures |
| `python -m pytest --noconftest -q tests/test_recovery_dispatch_runner_20261104.py --basetemp E:/hospital_ies_recovery_dispatch_20261104_run01/diagnosis/runner_checks/pytest_green` | 1 | `runner_green.log`: exact case-copy test passed; new wiring test incorrectly expected `.base` instead of the actual `.base_controller` API |
| `python -m pytest --noconftest -q tests/test_recovery_dispatch_runner_20261104.py --basetemp E:/hospital_ies_recovery_dispatch_20261104_run01/diagnosis/runner_checks/pytest_green02` | 0 | `runner_green02.log`: 2 passed in 0.08 s |
| `python -m tools.recovery_dispatch_20261104.run_campaign prepare --root E:/hospital_ies_recovery_dispatch_20261104_run01` | 0 | `prepare.log`; prepared only, zero formal experiments; `logs/COMMANDS.jsonl` |
| `python -m py_compile tools/recovery_dispatch_20261104/run_campaign.py tools/recovery_dispatch_20261104/run_worker.py tests/test_recovery_dispatch_runner_20261104.py` | 0 | `py_compile.log`, later `py_compile_final.log`; repeated after final source/input guard addition, exit 0 |
| `python -m tools.recovery_dispatch_20261104.run_campaign --help` | 0 | `campaign_help.log` |
| `python -m tools.recovery_dispatch_20261104.run_worker --help` | 0 | `worker_help.log` |
| `python -m pytest --noconftest -q tests/test_recovery_dispatch_runner_20261104.py --basetemp E:/hospital_ies_recovery_dispatch_20261104_run01/diagnosis/runner_checks/pytest_final` | 0 | `runner_final.log`: 2 passed in 0.08 s |
| `python -m pytest --noconftest -q tests/test_recovery_dispatch_runner_20261104.py --basetemp E:/hospital_ies_recovery_dispatch_20261104_run01/diagnosis/runner_checks/pytest_handoff` | 0 | `runner_handoff.log`: 2 passed in 0.08 s after final output-root/retry guards |
| `python -m py_compile tools/recovery_dispatch_20261104/run_campaign.py tools/recovery_dispatch_20261104/run_worker.py tests/test_recovery_dispatch_runner_20261104.py` | 0 | `py_compile_handoff.log`: final handoff compile |

The initial broad registration/root scaffolding checks were narrowed to the two focused tests requested during review; no pre-existing test or assertion was edited. The `.base` wiring-test assumption was corrected against the actual policy API; no production behavior changed to satisfy that assertion. `--noconftest` is used only for these new focused tests because the unchanged historical route hook binds another explicit worktree. This is not a full-suite pass or a replacement of the historical gate.

Read-only Python identity checks were also executed (exit 0): original source/input dictionaries compared equal; copied case bytes compared equal; `runs_created=[]`; `source_freeze_exists=False`.

## Commands available after root review

These are available commands, not executed trajectory evidence:

```powershell
python -m tools.recovery_dispatch_20261104.run_campaign freeze --root E:/hospital_ies_recovery_dispatch_20261104_run01
python -m tools.recovery_dispatch_20261104.run_campaign run --root E:/hospital_ies_recovery_dispatch_20261104_run01 --max-workers 2
```

Source freeze is immutable. Complete all policy/test/audit source edits before freezing. Any later `.py`/config/input drift fails native worker binding checks. External pinned R0/source/input files must remain accessible and unchanged. No checkpoint reset at repair, annual/35-day run, additional case, extra model assumption or parameter change is introduced here. Actual trajectory completion, engineering qualification and pre-recovery equivalence remain unexecuted at this handoff.
