# Literature basis, approved first step

Authorization: the user selected the three-part first step and explicitly asked
to create and run a goal. This implements that accepted design without a new
approval cycle. It does not authorize a full 8760-hour physical simulation.

## Decision and boundaries

Use a small opt-in literature module and the unchanged annual-readiness physical
runner. A documentation-only bibliography would not make an executable experiment;
a replacement reliability model or full annual run would exceed this first step.
The chosen work is source-bound external schedules, independent hospital-data
diagnostics, shared-support mapping, and bounded matched physical checks.

The source workspace is D:/hospital_ies_literature_basis_20261030, parent commit
47902b420168736169414ef96e82cdd141c5f97b. The parent workspace and all earlier
results remain read-only. Outputs and temporary data use this E-drive directory.
No capacities, DOE/IDF loads, physical equations, service criteria, action rights,
queue delays, protection settings, or normal-operation model will change.

## Source interpretation

ECM Table 2 contains hourly failure probabilities in percent and fixed failure
durations in hours. The paper samples every component every hour, including while
down. Repeated triggers can extend downtime. The experiment records both raw
triggers and the union of resulting half-open unavailable intervals.
The half-open convention, an initially up year without unknown carry-in, and
retaining year-end carry-out are new explicitly named experiment conventions.
They are not presented as recovered author code or a stationary initial state.

The main mapped profile contains BOILER_1/2/3 and ABS only. The boiler ports are
three equal capacity shares but retain the original aggregated operation/start
state. They must not be called three independently controlled boiler models.
CHP-to-GT transfer is an explicitly opt-in proxy profile, never the main baseline.
Missing grid, gas, tower, pump, UPS, DG and residual-common-cause rates stay null.

Schedules are external scenario files. No controller receives future events.
Offline schedules cover 8760 nominal hours without loading hospital demand or
executing sealed days. Physical checks use registered, complete unsealed source
windows only. A generated calendar is not a simulated annual service trajectory.

## Hospital and common-support evidence

Keep Figshare version 3 raw files unchanged, validate publisher metadata, bytes
and hashes, and diagnose actual identifiers, clocks, duplicate keys, missing data
and labels. Any paper/file year disagreement stays visible. Label runs can only
be described within unique, consecutive recorded hours; duplicates, conflicts,
gaps and boundaries censor the run. They are not repair durations or failure
arrival rates. A public data claim or metadata response is not acquired raw data.
UPS work orders declared available on request remain unacquired; no email is sent.

The dependency register distinguishes one shared support failure from independent
converter faults and uncalibrated extra common-cause events. Existing tower/pump/
AC/DC effects are counted once through the original model. No new damage cascade,
new wiring or arbitrary joint-failure probability is introduced.

## Actual execution and completion

Use healthy/fault pairs with the same current rule, initial state, configuration,
source support, response wait and service standard. Eight planned cases: boiler
pair (27 h, one boiler port down 24 h); absorption pair (51 h, down 48 h); shared
tower pair (3 h); shared chilled-water pump pair (3 h). Short pairs run first.
All cases are fixed conditional mechanism checks, not representative annual
inventory replay and not a strategy-superiority contest. They preserve shortages.

Only one physical process tree may run at a time. Limit this goal to eight planned
cases plus at most two diagnosed technical retries, 12 h actual experiment wall
time from the first producer, a 6 h per-case bound, and 10 GB owned-tree RSS.
Require available RAM >= 4 GB and E free space >= 30 GB. Estimate longer cases from
actual short pilots and prior immutable runtime observations with a conservative
factor; resource forecasts are screening estimates, not guaranteed bounds.
All source/input/evidence hashes and the plan are frozen before physical runs.
Failure, rejected admission, missing output and unknown tails remain explicit.

Completion requires source contracts, tested opt-in schedule generation, public
data evidence and its limitations, dependency mapping, a frozen small campaign,
actual matched checks with full observations, independent service/stock/fee
accounting, current tests, source/input preservation and owned-process closure.
No review archive or public release will be built.
