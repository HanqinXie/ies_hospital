# Audit-only V2 lifecycle correction

`final01` is retained with exit 1 and its original frozen source in `frozen_tools01`. Six primary native service/fee/storage/terminal checks passed, but both reference rows failed the additional V1 raw three-way state equality. This was a reporting checkpoint error, not a model or input change.

The native payload uses `initial_state = {state, executed}` (`src/hospital_ies/dataset_export.py:116`). Its nested `state` equals the independent reset certificate exactly. V1 incorrectly compared the wrapping object directly with that state, and also required the first actual interval pre-state to equal reset before any boundary processing.

The original frozen state-transition source calls `boundary()` before saving interval `pre_state` (approximately lines 479-485). At t=0 it records disconnected-contact request refresh, actual command arrival/contact restoration, and auxiliary request update from the executed dispatch (approximately lines 398-408 and 269-282). Actual normal and grid raw records each have those events. Only five state leaves differ between reset and first interval: one auxiliary-request scalar and four electric disconnection values.

V2 retains the V1 failed raw-equality diagnostic and adds separate strict checkpoints:

1. Complete reset state and core against the independent certificate and native `initial_state.state`.
2. Complete first interval pre-state against reset advanced through the recorded t=0 demand-refresh/restoration/executed-command events, using the unchanged pure auxiliary formula `(ABS*0.14 + sum(EC)*0.77)/RT_TO_KW` with coefficients from the frozen initial plant certificate.

No field is ignored in the reconstructed first-state comparison; an unrelated GT state change fails the synthetic regression. Missing restoration/execution evidence also fails. This is not a relaxed inventory-only check. All source code in D, the frozen planner, actual inputs, plans, requests and the two hospital trajectories remain unchanged.

Actual RED03_lifecycle reproduced two failing synthetic checkpoints before the correction. GREEN03_lifecycle passed all 11 synthetic read-only fixtures, exit 0. V2 is separately frozen before actual `final02`; V1 receipt, code, failed output, commands and logs remain intact. The same six-record CLI is retained. No model, solver or additional hospital run is performed.
