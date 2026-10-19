# Hospital IES V19 repository collaboration

Read README.md, docs/REPOSITORY_REPRODUCTION.md and docs/v19/PAPER_MODEL_SPECIFICATION.md first.
This is a curated V19 source publication; historical absolute paths in signed records are provenance.

- Preserve the original 345-file production binding, 235-file test binding, input bytes, frozen configuration and historical evidence. Future model changes need a new named version and validation record.
- No MPC, future fault scripts, hidden execution permissions, capacity/load/threshold changes to make a policy win, or final-12 background access.
- Separate faults, commands, delivery, actual execution, protection and evaluation. Retain solver failures, fallback, unknown outcomes and full denominators.
- Keep the small historical files at their original relative paths. Run `python scripts/repository_assets.py verify` before and after changes.
- Put large local artifacts and temporary outputs on D/E. Never add raw trajectories, training data, portable stages, archives, third-party literature PDFs, credentials or solver licenses to Git.
- `python scripts/test_v19.py` runs the supported 13-file V19 targeted gate. `python -m pytest -q` is the historical full-suite entry point; it has not been certified for this compact publication profile.
- Changes to the same physical state transition must be serial. Reproduce a defect and add a meaningful regression before a minimum necessary fix; do not weaken assertions or hide a core defect with xfail.
- Record actual commands, exit codes, raw logs, passed/failed/unexecuted checks, assumptions and limitations. Do not copy prior results as a new run.
