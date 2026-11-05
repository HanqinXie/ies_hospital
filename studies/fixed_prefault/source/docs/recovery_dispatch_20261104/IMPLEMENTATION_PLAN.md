# Recovery-only dispatch implementation plan

**Goal:** improve actual delivery after observed repair in the existing T05/T06 trajectories; T08 remains the exact T05 recovery alias.

**Architecture:** retain the entire frozen native physical/control execution implementation. Add a separately named, current-observation recovery command-selection layer at the existing `_prepare_command_dispatch` hook. It edits ordinary queued converter permissions only after the policy has observed failed shared support return. It does not add an upper action, new physical path, local optimizer, future controller or repair capability.

**Tech stack:** existing Python simulation and native solver; focused pytest; original engineering-audit helper; existing matplotlib export style only if authorized later.

## Scope and evidence

- Base commit: 5d6c21d28f04890d0c1d673774775bf5cd686a43; frozen original code D:/hospital_ies_noopt_boilerfix_20261104 stays read-only.
- New isolated code: D:/hospital_ies_recovery_dispatch_20261104. New raw/report root: E:/hospital_ies_recovery_dispatch_20261104_run01.
- Existing T05_R and T06_R selected via original FAMILY_SELECTION; existing N/U not changed. Cases, load bytes, initial state, physical parameters, faults, repair clocks, criteria, priority hierarchy, queue/delays, dynamic constants and reset/step API remain unchanged.
- Exact recovery observations and commands: diagnosis/RECOVERY_DISPATCH_DIAGNOSIS.md and RECOVERY_TRACE_FACTS.json.
- T05 current commands already arrive at repair with no queue lag. T06 later exchanges EC1/EC2 permission despite unequal warm states, causing large cold-service gaps. Independent early dynamic and electrical constraints remain.

## Registered candidate R1: recovery converter continuity and actual-service coverage

1. Controller wrapper delegates the original controller on every step and validates current snapshot clocks. Latch observed shared-support zero availability, and activate only when the same support is subsequently observed positive. No clock120/calendar/repair-end field is read. Policy memory resets per episode.
2. Before activation, the original request and command hook output are byte-for-byte semantically unchanged.
3. Active candidate uses current tier demand, current observed ABS delivered/capability and EC warm states to generate an EC permission envelope covering the current cold demand not reliably covered by ABS. It retains the original planned total EC permission if greater. Do not sum independent cold-storage contribution as guaranteed future supply. Original physical capacities/current availability cap every machine.
4. Allocate EC permission warm-first by observed running cooling, stable unit index for ties. An identical, fully healthy EC1/EC2 pair must not exchange identities merely from an equivalent native allocation. This is an explicit internal command-selection policy, not a new actuator or a physical proof claim. Actual dispatch is still bounded/optimized through unchanged source/tower/pump/electric/storage constraints and dynamic limits.
5. Source GT/grid/DG/boiler, ABS permissions and contact/cut decisions remain exactly the original native planner outputs for this candidate. No GT preference addition in R1; investigate separately only if residual data warrant it.
6. Keep original native solver diagnostic as original; attach explicit before/after command-selection receipt and do not present transformed command as the original raw optimizer solution.

## Implementation tasks

- [ ] Create tools/recovery_dispatch_20261104/policy.py for causal monitor, pure permission-selection arithmetic, wrapper and core command hook. No existing physical source file modified.
- [ ] Add focused failing tests before implementation: inactive/exact delegation; recovery trigger shifted in time and partial recovery; future clocks rejected; warm identity and real device caps; source/ABS/boiler/cut invariance; original queue call retained and reset isolation. Run actual RED then GREEN; no unrelated full suite.
- [ ] Create separate runner/registration using the unchanged existing worker with explicit build-runtime wrapper. Freeze all actual source/input pins and intervention config. Reuse original two response producers as R0, transparently bound, not relabeled as same source SHA.
- [ ] Run T05_R1 and T06_R1 continuously from0, in parallel if RAM allows, solver1 thread each. No checkpoint reset at repair. Keep failures and actual logs. No annual/35day or extra scenarios.
- [ ] Independent audit both new trajectories; exact case/input/physical config checks; compare every pre-recovery decision and physical state/segment against original R0 with original engineering tolerances, preserving any difference as failure.
- [ ] Report repair-window P0-P3 and total demand/served/ENS, all three carriers, costs/energy/end inventory, command changes and recovery timing. Fault-window delivered result must remain unchanged. Evaluate whether cold ENS falls materially (registered reference: >=10% after repair), without using this target to alter the model or reject honest negative outcomes. P0/P1 deterioration against original acceptance criterion blocks promotion.
- [ ] Final Chinese report, source diff, actual command/exit/log records, passed/failed/not-run tests, assumptions/limitations. Plotting remains paused under the latest instruction until further user direction.

## Stop/continuation rules

Do not promote a candidate with source/energy/causality failure. A normal investigable error is fixed and narrowly rechecked. A physically valid negative result is reported, not hidden or repaired by changing capacity/fault/criterion. If R1 leaves a measured causal bottleneck, one separately registered R2 may address that bottleneck after recording R1; no parameter search or best-of-unreported attempts. No extra confirmation is required within the authorized post-repair dispatch scope.
