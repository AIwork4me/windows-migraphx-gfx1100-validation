# R2.1-C — MIGraphX long-run stall Root-Cause Analysis (SOAK_RCA)

Historical fact under investigation (unchanged): R2 run `r2_20261117T004504Z`
soak `yolo26n__MIGraphX` = **FAIL_SOAK_NO_CLEAN_EXIT_HEALTHY_IN_WINDOW** —
last heartbeat at iteration 9856 (~6 min), no progress for ~20 min while the
process stayed alive; the R2 driver's `subprocess.run(timeout=20min)` killed
it. **No evidence the process had reached shutdown** (never call it an
exit-path hang). R2 AMDGPU control (same wrapper, recreate@2000, 15 min)
PASSED with 24,000 iterations / 12 recreates.

## Experimental design (run r21_20261117T040739Z, host = same physical XTX)

Instrumentation: `runner/r2_1/soak_rca_child.py` writes fsynced JSONL stage
events (`spawn, register_ep:*, resolve_device, create_session:*, warmup,
run:*, recreate:*, session_release:*, gc_collect:*, end heartbeat, finalize,
interpreter_exit`) — before AND after every potentially hanging call — plus
30 s heartbeats and per-500-iteration checkpoints. Supervisor
`soak_rca_supervisor.py` samples liveness/CPU/RSS/threads via psutil every
5 s, applies per-stage no-progress grace (compile/release/gc 420 s, run
300 s), snapshots forensics, and kills only its owned child.

Watchdog validated first by fault injection (soak_fault/): normal exit
(exit 0), nonzero exit (rc 7), alive-without-progress (sleep before run),
and blocked-recreate (real GPU session, blocks at 2nd recreate after a
clean 1st) — all four classified correctly with stage-level last-event
evidence; no false PASS.

Single-variable matrix (identical model SHA 4fc60f7e…, deterministic
seeded inputs, same ORT 1.30.1/MSIX 1.8.65.0/driver 32.0.21030.2001, same
fail-closed device resolver + canary, sessions opt=ALL; one GPU child at a
time; per-case bound 25 min incl. grace):

| case | EP | session policy | budget | result |
|---|---|---|---|---|
| c1_amd_control_persistent | AMDGPU | persistent, no recreate | 6 min | NORMAL_EXIT, 18,514 iters, exit 0 |
| c2_mgx_persistent | MIGraphX | persistent, no recreate | 12 min | NORMAL_EXIT, 39,684 iters, exit 0 |
| c3_mgx_recreate2000 | MIGraphX | recreate every 2000 (R2 cadence) | 16 min | NORMAL_EXIT, 24,000 iters, **12 clean recreates**, exit 0 |
| c4_mgx_recreate4000 | MIGraphX | recreate every 4000 | 10 min | NORMAL_EXIT, 20,273 iters, 5 clean recreates, exit 0 |
| c5_mgx_teardown_interpreter | MIGraphX | single session, explicit `del`+`gc.collect`, then full interpreter shutdown | 5 min | NORMAL_EXIT, 14,943 iters; session_release 0.04 s, gc 0.005 s, interpreter shutdown completed (no hang) |

Stage timing observed (c3): session create ≈ 42–43 s (MIGraphX compile),
release ≈ 0.04 s, recreate totals well inside the 420 s stage grace; run
phase ≈ 3,500 iters/min with the lean input path.

## Crossing the historical failure boundary

R2 stalled between heartbeats shortly before the recreate due at iteration
10,000 (recreate #5 of 12). c3 crossed iteration 10,000 and completed
recreates #5 and #6 (and #7–#12) cleanly within the same budget the R2 run
had (15–16 min), on the same model/EP/driver/host, under a supervisor that
would have localized any stage hang to its exact boundary. c2's 39,684
iteration persistent run and c5's clean teardown+interpreter-exit further
exclude `sess.run` accumulation, session_release, and interpreter shutdown
as reproducible stall points under these conditions.

## c6 — exact R2 input-path counterfactual (COMPLETED)

One controlled difference remained between R2's child and c1–c5: R2's
`soak_child.py` rebuilt feeds through `session_inputs()` on EVERY
iteration (fresh rng + array allocation per iter) and rewrote the whole
receipt JSON each heartbeat. c6 reran c3's exact configuration with
`--r2-inputs` (per-iteration `session_inputs()` replication).

**Result: NORMAL_EXIT** — 960.4 s wall, **25,849 iterations, 12 clean
recreates**, exit 0, `interpreter_exit` reached. Throughput 25,849/960 s ≈
1,615 it/min — statistically indistinguishable from R2's observed ~1,620
it/min, i.e. the R2 workload profile was faithfully reproduced (input-path
churn included) and still did not stall: the run crossed iteration 10,000
(R2's failure point) at the same cadence and completed recreate #5 of 12
cleanly. Receipt: `soak_matrix/c6_mgx_recreate2000_r2inputs.receipt.json`.

## Verdict

**`RCA_NOT_REPRODUCED`**: across 6 controlled experiments totaling ~65 GPU
minutes — AMDGPU control, MIGraphX persistent (39,684 iters), R2-cadence
recreation through 12 cycles (twice: c3 and c6), doubled recreation
interval, explicit teardown + interpreter shutdown, and c6's R2-exact
input-path replication at matched ~1,615 it/min throughput — the R2 stall
did not recur. Stage-aware instrumentation proves every recreate boundary
(create_session ≈ 42 s, session_release < 0.1 s) and interpreter exit
complete normally. The R2 historical FAIL stands as recorded; with the
evidence available it is most consistent with a transient host/runtime
condition (single occurrence; R2 history records launcher/orphan-process
contamination of earlier attempts; no reproducible stage boundary), NOT
with a deterministic MIGraphX recreate/run/teardown defect. No upstream
filing is justified from this single confounded historical stall (mission
constraint upheld).

Residual uncertainty (honest bounds): R2's exact process environment
(its driver also rewrote the full receipt JSON every 30 s; earlier orphaned
experiment processes existed in that session's history) cannot be fully
reconstructed; "not reproduced in 6 controlled attempts incl. an exact
input-path replication" is the strongest defensible claim.
