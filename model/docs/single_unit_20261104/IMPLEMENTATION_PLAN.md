# Single-unit fault study implementation plan

**Goal:** add separately named single tower-unit and single primary-pump branch outage cases with auditable normal/U/R figures, without replacing the old common-system failures.

**Architecture:** new derivative registration and explicit causal-to-functional scene adapter around the unchanged frozen minute worker. Original canonical validators and physical functions remain unchanged. A primary-pump failure is represented as a declared flow/interlock proxy that makes EC1 functionally unavailable, not as compressor hardware damage. It does not fail the common secondary pump or storage path. All actual flow/dynamics/queue/stock accounting uses the unchanged native implementation.

**Code:** D:/hospital_ies_single_unit_20261104, base911d758. **Raw:** E:/hospital_ies_single_unit_20261104_run01. **Delivery:** D:/云大文件/论文/医院ies/code_4_2/plotting/problems_8/single_unit_faults.

## Registered design, approved scope

- SU_TOWER: retain original T05 hour4137,H2,M_ready; TOWER_1 fails60..120 while commonTOWER and remaining three slots stayhealthy. It is one of four equal research aggregate slots, not a verified DOE cell-to-NMCSD tower mapping.
- SU_PUMP: retain original T06 hour5072,H2,M_available; EC_1_PRIMARY_PUMP fails60..120. EC1 was actually delivering throughout the original healthy pre-fault hour, so lowest active numbered branch is selected without reference to new outcomes. Only its functional cold-producing branch is interlocked. EC1 rawhardware, otherECs, commonAUX_CHW_PUMP, tower andstorage remainhealthy.
- Both180min continuousfrom0, native loads/uppercontrol/physicalmax1min, same fault andrepairtimes, original capacities/initials/rules/servicecriteria. Existing chiller dynamic hardtrip/ramp on functional outage applies; no new pump time constant, hydraulic power, flow, standby device or extra capacity invented.
- Main curves: original healthy normal(blue), fault/no optimization(red), fault/normal algorithm dispatch(green), black dashed current total demand, gray0..60relativefault. ActualkW and signed-normalized percent; zero normalundefined. All image labelsEnglish, 600dpiPNG+editableSVG, noPDF, no smoothing/interpolation/offsets.
- Add healthy/no-dispatch U0 perbackground as a necessary causal diagnostic: it separates loss of ordinary load-following from incremental fault impact. It is retained in data/report, not an extra main curve. U andU0 stopboth upper/loweroptimization after60 and use same existing fixed-reference physics/local protections.
- Six new producers: TOWER_U,TOWER_R,TOWER_U0,PUMP_U,PUMP_R,PUMP_U0. Two accepted normal producers may be reused only with exact originalpins and unchanged native-closure/input/initial binding; otherwise run newnormal rather than fake a source identity. Do not add yearly or35day runs.
- A single-tower negative result is legitimate: current3/4 capacity3165.167556kW exceeds original normal maximum rejection1754.600977kW. Do not change load/capacity/window to force fault loss or a green advantage. Normal itself has shortages; compare increments.

## Causal adapter contract

New CASE carries a new schema and full byte-authenticated canonical basecase, basecasefile pin, variant, declaredsinglefault, native-loweredscene and strict differencelist. Nativeworker builds from unchanged canonical basecase; reset is explicitly wrapped to accept only the registered declaredscene and pass only its authenticated loweredscene into native reset. Existing old validators are not relaxed.

For primarypump, lowering EC_1_PRIMARY_PUMP events to EC_1 functional availability is allowed only as an explicit one-cause functional interlock. Native event/segment functionalEC1=0 is accompanied by a separate raw cause ledger showing EC1hardware1/pump0/commonpump1. Never claim two exogenous faults or measured hydraulic flow. Runtime metadata binds declaration/lowering hashes; policy observations expose currentcause only, no futurecalendar. No _solve/_local_interval_action replacement; no extra physical commit.

## Tasks

1. New tools/single_unit_20261104/{registration,runtime}.py and focused RED/GREEN tests. Check exactcases/onlyallowedchanges, singlecause/raw-vs-functionalstate, healthyU0, and unchanged oldvalidator/physical guards. Exercise real reset interface before formal runs; label probes separately.
2. Separate runner/worker reuses canonical native lifecycle and logs. Native RESULT is immutable; extensions go to new sidecar (do not repeat known exclusive-result-overwrite defect). Freeze all source/input/case/admission bytes before runs; real perattempt logging and RAM-aware admission,max6/initial4, solver1thread, Etemp/output. Controlled stops/errors retained.
3. Independent newactual energy/stock/dynamic audit with unchanged tolerances and exact runtime sparse-default completion only. Validate same background/base physical config and initial state, pre-fault actual/policy agreement, post60 zerooptimization U/U0, exact declaredfault/functionalavailability and allnon-targetdeviceshealthy. Audit normalreuse sourceclosure explicitly; no fakeoneSHA.
4. Extract alltier/allcarrier data, main3curves and U0 diagnostic. Report absolute demand/served/ENS, faultincrements R-N and U-U0, allcost/energy/endstocks and repair recovery. Preserve all negative/tradeoff results.
5. Render2topics*3carriers*2styles=12logicalfigures, eachPNG+SVG. Reuse existingstyle; audit actual data alignment and exact current exports, keep editable vector marks/text. Save sourceCSV, newcode, assumptions/provenance, commandlogs and Chinese README in requested newfolder. Preserve all oldfigures/results.

## Verification and completion

Only focused meaningful regressions/interface probes, six required continuous trajectories, independent accounting, figure/source identity and visual checks. No unrelated fulltest suite, no parametersearch, no conditional outcome deletion. New assumptions and physical qualification limits stayexplicit. Task complete only after requested code+figures exist and all actual acceptancechecks pass, or a concrete external material blocker is identified; do not stop at a proposed matrix.
