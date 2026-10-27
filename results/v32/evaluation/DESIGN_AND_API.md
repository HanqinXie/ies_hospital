# V32 six-record read-only audit

CLI: `python -B evaluation/audit_v32.py --root ROOT --manifest EXPERIMENT_REGISTRATION.json --support audit_support --project PROJECT --output FRESH`.

The manifest fixes six records: b01 C/M/R32 each normal/disturbed. Rows contain id, context, mode, method, evidence_role, path, freeze_file and case_id. R32 rows may provide `reference_files` mapping spec/plan/actions/initial/selection to portable relative paths. CASE and RESULT retain their original absolute bindings; mapped files must match their original SHA. The top-level `base_v31_freeze` identifies the unchanged 864-file baseline. Source trees may add files but not change/remove existing ones or alter the original 36 input bindings.

The byte-preserved V30 audit/price receipt are copied in `v30_support/`; the byte-preserved V31 auditor is copied as `v31_window_support.py` for previously tested read-only window, coverage, timing and signed-delta functions. `--support` selects the parent's identical packaged support. No environment or simulation is imported. The frozen new planner is loaded from explicit `--project` only to call its named-flow `audit_plan`, never `solve_reference`.

Outputs: AUDIT.json, MAIN_RESULT_TABLE.csv, PAIRED_DELTAS.json, PLAN_VS_ACTUAL.json, COMMAND_CHAIN.json, COMPLETION_MATRIX.json, ENGINEERING_REPORT_ZH.md, OUTPUT_SHA256.json. Missing evidence is partial/not_started, never zero-filled. Final completion requires six complete native runs, three paired result deltas, true terminal/resource/source checks, two verified reference plans/bindings/initial states/request chains and no audit/solver/fallback error.

The C/M normal-disturbed actual prefault prefix remains a check. R32 explicitly has `offline_known_window` and has no same-prefix requirement. Its normal-disturbed increment includes preparation differences chosen using future exogenous knowledge. Common t=0 physical state/core, hardware, input, scenario and end are still checked across all methods.

Plan versus actual reports each priority/carrier separately, energy/fees/stocks and GT on/off histories, without comparing unlike fee components as solver errors. Initial inventory preparation is counted once and separated from actual window operation. DG mechanical work is read from its actual receipt even if electrical output is zero. No chemical-fuel or maximum-endurance estimate is introduced.

The four-layer chain retains original plan, transformed early preparation requests, genuinely arrived commands and actual native outputs. Requested action is compared with the frozen schedule; common M23 preparation can enlarge queued boiler/EC ceilings and is reported separately. Plan unmet and continuous storage ports are explicitly not executed. The independent raw demand/spec comparison occurs only after execution and never feeds historical C/M results into input generation.

Execution order: meaningful synthetic RED; implement fixed selection, independent reference/binding/demand checks and reporting; GREEN; partial read-only checks if useful; freeze tools before the parent's two completed-run notification and final six-record audit. No model, solver or broad tests are run here.
