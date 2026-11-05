# Observed-steam recovery dispatch R2

**Goal:** address the actual remaining T05 ABS-reference cycling after preserving the full R1 negative result. Hardware, failure/repair times, loads, priorities, criteria, dynamics and queue remain unchanged. T06 keeps its independently evaluated R1 result; T08 aliases T05.

**Architecture:** add a separately named recovery-steam layer on top of unchanged R1 policy/module. Only after observed TOWER zero-to-positive return, use fresh completed actual HRSG/boiler steam and current total heat demand with original plant conversions to limit the queued ABS permission. Then reuse R1's pure warm-first EC allocation within effective capacities. Preserve all original source/boiler/contact commands. No calendar endpoint, free warmup, current ABS-output startup latch, physical constraint edit or promised gain.

## Evidence prerequisite

R1 T05 activated normally but native EC permissions were already full, so its selection was a no-op; actual recovered 120..132 demand/service/ENS and recharge were identical to R0. Full R1180minute outcomes must be independently audited and pinned before R2 registration. R1 T06 actual recovered cold ENS fell materially, and must not be rerun solely to extend T05.

## Tasks and validation

- Add new tools/recovery_steam_20261104/policy.py and __init__.py only; preserve all inherited source bytes.
- First reproduce missing behavior, then implement bounded actual-steam allocation. Focused checks cover original plant conversion, all-priority heat reservation, actual vs commanded steam, nonzero ABS start from available steam, EC real caps, pump-only R1 invariance and actual unchanged delayed-queue insertion. Retain actual RED/GREEN logs.
- One-producer runner uses byte-identical original T05 CASE, separately named R2 config, original1minute/180minute continuous run from0, unchanged native lifecycle. Native RESULT is immutable; wrapper writes a new completion sidecar. No repeated exclusive RESULT overwrite.
- Require and pin completed R1 comparison audit before prepare. Freeze source/input/case/intervention identity. Run only T05_R2 (T08 is an alias), not another T06.
- Independently audit new trajectory, original physical config and initial state, exact pre-recovery requests and actual prefix. Compare R2 to both original R0 and full R1. Report E/H/C tier demand/served/ENS, all costs/end stocks, original failure/thermal durations and original P0/P1 guard. Keep R1 negative evidence and R2 negative outcomes if any.
- Actual postrepair cold ENS reduction >=10% is the registered material-benefit reference; it does not authorize weakening physical or acceptance checks. P0/P1 material deterioration blocks promotion.
- New figures and four-tier figures remain paused. Final report includes all attempts/commands/source diffs and limitations.

No additional R3/search is authorized by this finite implementation plan. A valid negative R2 outcome is reported honestly.
