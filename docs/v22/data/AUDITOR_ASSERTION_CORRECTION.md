# Cold-addon audit assertion correction

The actual `audit_cold_addon.py --authorized --output .../addon01` returned exit1: 98 PASS,2 FAIL,0 UNKNOWN. Both failures were the additional assertion that a healthy, connected GRID must have positive import in **every** fault interval. All actual source, cold, storage, station and service balance checks passed. The failed receipt and both original trajectories remain unchanged.

The first held command has arrived GRID ceiling0. Actual GT3 supplies192.997223418kW, including the full-store pump1.583297562kW; GRID hardware is healthy and bus connected. During [0,60), later actual GRID import integrates367.690445313kWh (full) /366.950448414kWh (empty). Zero import at the beginning is physically legitimate, not evidence of unavailable GRID or free pump energy. No physical tolerance, capacity or output was changed.

The original auditor is preserved byte-for-byte as `audit_cold_addon_before_grid_check01.py` (SHA292ffa7c6dbc4f0a5ec730eca992bf0cae384051b95102e472f183cdad35f584). Its invalid mandatory-import assertion was replaced with an aggregate positive actual import observation over the stated fault window; healthy hardware and actual GRID connection remain checked **at every native interval**, and the unchanged original physics audit separately verifies actual source power pays the pump. This checks real GRID access without requiring inefficient mandatory imports.

New focused regression `test_addon_grid_import.py` first failed with a missing helper (exit1), then passed3 tests (exit0). It covers legitimate initial zero import followed by paid import, no import during the fault even if later import is positive, and native partial overlap integration. Logs: addon_grid_{red01,green01}.{stdout,stderr}.log.

Rerun goes to fresh `addon02` with original addon01 preserved. This is an auditor-semantic correction only; zero new IES episodes or solver calls, D frozen source unchanged.
