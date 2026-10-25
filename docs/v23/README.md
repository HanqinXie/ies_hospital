# V23 model correctness review

Read MODEL_CORRECTNESS_REPORT.md for actual outcomes and ASSUMPTIONS.md for the engineering boundary. The exact authorized task is USER_TASK_20261025.txt. This is M23_model_correctness, continued in the existing V22 worktree on sci-v23-model-correctness from local V22 commit 08eb3fbfec190cb19dc1a66a6a9162eb00cde872. The remote V21 baseline is older; do not reset the worktree to it.

Python entry points (run from the supplied project root):

```powershell
python -m tools.model_correctness_v23.validation --list
python -m tools.model_correctness_v23.validation --case cold_partial_tower --policy C --configuration H2 --output E:/hospital_ies_v23_reproduce/cold_C_new
python -m tools.model_correctness_v23.validation --case boundary_067_600 --policy C --configuration H0 --output E:/hospital_ies_v23_reproduce/067_C_new
python -m tools.model_correctness_v23.review --runs E:/hospital_ies_v23_reproduce/cold_C_new E:/hospital_ies_v23_reproduce/067_C_new --output E:/hospital_ies_v23_reproduce/review_new
python -m pytest -q
```

Outputs must be new directories. The review accepts explicit run folders; it never runs the plant or silently selects only successes. RESULT/STATUS/INVOCATION, native PROCESS, compressed RAW, source/input/config hashes, observed prefixes, three recovery clocks and missing/censored/N/A statuses are retained. A solver failure does not become a zero ENS result. Main plan is the registered small RUN_PLAN.json, not a Cartesian search. The standalone dispatcher used in this research is retained with the review package and records every actual child attempt.

The lightweight package contains the runnable source closure, required small original inputs/reference manifests, actual native output tables/selected RAW, baseline-bound patch, thermal native inputs/derived IDFs/frozen parameters and a separate portable temperature replay. Python dependencies are in the supplied requirements files; the physical solver requires a locally valid Gurobi license. Credentials and license files are not included. EnergyPlus26.2 is required only to regenerate the external native thermal runs; replaying the included measured input tables does not launch EnergyPlus. See the package's REPRODUCE.md and exact receipts for the independently extracted smoke run.

All five main policies share M23 physics and observations. H0/H1/H2 describe explicitly different hardware counterfactuals, not policy advantages. The half-minute storage integration case changes numerical precision only. The temperature coupling cases are exploratory and use no future repair or temperature labels. Original V22 results are historical references, never relabeled as M23 computation.
