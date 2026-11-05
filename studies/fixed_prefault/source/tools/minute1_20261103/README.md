# Native minute research adapter

This is a separate sampling and execution version. Historical SF1/SF2 entry
points, source identities, inputs and outcomes remain historical.

`MinuteSource` accepts only four hash-bound native EnergyPlus minute files.
It checks clock coverage, all 14 critical zones, original priority allocation,
auxiliary accounting and the ordinary DHW useful-heat target. It never
interpolates or fills absent data. Source sizing and physical-quality review
are separate prerequisites; CSV validation alone does not establish them.

`registration.make_case` retains an authenticated canonical historical parent
and creates a distinct minute case. Five finite H2 backgrounds are admitted.
`scenario.step_minutes = 1.0`; `duration_steps` is 180 or 240. Existing start,
repair, protection and response clocks keep their real-minute values. The
original event response window is 15 minutes: commands generated during
minutes 60 through 74 share release time 75; it is not a new fixed 15-minute
delay after each command.

`with_minute1` directly composes the original readiness environment with the
unchanged SF1 continuous-loss storage and SF2 fuel mathematics. Their physical
model IDs remain explicit; `MINUTE1_SF2_20261103` identifies the new sampling
and execution contract. `reset` and `step` keep their public return interfaces.
Initialization uses native minute zero consistently in the reference solve,
the unchanged four static 15-minute initialization solves, connection basis
and cooling reference. Those static solves are not a continuous elapsed
warmup. The actual pre-event trajectory is the separate interval [0, 60).

For `variant='unattended'`, the runner stops calling the upper controller at
minute 60. The environment explicitly suppresses new command generation and
retains pending commands, executed permissions, local protection, automatic
behavior and external repair. `None` on an ordinary historical environment
still has its original meaning and is not a new suppression API.

Physical segments stop at each native input change and at earlier events.
The final actual segment carries `minute1_sampling` and
`minute1_source_context`, including the four input pins and actual/source
interval bounds. The latter is an explicit segment sidecar; the compiled
flow DTO is not claimed to contain it. A segment crossing a source minute is
rejected. Thermal references and ordinary DHW traces use the same current
native interval.

The runner requires a separately reviewed complete-source admission. It
does not substitute new bytes into an old approved source profile. Failures,
partial prefixes, unknown tails, solver sidecars and nonlinear evidence are
retained. A completed runner is only the first gate; independent physical and
service accounting must accept the producer before plotting or comparison.

The source is a DOE/PNNL/TMY3 model demand proxy. Minute sampling does not
establish measured hospital demand, OEM startup performance, hydraulic or
clinical certification. The source-quality receipts retain warnings, native
comfort differences and unresolved SQL completion flags.
