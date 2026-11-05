# R2 runner scaffolding and verification

New code root: `D:/hospital_ies_recovery_steam_20261104`, based on `4d255c92c4a01fc0bf22956d6b343766ff02dbf3`. New output root: `E:/hospital_ies_recovery_steam_20261104_run01`. No R2 registration, freeze or physical trajectory has been created at this scaffolding handoff.

## Changes and retained boundaries

- New `tools/recovery_steam_20261104/run_worker.py` reuses the unchanged canonical cooling worker `build_runtime` and `run`. The new R2 policy is attached before native reset; its API attaches unchanged R1 behavior internally. No controller-history optimization is added.
- New `tools/recovery_steam_20261104/run_campaign.py` admits exactly one responsive `T05_R2` trajectory, 180 one-minute steps continuously from zero. T08 aliases T05. T06's actual completed R1 producer is pinned and retained; no T06 trajectory is authorized or launched.
- New `tests/test_recovery_steam_runner_20261104.py` provides one regression around the actual exclusive native RESULT writer with a stubbed simulation. The native RESULT bytes remain identical; only a separate R2 `WRAPPER_COMPLETION.json` is created.

Every existing R1 module remains byte-identical. A fresh read-only comparison verified all 1,080 original R1 source files and all 36 runtime input files unchanged. New policy/runner/test files are additions only. The source freeze must occur after all new files are final.

The native worker exclusively owns `RESULT.json`. R2 metadata is written only to `WRAPPER_COMPLETION.json`; no existing result is reopened for writing. Sidecar schema is `hospital_ies.recovery_steam.wrapper_completion.v1`, and it pins the native RESULT, source freeze and registration, records the R2 config and actual native return code, and states that actual process exit code is recorded by the supervisor. It does not manufacture an original process exit status. The campaign records the real child exit code in `ATTEMPTS.jsonl` and the selection. Failed attempts, native errors, stdout/stderr and command records remain intact.

## Registration dependencies

Prepare requires `--r1-audit` pointing to the completed passed independent R1 comparison JSON. It verifies that audit's `T05_R1` RESULT pin matches the selected completed actual R1 T05 producer. Selection and the original R1 administrative sidecar must match that native result. R2 then pins the full R1 T05 CASE/result/resolved/initial/decision/state/segment/event evidence and the passed audit, separately from original R0 evidence.

The R2 case is an exact byte copy of MAIN FAMILY_SELECTION's original `T05_R/CASE.json`, SHA-256 `41900739cb68ad36e85a901011f8d85920943015c30bfbada51dc6b8f66003c5`. Its original policy metadata remains original; only the new registration carries `R2_CONFIG`. Existing bound minute inputs and old actual trajectories are referenced and hashed without copying raw/minute files. Original physical configuration, native execution/queue delays, reset/step interface and source/input data remain unchanged.

## Actual checks and logs

Working directory: `D:/hospital_ies_recovery_steam_20261104`. Raw logs: `E:/hospital_ies_recovery_steam_20261104_run01/control`.

| Actual command | Exit | Evidence |
|---|---:|---|
| `python -m pytest --noconftest -q tests/test_recovery_steam_runner_20261104.py --basetemp E:/hospital_ies_recovery_steam_20261104_run01/control/runner_red_fixture` | 1 | `runner_red.log`: expected missing-worker assertion failure before implementation |
| `python -m pytest --noconftest -q tests/test_recovery_steam_runner_20261104.py --basetemp E:/hospital_ies_recovery_steam_20261104_run01/control/runner_green_fixture` | 0 | `runner_green.log`: 1 passed in 0.28 s; actual exclusive native writer, native simulation stubbed |
| `python -m py_compile tools/recovery_steam_20261104/run_worker.py tools/recovery_steam_20261104/run_campaign.py tests/test_recovery_steam_runner_20261104.py` | 0 | `runner_compile.log` |
| `python -m tools.recovery_steam_20261104.run_campaign --help` | 0 | `campaign_help.log` |
| `python -m tools.recovery_steam_20261104.run_worker --help` | 0 | `worker_help.log` |

`unchanged_r1_core()` was executed read-only, exit 0: 1,080 source files and 36 runtime inputs equal. No pre-existing tests, assertions, tolerances, physical modules or original R1 artifacts were changed. The unchanged historical worktree route hook is not used for this one new focused test (`--noconftest`); no full-suite pass is claimed.

Available next commands after the R1 passed audit exists and code review is complete; these are not executed evidence:

```powershell
python -m tools.recovery_steam_20261104.run_campaign prepare --root E:/hospital_ies_recovery_steam_20261104_run01 --r1-audit <actual-passed-R1-COMPARISON_AUDIT.json>
python -m tools.recovery_steam_20261104.run_campaign freeze --root E:/hospital_ies_recovery_steam_20261104_run01
python -m tools.recovery_steam_20261104.run_campaign run --root E:/hospital_ies_recovery_steam_20261104_run01
```

The one-worker RAM/space guard requires 10 GiB available RAM and 30 GiB free E space before admission, with native boundary stop at 6 GiB available RAM or 30 GiB free disk. TEMP/TMP and raw outputs remain isolated on E. STOP requests are retained for supervised review, and no automatic rerun or best-of selection is performed.
