# V30 evaluation, literature adaptation and finite comparison

Authority: USER_TASK_20261027.txt (later pasted task); USER_TASK_EARLIER.txt is retained for traceability. Continuous execution and local commits are authorized. No routine design/phase confirmation is required. This supersedes closed V29 phase scope, not physical/information boundaries.

Base: clean linked checkout D:/hospital_ies_v22_20261025, 38c5259301cc79ef6cedba1c9b5b50ef75fd0b65; new branch sci-v30-evaluation-literature. Original Git common directory and historical results are protected. New large artifacts E:/hospital_ies_v30_20261027.

## Design and decisions

Use an evaluation-only module for historical exports, a pure terminal-state serializer attached by a V30 environment adapter, and one independent multi-period literature prototype. Keep current physical integration and C/M planning rules. This preserves reusable native trajectories; changing the physical core or building a second simulator would create unnecessary revalidation and is not justified by a reproduced defect. Do not confuse thermal_coupling=null (unqualified room-temperature feedback) with existing GT/HRSG/ABS coupling.

1. G0: capture Git, complete source/input hashes, selected environment and all common configuration. Preserve two user tasks. Record attempt roles distinctly.
2. G1: reproduce missing pure fee, preparation/stock ambiguity, incomplete multi-carrier flag interpretation and stale last-decision terminal. Add meaningful failing tests, then minimal evaluation implementation. Export fully expanded C/M/F configurations with explicit mechanism semantics; no new F main arm.
3. G2: independently integrate native purchase/fuel/shaft cost and all carrier ledgers; re-export seven V29 runs without simulation. Test unknown/truncated artificial fixtures explicitly as fixtures. Pure terminal serializer must not call observe/decision_snapshot/solve/advance. New adapter verifies clocks/state/queues/inventory/solver count unchanged before and after; short real normal/fault runs provide hook integration evidence.
4. G3: parallel full-paper readings and page/formula mappings. Equation fixtures check units, useful heat/cold scaling, storage, finite fuel and fixed-on fuel with manual checks and infeasibility. Prototype preserves multi-period inventory and coupled energy. Audit plan-to-command rights before any replay; use one offline reference only, preserve infeasible/time-limit/no-incumbent separately. No direct inventory writes. Block only concrete unsupported execution portions, continue independent work.
5. G4: pre-register 8 finite backgrounds (2 per L1-L4), selected by original load and common normal exposure, not strategy results. At most 16 primary disturbed C/M runs plus matching normals, representative integration/reference runs and limited diagnosed retries. Initial working cap 48 actual trajectories / 24 experiment hours; serial physical runs, D/E only. If actual cost requires revision, record reason before new dispatch. Same preparation-before physical state/history/queues, common endpoints, after_cost, adopted loads, actual command delays. Prefix causality check for C/M before hidden future divergence. No broad search/ML/MPC/EP.
6. G5: audit all actual attempts, common initial conditions, actual terminal states and service/fee/resource tradeoffs; retain raw failed/rejected evidence. Run required default pytest once after final source implementation, then freeze. Package runnable source/input/evidence with lossless deduplication, aim <100000000 bytes, verify member CRC/SHA and independent extracted replay. New implementation changes after freeze require a new producer/freeze and affected reruns.

## File ownership

- Root: V30 __init__, terminal.py, environment/entry/config/scenario selection, runner pure-fee addition, integration and public tests; only root may edit shared physical transition code (none expected).
- v30_evaluation: tools/model_review_v30/evaluation.py, tests/test_v30_evaluation.py and E:/.../evaluation; no shared file edits.
- v30_hirwa_mapping: E:/.../literature/hirwa only.
- v30_rosales_mapping: E:/.../literature/rosales only.

All tests use actual command/exit/environment records. Historical test counts are provenance only. No changes to capacities, source loads, original priorities, thresholds, repair truth access or execution permissions. Remaining physical qualification limits persist. Each gate status is in RUN_PLAN.json with actual evidence pointers, not an optimistic completion marker.
