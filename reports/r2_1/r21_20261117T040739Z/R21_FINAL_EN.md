# R2.1 Final Report (EN) — Evidence Freeze, Guard Validation, Soak & SSD RCA

Run `r21_20261117T040739Z` on the physical Windows 11 26300 + RX 7900 XTX
(gfx1100) host, driver 32.0.21030.2001, onnxruntime-windowsml 1.30.1
(plugin-EP build), official WinML AMD EP MSIX 1.8.65.0 (untouched). Source
R2 run `reports/r2/r2_20261117T004504Z` frozen and unmodified. The Chinese
master report is `R21_FINAL_CN.md`; this is its English mirror (same
facts, condensed). Final HEAD is pinned in PR #2's description (verified
via git ls-remote / gh api) rather than in a self-referential commit.

## Verdict table

| area | verdict |
|---|---|
| Evidence freeze | 464/466 modified legacy files restored to merge-base bytes (blob-identical, 0 mismatch); PR diff 1821→1362; R1/R2 raw receipts untouched |
| Counts | 290 = 234 PASS + 54 fixture-defect + 2 SSD FAIL; per-EP 117 PASS = 111 synthetic + 6 real; manifest 1319 entries, 0 hash mismatch (1314 claims were a stale snapshot) |
| CI false-positive | closed: CPU-only receipts now FAIL the gate (19 adversarial tests); 234 genuine receipts still validate; green in CI |
| C++ guard | INT_MAX assertion bug fixed; compiled AND run on g++/clang++/MSVC with -Werror//WX (CI 44016624979) — helper validated; native EP build remains BLOCKED_TOOLCHAIN; no upstream posting |
| Soak RCA | `RCA_NOT_REPRODUCED` — 6 controlled cases incl. exact-R2 input-path replication all clean (39,684 iters persistent; 12 recreates ×2; teardown+interpreter exit); historical R2 FAIL stands |
| SSD RCA | `LOOP_BARRIER_ISOLATED`: EPs accept+compile the whole graph, the fused kernel fails at runtime (EP_FAIL), ORT falls back to a CPU-only session → 5,587 CPU events, 0 EP nodes; reproduced at 4-node scale; kernel-internal cause NOT isolated (closed MSIX) |
| Release | READY_FOR_OWNER_REVIEW (not production-ready, not upstream-ready) |

Key stage timings (MIGraphX): session create ≈42 s, release <0.1 s; every
boundary evidenced by fsynced JSONL (`soak_matrix/*.jsonl`). Workaround
direction for SSD-class graphs (suggested, untested): eliminate control
flow at export time (unfold Loops / static NMS); CPU fallback preserves
numerics, so correctness is unaffected.

Evidence: everything under `reports/r2_1/r21_20261117T040739Z/` with
`manifest.sha256` (self-check 0 mismatch; manifest/finalize/redaction files
excluded by design, R2 contract); tooling `runner/r2_1/`, gate wired into
`.github/workflows/r2-cpu-tests.yml`; fork changes @ `0baccb2` with
`.github/workflows/guard-helper-cpp.yml`.
