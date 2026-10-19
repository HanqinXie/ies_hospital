# Same-background short/medium/long energy trade-off

Scope: **development**. These are actual saved M19 outcomes; development observations are not final or independent confirmation. Each row uses its complete registered window, not legacy full600. Source-day indices [40, 100] are separate disclosed backgrounds; no cross-duration/background totals or population superiority claim.

|day|fault min|policy|P0 ENS [e,h,c] kWh|P1 ENS [e,h,c] kWh|P2 served fractions [e,h,c]|P3 served fractions [e,h,c]|DG shaft kWh|energy fee USD|grid+gas CO2 kg|conditional total CO2 kg|saved P0 failure|
|---|---:|---|---|---|---|---|---:|---:|---:|---:|---|
|40|120.0|C18_M19|[10.925, 0.0, 7.653]|[12.01, 0.0, 5.411]|[0.986, unknown, 0.982]|[0.983, 1.0, 0.996]|780.787|1013.859|4323.717|4868.486|1|
|40|120.0|C19|[10.925, 0.0, 7.653]|[12.01, 0.0, 5.411]|[0.952, unknown, 0.982]|[0.91, 1.0, 0.978]|267.321|865.858|4356.591|4543.106|1|
|40|360.0|C18_M19|[10.925, 0.0, 7.653]|[12.01, 0.0, 5.411]|[0.986, unknown, 0.982]|[0.975, 1.0, 0.996]|2118.846|1314.244|3788.5|5266.857|1|
|40|360.0|C19|[10.925, 0.0, 7.653]|[12.01, 0.0, 5.411]|[0.881, unknown, 0.982]|[0.765, 1.0, 0.951]|747.668|912.806|3828.987|4350.649|1|
|40|780.0|C18_M19|[704.587, 0.0, 301.309]|[545.486, 0.0, 189.413]|[0.503, unknown, 0.554]|[0.515, 1.0, 0.48]|2400.0|997.747|2340.946|4015.47|1|
|40|780.0|C19|[42.797, 0.0, 15.458]|[37.64, 0.0, 15.066]|[0.472, unknown, 0.899]|[0.288, 1.0, 0.435]|2400.0|985.255|2508.796|4183.32|1|
|100|120.0|C18_M19|[20.757, 0.0, 9.638]|[22.818, 0.0, 7.022]|[0.979, unknown, 0.981]|[0.969, 1.0, 0.995]|1444.161|1304.997|4916.022|5923.64|1|
|100|120.0|C19|[20.757, 0.0, 9.638]|[22.818, 0.0, 7.022]|[0.916, unknown, 0.981]|[0.867, 1.0, 0.966]|456.188|1006.101|5009.576|5327.867|1|
|100|360.0|C18_M19|[166.93, 0.0, 49.724]|[145.469, 0.0, 38.316]|[0.856, unknown, 0.895]|[0.817, 1.0, 0.972]|2400.0|1442.733|4185.651|5860.174|1|
|100|360.0|C19|[20.756, 0.0, 9.638]|[22.818, 0.0, 7.022]|[0.744, unknown, 0.981]|[0.629, 1.0, 0.915]|1417.253|1127.536|4135.719|5124.562|1|
|100|780.0|C18_M19|[1161.1, 0.0, 466.115]|[896.185, 0.0, 309.767]|[0.374, unknown, 0.414]|[0.383, 1.0, 0.426]|2400.0|1009.899|2264.046|3938.57|1|
|100|780.0|C19|[473.037, 0.0, 217.87]|[370.9, 0.0, 150.264]|[0.328, unknown, 0.686]|[0.206, 1.0, 0.37]|2400.0|990.587|2319.248|3993.772|1|

First clean time, saved 30-minute confirmation, first subsequent saved material-loss start and full saved loss runs appear by carrier in CRITICAL_SERVICE/CRITICAL_COMPARISON. These projections preserve the inherited material thresholds and recurrence count; they do not redefine recovery. P2/P3 demand and delivered kWh come from bound ENGINEERING_INTERVALS arrays, with complete coverage and CSV-versus-RESULT ENS checks. A zero-demand carrier has an undefined served fraction; its zero demand and delivery remain explicit in the CSV. Zero actual source output alone does not classify load delivery.

DG shaft/electrical/loss/rotor ledgers and original/shaft-equivalent fees remain separate. Grid plus gas CO2 is a factor-based operational subtotal. DG and total CO2 are conditional constant-specific-fuel scenarios, HHV, with exact startup/idle/low-load chemical emissions unknown. The main factor case is CAMX2023r2_DG75pct; all four registered cases remain in EMISSIONS/ENERGY_COMPARISONS. No emissions dispatch objective is introduced.

Saved P0-failure outcomes retained: day40/120.0min/C19; day40/120.0min/C18_M19; day40/360.0min/C19; day40/360.0min/C18_M19; day40/780.0min/C19; day40/780.0min/C18_M19; day100/120.0min/C19; day100/120.0min/C18_M19; day100/360.0min/C19; day100/360.0min/C18_M19; day100/780.0min/C19; day100/780.0min/C18_M19. A completed numerical episode and passed energy audit do not mean critical service was protected.

Duration design proof: [{"source_day_index": 40, "outage_durations_minutes": [120.0, 360.0, 780.0], "registered_cases": 3, "policy_outcomes": 6, "same_background_duration_design_valid": true, "duration_proof_status": "verified", "unchanged_physical_projection_except_common_fault_endpoint_sha256": "54c6e0774c21431b8772c96bc554d6e92263eceaa2ed06aac64622dec714324c", "source_and_reset_inputs_match_across_durations": true, "exogenous_streams_intentionally_different": true, "claim": "Predeclared duration scenario sensitivity, not a same-exogenous-stream policy comparison or independent population sample"}, {"source_day_index": 100, "outage_durations_minutes": [120.0, 360.0, 780.0], "registered_cases": 3, "policy_outcomes": 6, "same_background_duration_design_valid": true, "duration_proof_status": "verified", "unchanged_physical_projection_except_common_fault_endpoint_sha256": "a23f0255fda54f77bb1acc8724401ab7a009b6cac2b691b379e0fa38275f976a", "source_and_reset_inputs_match_across_durations": true, "exogenous_streams_intentionally_different": true, "claim": "Predeclared duration scenario sensitivity, not a same-exogenous-stream policy comparison or independent population sample"}]
