# Final registered-attempt census

Status: passed

Actual metadata census: 506 registered; 466 started; 466 terminal; 465 completed with 465 complete registered-window audits; 1 failed native optimization; 40 not started.

| Source epoch | Registered | Started | Complete full audit | Failed native | Not started |
|---|---:|---:|---:|---:|---:|
| `571facd5312050e4b29d7d2acee5a9a08cf43284fab1bb323be58d15c1ba5d20` | 8 | 4 | 4 | 0 | 4 |
| `9db8880c5042316805172b3b98d857cae151716ad8689d06d1030328d12d8a4d` | 44 | 8 | 7 | 1 | 36 |
| `f88ebed9d52dfe7d5c62d1ea994a70245063e365fb52ba80d478fc3104202823` | 454 | 454 | 454 | 0 | 0 |

Current frozen source cohorts:

- development: 24 batches / 48 pairs; completed and complete registered-window audit passed 48.
- final_nonconfirmation: 116 batches / 310 pairs; completed and complete registered-window audit passed 310.
- confirmation: 32 batches / 96 pairs; completed and complete registered-window audit passed 96.

The retained old native failure is `dev_long_r1_000` / C19, observed to 255/1065 min at `arrived_current_P3` rank 20. Its saved-prefix audit passed while `complete_registered_interval=false`; that prefix is not a successful full trajectory.

JSON SHA256: `f9d2433d2cf6cdd6a58659eeae27f8a34c0f27d6db8dd4c2bef670130d0c65e6`.

All plan/manifest/terminal/producer-result SHA bindings and the current frozen source bytes were checked. No raw, annual input, CSV, build, stage or physical replay was opened/executed. Cumulative campaign snapshots in individual terminal files were not summed.
