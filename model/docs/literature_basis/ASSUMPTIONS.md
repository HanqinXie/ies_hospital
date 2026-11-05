# Explicit research assumptions and limits

The plant, capacities, efficiencies, loads, service priorities, protection
thresholds, legal actions and command queues are inherited unchanged from
47902b420168736169414ef96e82cdd141c5f97b. No controller receives future events.

1. ECM 2026 Table 2 describes externally assumed hourly Bernoulli failure
   probabilities and fixed unavailability durations, not hospital-estimated
   hazards, repair distributions or MTTR. The local source is R14, DOI
   10.1016/j.enconman.2025.120946. Original secondary sources are distinguished
   from independently read primary evidence in the source contract.
2. The main profile maps only three boiler capacity shares and one absorption
   chiller. Boiler startup/operation remains aggregated, not three separately
   controlled machines. Independent Bernoulli draws are the paper assumption.
3. The optional GT profile transfers a CHP parameter to gas turbines. This
   incompatible-equipment proxy requires explicit consent in the API and is
   reported separately; it does not calibrate gas-turbine reliability.
4. Yearly schedules start all-up with no inherited failure. Half-open interval
   union and carryout retention are this study's explicit endpoint conventions,
   not independently verified author-code behavior or a stationary population.
   Failed components continue to receive hourly draws, per the paper algorithm.
5. Shared tower and chilled-water pump checks use registered one-hour losses.
   Their probabilities are unknown. Shared-path effects are not added again as
   independent machine failures; extra residual common-cause parameters remain
   unknown. No new damage or stress-propagation law is introduced.
6. Native checks use the inherited 15-minute decision grid and actual event
   subdivision, 15-minute response delay, fixed M_ready rule and H0/H2 hardware
   profiles. Profiles never switch within a case. The 1-minute boiler electrical
   restart assumption is retained, not claimed as an OEM cold-start curve.
7. Native windows have local preparation and real continuous inventory after
   their one initialization. Their initial stocks do not come from replaying
   the preceding year. Each healthy/fault pair has the same preparation.
8. Each fault window retains at least 60 minutes after its last transition.
   Observation of a recovery tail does not guarantee complete thermal recovery,
   stock replenishment or clinical qualification.
9. The hospital AHU public CSV supplies equipment/fault-mode context. Timestamp
   duplicates, missing hours and a 2022/2023 description discrepancy prevent
   using label runs as independently verified repair events or annual rates.
   Hospital UPS work orders remain unavailable under a data-on-request statement.
10. Existing unknown startup/control-power energy, standby maintenance costs,
    full independent steam balance and endpoint clinical validation remain
    unknown. File integrity and test success do not supply those missing facts.
11. Twenty-four external annual calendars and eight conditional physical windows
    are different outputs. This goal performs no full8760 physical simulation,
    final12 evaluation, annual hospital failure-probability estimation or
    policy-superiority claim.
