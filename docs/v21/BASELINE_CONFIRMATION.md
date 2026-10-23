# V21 baseline confirmation

The complete 407-line user task is retained as USER_TASK_20261023.txt. The current study starts from GitHub main c9226ea15d6cf9e2f0e1d6570d04929aecd26d2e, independently cloned to D:/hospital_ies_v21_20261023 on branch sci-v21-engineering. Original complete V20 and its V19 Git common directory remain intact.

Actual checks before changing production:

| Check | This execution |
|---|---|
| V20 repository verify | exit 0, 4.625 seconds |
| Supported baseline pytest | exit 0, 463 passed in 94.67 seconds |
| Published reproduction wrapper | exit 2, 20.546 seconds, no physical attempt started |
| Independent unchanged-M20 physical replay | exit 0, full 600 minutes / 59 native segments / 94 of 94 reference scalars exactly equal |

The published wrapper's old 48-hour campaign budget starts from historical 2026-10-19 attempts and is already expired on this study date. The failed command/log is retained. No clock, historical record, frozen implementation or budget was rewritten to claim that wrapper passed. A separate one-attempt budget invoked unchanged M20 run_episode/factory/analyze with the same original scenario/configuration/seed. The original-tree Python read guard observed 2941 operations and zero forbidden original-root reads. All project imports resolved inside the new clone.

The physical replay took 34.631 seconds; sampled process-tree peak RSS was 639553536 bytes and private was 2182672384 bytes. Its gzip trajectory was 3269680 bytes. These are one E-policy baseline case, not C-policy/long-window resource estimates.

The original V20 complete worktree and the new clone each matched all 366 frozen production-file hashes. The apparent dirty V20 tree contains already published effective work; it was preserved rather than overwritten or reset.

Exact commands, exit codes, raw stdout/stderr, the replay harness, immutable reference scalars, raw trajectory and proof are under E:/hospital_ies_v21_20261023/baseline. The physical proof is physical_replay01/PHYSICAL_REPLAY_PROOF.json. V21 establishes a new budget clock and source freeze rather than continuing the expired V20 campaign.

Post-change diagnostics are separate. An initial shared-regression run had 459 passes and four failures because default pytest temporary directories were on C, which the existing data-path gate correctly rejects. All affected files passed when actually rerun with an explicit fresh E-drive --basetemp. No scientific assertion or tolerance was changed for this environment issue. Later V21 gate logs list their own fresh actual counts.
