# V20 engineering readback working notes

This is an unbound working document. It is not a final campaign result or a change to the frozen source. All numbers below were read from registered compact RESULT/plan/manifest/ATTEMPTS/BATCH_RESULT records and the registered emissions factors; no raw trajectory or annual load values were opened for this readback.

## Pilot readback, 2026-10-19 04:40 UTC

The fixed source scenario is `v17_L1_05d91fa94a2b551ad7ab17ff`, batch `v20_main_000`: calendar start hour 121; GRID fault starts at minute 67.5 for 30 minutes; no demand shock, no propagation, response delay zero. The registered window is [0,600) minutes. This is a relatively short, fault-only example, not evidence for all classes or prolonged energy shortage.

The batch completed all three registered trajectories. Actual start/finish events are unique, physical statuses are completed, saved analysis is available, audits explicitly passed with no issues, finish RESULT hashes match, all three qualify for comparison, and the terminal receipt confirms identical actual reset states and exogenous streams. Current production source equals the signed M20 freeze. At this snapshot the campaign denominator remains 406: 3 complete, 403 not started, no numerically complete but disqualified records. The original final 12 backgrounds remain unopened.

| [0,600) metric | E | K1 | C19 |
|---|---:|---:|---:|
| P0 material interruption union, min | 0 | 0 | 0 |
| P1 material interruption union, min | 0 | 0 | 0 |
| P0 electric ENS, kWh | 0.0004952 | 0.0004813 | 0.0004908 |
| P3 electric ENS, kWh | 79.9659 | 53.1856 | 36.7746 |
| Actual modeled energy fee, USD | 397.4299 | 367.5687 | 378.9421 |
| GRID purchase, kWh | 2637.9489 | 2539.3928 | 3610.5143 |
| GT electricity, kWh | 2471.7695 | 2527.5873 | 1456.3280 |
| DG electricity, kWh | 21.0360 | 0 | 7.7680 |
| DG shaft-equivalent budget debit, kWh equivalent | 30.4166 | 9.3805 | 17.1483 |
| Conditional operating CO2, kg | 2931.3444 | 2707.1895 | 2295.1405 |

The conditional CO2 uses the registered `CAMX2023r2_DG75pct` factor case. Diesel chemical fuel consumption remains unknown; this is not an exact measured-emission total.

All three have zero material P0/P1 minutes in this pilot, but nonzero sub-threshold electric ENS remains. Do not state exact zero unmet energy. The three policy demand integrals agree to floating-point summation precision: P0 requests e/h/c = 345.0661 / 531.1089 / 338.3911 kWh; P1 = 227.9126 / 379.7891 / 234.1925 kWh.

C19's P3 electric ENS comprises 7.6415 kWh active disconnection plus 29.1330 kWh connected-but-unserved. E and K1 P3 electric ENS are connected-but-unserved in this example. This distinction matters: controller isolation and supply insufficiency are not interchangeable labels.

The initial DG shaft-equivalent budget was 2400 for all policies and none exhausted it. K1 has zero delivered DG electrical energy but consumes 9.3805 shaft-equivalent units; the existing budget includes mechanical/rotor/loss work. This is not a reason to substitute electrical work for the shaft debit or infer liters of diesel.

Against the separately identified V19 records for this exact source scenario and window, modeled fees change by E -9.0676, K1 -9.9492, C19 -24.6161 USD. P0/P1 material minutes remain zero in both versions. Old compact records do not identify exact requested/delivered integrals; those comparisons must remain unknown.

The same accounting correction does not guarantee a lower-emission dispatch. E conditional CO2 increases by 481.5218 kg, although its fee decreases: GRID purchase falls from 3723.9817 to 2637.9489 kWh, GT gas rises from 22.0144 to 39.7355 MMBtu, and boiler gas falls from 9.8086 to 5.4481 MMBtu. K1 and C19 conditional CO2 decrease by 95.4476 and 311.1886 kg respectively. These observed changes in the source mix distinguish dispatch consequences from the arithmetic correction itself.

Within this single case K1 is cheapest and C19 delivers the most P3 electrical service with lower conditional CO2. This is an observed tradeoff, not an overall policy ranking. Await the full four-class/long-window/fixed-background campaign before drawing general conclusions.

Evidence locations: new `experiments/v20_main_000/` registration, ATTEMPTS, three compact RESULT records and BATCH_RESULT; unchanged V19 `main_final_r1_000/`; new `references/EMISSIONS_FACTOR_REGISTRY.json`. Readback used `scripts.v20_report.collect_campaign`, `comparison_emissions` and the frozen runner's source binding check; both read-only Python commands exited 0.

## Early multi-case checkpoint, 13 started / 11 numerical completions

The denominator is still 406. At the next readback 13 pairs had started, 11 compact results were completed and audited, 2 were running and 393 had not started; 9 pairs had complete terminal batches and were eligible for matched comparison. There were no numerical completions disqualified by execution/audit evidence. The eligible subset covers only the first calendar background's L1/L2/L3 specifications; it is not a balanced full study and excludes the first L4 until that batch closes.

In `v20_main_001` (canonical L2), K1 retains 2.5 minutes of P0 and P1 material interruption, exactly as in V19; E and C19 each retain zero material minutes. The accounting correction therefore does not remove this existing K1 handoff interruption. In the first L1 and L3 specifications all three have zero material P0/P1 minutes, with any sub-threshold ENS kept separately.

All nine eligible [0,600) modeled fees decrease against their own V19 records. This early cost observation is not a general low-emission or higher-service conclusion. P3 electric ENS moves in different directions: L1 E 72.5111→79.9659, K1 51.3231→53.1856; L2 E 110.2221→100.0221, K1 151.0174→162.0645 kWh; first L3 values are essentially unchanged. P3 is the residual category, so its requested-demand denominator changes when the grouping is corrected. A P3 ENS difference alone does not identify the change in its delivered energy or service fraction; old compact requested/delivered integrals remain unknown. Preserve this distinction in the final report.

## Early resource estimate, not a completion promise

After three completed pairs per strategy, measured episode means are E 25.23, K1 66.66, C19 133.47 seconds; observed ranges are 23.50–28.28, 63.25–69.79, 127.50–138.43 seconds. Scaling these easy early cases only by registered duration gives about 10.56 process-hours, or about 5.81 wall hours with two comparison workers and serial normal windows. Difficult solver cases can take longer; this is not an SLA or real-time control claim.

The eight long specifications each last 1065 simulated minutes. The twelve single-policy normal jobs comprise six 24h, three 48h and three 72h runs. Freshly started E processes were observed around 0.2–0.3 GiB RSS and 1.6–1.7 GiB private memory; those startup samples do not bound later C19 memory. The existing V19 72h C19 private peak around 26.5 GB remains the relevant large-window caution. Keep the existing RAM guard and run normal windows globally serially.

## First four-class background closed, 19 started / 17 numerical completions

At this readback 17 pairs were completed, 2 were running, 387 were not started, and 15 had terminally bound matched results; no failed or audit-disqualified completed records appeared. The first background's L1/L2/L3/L4 sets are now all closed. The K1 2.5-minute P0/P1 interruption also persists in the first L4 specification (`v20_main_003`), unchanged from V19; E/C19 retain zero material minutes there. Thus these first four cases do not establish that the accounting correction has changed critical interruption rankings.

In this first L4, fees E/K1/C19 are 381.1355 / 405.8516 / 385.0964 USD, each below its own V19 fee. P3 electric ENS rises in all three: E 127.2532→143.3628, K1 187.9867→201.9046, C19 70.4185→99.6795 kWh. Retain the changed residual-request boundary above rather than assigning these differences directly to delivered-service fractions. Conditional CO2 changes are mixed: E 1901.5412→1974.1509 kg, K1 2742.7529→2346.2594, C19 2637.9698→2313.9085. A second-background L1 (`v20_main_004`) instead shows all three material-critical durations zero and both fees and conditional CO2 lower. These are scenario-dependent dispatch consequences, not a universal monotone effect of the arithmetic correction.

## Evidence for normal-window resource admission

The old V19 read-only `review/resources_r2/SUMMARY.json` contains all 12 normal process peaks, obtained from `psutil.memory_info().rss/private/peak_wset`; the CSV has 7–131 samples per normal process. These are separate measurements, not interchangeable fields. The often quoted 26,497,581,056 B is **sampled private/commit**, 24.68 GiB, for `normal_C19_final_r1_002` (72h). Its actual OS lifetime peak resident working set is 24,352,612,352 B, 22.68 GiB, and sampled RSS peak is 24,317,726,720 B. The lifetime RSS peak has no exact timestamp.

| Original V19 strategy/window | OS lifetime RSS peak, GiB | Sampled private peak, GiB |
|---|---:|---:|
| E 24h (maximum of two windows) | 1.16 | 2.60 |
| E 48h | 2.13 | 3.57 |
| E 72h | 3.12 | 4.49 |
| K1 24h (maximum of two windows) | 3.21 | 4.66 |
| K1 48h | 6.26 | 7.82 |
| K1 72h | 9.29 | 10.94 |
| C19 24h (maximum of two windows) | 8.11 | 9.71 |
| C19 48h | 16.09 | 17.93 |
| C19 72h | 22.68 | 24.68 |

A current V20 observation while two C19 comparison children were active showed physical total 47.38 GiB and available about 20.15 GiB, but Windows committed memory 48.89 GiB of a 57.54 GiB limit, leaving only 8.65 GiB commit headroom. Thus physical availability alone is insufficient admission evidence. Those two children had about 10.29 GiB private memory combined; releasing them could increase commit headroom, but the amount must be measured again after comparisons finish because other applications can change.

An evidence-based external serial sequence can distinguish the existing registered cases: run low-memory E windows and K1 24→48→72 only when both physical availability and commit headroom cover the relevant observed private peak plus the retained host reserve; measure new process private/RSS peaks. C19 24h needs its own admission (old private about 9.71 GiB), followed by separate 48h/72h decisions (17.93/24.68 GiB before reserve). If adding a provisional 20% task-growth margin and a 6 GiB host reserve, illustrative thresholds become E max 11.4 GiB, K1 about 11.6/15.4/19.2 GiB and C19 about 17.7/27.6/35.7 GiB. These are explicit engineering planning estimates, not validated new upper bounds or changes to the frozen model. Reassess actual V20 measurements; do not infer the 72h C19 requirement from a smaller E/K1 case or silently lower its admission rule.

This readback did not close applications, alter pagefiles, modify global TEMP, change the production source, or start another simulation.
