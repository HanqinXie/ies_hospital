# Annual transfer refresh implementation plan

Goal: Deliver only `transfers/normal_year` and `transfers/busy_year`, both using one new source revision with the latest accepted equipment dynamics and declared single-unit failures, for Windows EPYC9654 /256GB. No scientific trajectory runs on the packing machine.

Approved cadence: original8760 hourly inputs held at their original boundaries;15-minute upper dispatch; at most1-minute physical segments. No invented full-year native-minute input or interpolation. Original physical capacities, service priorities, controls and comparisons remain.

Architecture: retain the annual continuous runner, disk history, journals, process ownership and recovery machinery. Add an opt-in `annual_dynamic_20261104` profile to annual registration/runtime; reuse the accepted continuous inventory, fuel-path and low-order cooling implementations. Export a separately identified engineering actual-owner ledger and independently audit it. Preserve the legacy native-primal route for legacy cases.

Responsibilities:

1. Root: complete archive, source clone, opt-in annual registration and runner wiring, typed checkpoint additions, package assembly/source provenance and final cleanup.
2. Physics agent: exclusively owns the new physical bridge and its pure-component tests in shared_physics; no concurrent edits to the same physical state transition.
3. Server agent: pure host/topology profile, new independent accounting CLI and focused interface tests in shared_server.

New busy calendar preserves the existing demand window and old dates/durations except the explicitly migrated T05 target from wholeTOWER toTOWER_1. Add a separately registered EC1 primary-pump fault at the first valid September15 14:00 normal-reference anchor,60minutes, search forward at most6days before any loss-based selection. Original E6a/b common-tower partial double pulse remains an annual repeated-recovery extension; do not relabel it as the new single pump or as an identical copy of the local natural-recovery figure.

Checks: focused actual RED/GREEN interface tests for registration, state fingerprint/recovery, declared-to-functional lowering and new ledger admission; no default scientific suite or IES run locally. Regenerate canonical cases, source/input hashes, exact normal-reference identity, manifest and documentation. On the real server require fresh full default tests, same-source short/recovery/independent-accounting and host admission before annual execution. Old passed/admission receipts are history only.

Delivery: preserve complete backup outside transfers; final package directories contain only current active tooling, pinned source, cases, required immutable fixtures and clearly scoped evidence. Verify member/hash coverage and independent sourceGit objects. Clean old transfer variants and looseZIPs only after candidate verification and recoverability checks; never delete external simulation roots.
