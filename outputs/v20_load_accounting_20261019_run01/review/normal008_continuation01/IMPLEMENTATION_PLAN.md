# Lightweight normal008 continuation

Authorized scope: new publication/continue_normal008.py and its dedicated tests; frozen science, old helper50ab, S3 and every existing terminal are read-only. ROOT will create the authorized STOP and decide when to run. This implementation never stops an existing process.

The accepted design uses a standard-library/psutil parent holding the same one-byte msvcrt normal_serial.lock. Only the existing registered v20_normal_008 can start once. Heavy project qualification runs in short-lived subprocesses, which must exit before resource admission or science. Historical same-72h C19 peaks are observations, not new M20 bounds. A stable observed window of at least30 seconds must satisfy both 1.1*historical peak+2GiB (3GiB is preferred when every sample supports it). No predicted release, averaged counter, unknown counter, partial attempt or concurrent science can admit the job. Low memory is recorded; an owned scientific child is drained, never automatically killed.

CLI contract:

- --qualify405 --receipt <new-path>: read-only pre-STOP 147 terminal batches/405 pairs plus registered008 notstarted, full148/406 census and366/241/28 binding. May be run immediately by ROOT while old helper still waits.
- --run --name L72_RUN01 --baseline-root <V19-root> --max-wait-seconds <bounded>: require actual old STOP/normal03exit125/S3failed, acquire old serial lease, short-lived preflight, stable resource window, execute008 once, short-lived strict406 postflight, frozen FINAL01 readback and final strict406 proof. --check-only records the same resource plan without science.
- Every runtime artifact lives under RUN/review/normal008_continuation01/<name>/. START/SUMMARY, preflight/postflight/final qualification receipts, exact command/exit receipts, v20_normal_008.result.json, SAMPLES and EVENTS are independent. Existing normal03/S3/009 and failed attempts are never rewritten. FINAL01 command receipt keeps its canonical RUN/logs/final_readback01.command.json location.

Implementation sequence:

1. Write meaningful guard/pre405 regression tests; capture actual RED.
2. Implement pre405 only; capture GREEN and expose it to ROOT before completing monitor.
3. Add stable-window, no-doublelaunch, stopped-chain and unconditional-drain regressions; actual RED before implementation.
4. Implement lightweight single-child parent and post406/FINAL01 handoff; capture GREEN.
5. Independent read-only review and source/frozen guard. Do not run science, stop helper, or execute actual --run.

Remaining analysis: analysis03 must explicitly join old10 measured resources, new008 one measurement, and009 unknown, preserving normal03 intentional125/S3failed. Old analysis02/once02 are not executed or rewritten.
