# V31 read-only evaluation plan

The authorized task fixes twelve records, two contexts, three methods per context and two modes. The eight C/M records are reused V30 N02/D02 producer evidence; four preparation variants are new V31 evidence. No model or solver is called.

CLI: `python -B evaluation/audit_v31.py --root ROOT --manifest EXPERIMENT_REGISTRATION.json --support audit_support --output FRESH`.

The registration supplies `runs`, each with `id`, `context`, `mode`, `method`, `evidence_role`, `path`, `freeze_file`, `case_id`. It may also supply `allowed_changed_source_files`; all shared source bindings outside this explicit list and all input bindings must remain equal. New source files may be added. Physical-core and accounting/evaluation files cannot be allowlisted. Each producer is checked against its own freeze, rather than pretending both whole source trees are identical.

The support directory contains byte-preserved V30 `audit_campaign.py` and `FROZEN_ACCOUNTING.json`. Reuse its raw service/fee/storage arithmetic, terminal validation, snapshot decoder, exact timing normalization and prefix comparator. Never import the simulator or instantiate an environment.

- [ ] Write small synthetic tests for signed carrier-specific changes, missing records, manifest provenance, exact prefix normalization and preparation-window boundaries.
- [ ] Run tests before implementation; retain command, exit and full log under this directory.
- [ ] Implement `audit_v31.py`: explicit selection, read-only per-run audit, preparation and mechanism windows, six paired normal/disturbed deltas, shared-source/initial-state/prefix checks, partial/complete gate.
- [ ] Write UTF-8 JSON/CSV/Chinese Markdown with full scalar repr, carrier units, no percentages or cross-carrier score, compact terminal/command summaries and native pointers.
- [ ] Run targeted tests, an actual eight-record partial audit when registration exists, then the final twelve-record audit only after the parent provides four real completed runs.
- [ ] Freeze tools, support lineage, commands, logs and actual output hashes. Do not manufacture a completed result or archive receipt.

Preparation accounting separates actual [0,60) operation from initial inventory preparation. The original initial fee is counted once in the episode total. If a window cuts a DG integrated receipt, shaft work is unknown for that partial receipt; it is not interpolated. For continuous ramping GT fuel, follow the existing native linear-output window rule. Actual end states are recorded only at observed exact boundaries. All synthetic fixtures are labeled synthetic and are not trajectories.
