# Hospital IES latest model and result publication plan

> Use the writing-plans workflow with independent dependency, result and publication review. The user's existing autonomous authorization and explicit repository-update request permit routine design choices and remote publication without another approval gate.

**Goal:** Replace obsolete material on the repository's default working tree with the verified V30 fix1/C02 physical baseline, V31 preparation controls and V32 external reference, including readable results and remotely recoverable evidence.

**Architecture:** Keep the V32 scientific source set (873 files) and all 36 original input bindings byte-identical. Use the verified 980-file portable project as a seed, then add the complete closure of required test fixtures and source references before removing obsolete tracked output/docs. Preserve previous remote main and scientific producers by annotated tags and a verified local Git bundle. Use ordinary descendant commits and a fast-forward push; never force-push or rewrite history.

**Tech stack:** Git, Python3.10, existing pytest, connected GitHub read/ref tools, GitHub REST for release assets (the connector has no release-asset methods). Credentials remain only in memory through the existing credential manager.

## Phase 1: Current evidence and recoverability

- [ ] Read remote default branch/refs and actual repo metadata; compare remote main c9226ea with local scientific HEAD575cb02 and require ancestry.
- [ ] Verify actual V32 delivery receipt, original ZIP digest and all873/36 current paths. Retain authentic V30/V31 hashes and actual audit scope.
- [ ] Save pre-change tracked inventory and hash manifest, create and verify a local bundle of the scientific branch and remote-main ancestry, and create explicit backup/scientific tags.
- [ ] Use the existing isolated linked checkout, create publication-v32-20261028 there, and preserve sci-v32-external-reference at575cb02.

## Phase 2: Curate and clean

- [ ] Dependency reviewer produces keep/remove manifests. Do not implement the initial982-file candidate until required default-pytest historical imports/data fixtures have been resolved. Keep pytest.ini byte-identical.
- [ ] Copy verified readable V30 32-record, V31 12-record, V32 6-record reports/tables/registrations/freezes into results/, with accurate roles and information conditions. Native full evidence and failure histories remain in the three original under100MB ZIPs for release assets.
- [ ] Root creates current README.md, current publication authority at the top of AGENTS.md, a short .gitignore retaining required frozen fixtures, docs/publication/ plan/catalog/cleanup ledger and a read-only current-source verification command. Do not alter any frozen scientific file.
- [ ] Remove only explicitly listed Git-tracked obsolete files using one checked Python filesystem operation rooted inside the existing D workspace. No recursive cleanup of old E research evidence or other worktrees. Preserve deleted path/blob/size and recoverable commit/tag mapping.

## Phase 3: Validation and local review

- [ ] Rehash873/36 and all staged result copies; require zero modified frozen files. Verify deletion set is disjoint from the dependency keep set.
- [ ] Run frozen default `python -B -m pytest -q` once because broad file removal risks old default fixtures; run the explicitly selected V31/V32 tests (not silently omitted by the older frozen pytest selection). Preserve commands/exits/stdout/stderr, failures and finite repairs. No tolerance/criterion/assertion changes.
- [ ] Run current V30/V31/V32 public entry `--help` and source/import/resource checks. Do not start a new scientific comparison or plan/episode campaign.
- [ ] Independently review the exact Git diff, README claims, cleanup ledger, source bindings and release manifest. Commit concrete reviewed tree.

## Phase 4: Publish and verify

- [ ] Before push, reread live remote main; require unchanged expected head or integrate and re-review any new remote work. Push backup/scientific tags then the descendant publication commit to main with normal fast-forward semantics.
- [ ] Create a release for the published commit with three verified packages (each below100000000 bytes), SHA256SUMS and provenance index. Do not delete existing branches/releases/assets.
- [ ] Verify remote main SHA/tree, read current README and result/source binding files remotely, inspect all asset sizes/digests and stream-download each new asset to compare SHA256. Keep transient retry/failure logs without exposing credentials.
- [ ] Write final receipt with published commit/URLs, source verification, tests, actual cleanup count, original ZIP hashes and remote verification. Stop when current main and artifacts are verified.

Self-review: the remote-main update is now explicitly authorized. Current model identity and historical scientific producer are separate. Old numeric-version names alone do not determine deletion; required source and test fixtures remain. Results are curated from verified final runs without inventing new simulations or treating V32 as a same-information winner. Repository cleanup means a current-tree change, not destructive history rewriting.
