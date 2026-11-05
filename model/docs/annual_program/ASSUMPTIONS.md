# Annual research assumptions and boundaries

1. H2 is the fixed main research plant (60 kWh_c/120 kW_c storage; no UPS).
   H0 and H1 are separately simulated equipment sensitivity, never swaps.
2. Normal grid, natural gas and makeup water follow the inherited model's
   available external supplies. Existing explicit fault ports only. No newly
   invented gas-header, independent fan or condenser-pump topology/failure rate.
3. Diesel remains a 2400 kWh initial shaft-equivalent work budget inherited from
   the registered source. No automatic month/year refill or litres conversion.
4. Functional normal-supply requalification uses the previously registered
   60-minute assumption equally for all methods; 30/120 are named sensitivities.
   It is not DC charging, charger capacity or measured battery autonomy.
5. Boiler1/5/15-minute settings describe electrical requalification under the
   inherited model. They are not OEM cold-start curves. GT start/HRSG and cold
   output dynamics retain inherited equations and their known qualification gaps.
6. Initial state is the inherited same-first-hour initialization with the original
   seed/preparation. It is not a measured prior-year state or a stationary sample.
   Storage initial charge is counted once; unmetered startup/purge/warm-standby
   fuel and maintenance are unknown, not proven zero.
7. Only inherited USD2001 model fees are available. Report starts, readiness
   hours, stock throughput and extra-price break-even boundaries. No claim of
   present-price hospital total cost or fully priced standby economy.
8. Demand-only/combined main years increase the existing ER_TRAUMA1 terminal
   cold-service request by25 percent; direct electrical and heat requests do not
   duplicate this increment. This is a conditional service-demand experiment,
   not a calibrated clinical occupancy/room-temperature prediction.
9. The complete original8760 support is explicitly authorized for the new frozen
   nonblind study. Old benchmark scenes/results stay read-only; actual source
   parsing/processing exposure is recorded, even when an invocation fails.
10. Year end stops integration at525600 min and preserves unfinished states.
    No actual next-year source exists; repair/service/stock recovery after that
    boundary is unobserved, not zero or inferred from repeated last-hour data.
11. ECM main and GT-proxy calendars retain their original source, fixed-duration
    and hourly-draw conventions. Missing/common-cause rates remain unknown.
    The hospital AHU/UPS sources do not calibrate this plant's failure statistics.
12. Helper tests, exact model accounting and simulated service continuity do not
    establish OEM, wiring, hydraulic, clinical environment or real-hospital annual
    reliability qualification. Remaining unknowns travel with every annual claim.
13. N/F and D/FD retain the identical physical configuration and propagation-off
    setting. D/FD use native stage2/L2 solely because the inherited L1 parser
    rejects zone load growth. FD remains the paired external calendar plus the
    same demand increment; it is not an imposed or validated cascade experiment.
    The disabled residual-demand GT stress proxy is unchanged. Capacity limits,
    no-export/minimum-output interlocks, finite fuel and functional supply gates
    remain active. Original unshocked first-hour initialization is retained; the
    demand increment begins at t=0 during actual integration, with its original
    command wait, rather than preparing a different increased-load initial state.
14. A specified main fault calendar must demonstrate positive in-horizon
    availability loss. A literature random draw with zero realized faults is a
    legitimate year, never filtered or redrawn to create failures. Only an empty
    calendar uses physical N/D; nonempty zero-exposure calendars retain F/FD and
    their original events because event response clocks can still differ. Both
    the observed exposure and random-calendar provenance are recorded. Current
    make_case F/FD requires a nonempty calendar; it is not the random-year route.
15. Recording is explicitly frozen as full_v1 (default) or engineering_v1.
    Engineering mode omits only recognized normal successful internal probe
    row tables, retains their original primal/non-row values and declares that
    omitted rows cannot be recovered. Actual execution, failure, recovery,
    fallback, projection and unknown roles retain the full logging path.
    This changes neither solver/physical tolerances nor equipment assumptions.
    Mode cannot change during continuation. It grants no complete-internal-row,
    lossless-archive or annual resource qualification; size/time benefits still
    require matched actual pilots, and old complete records remain read-only.
