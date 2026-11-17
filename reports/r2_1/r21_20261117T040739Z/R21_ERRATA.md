# R2.1 Errata — corrections to R2 publications (run r21_20261117T040739Z)

Scope: corrections are **additive documents**; frozen R2 run directory
`reports/r2/r2_20261117T004504Z/` is untouched. Raw receipts are never edited.

## E1. Per-EP PASS composition ("逐 EP 各 117 合成 PASS" was conflated)

`reports/r2/R2_FINAL_CN.md` line 50 says per-EP 117 *synthetic* PASS.
Actual ledger-derived truth (R2_RECOUNT.json):

- per-EP total PASS = 117 = **111 synthetic + 6 real-model**
- synthetic total: 276 = 222 PASS + 54 SKIPPED_FIXTURE_DEFECT
- real total: 14 = 12 PASS + 2 FAIL_NO_GPU_COMPUTE (both ssd_mobilenet_v1_10)

## E2. YOLO26n speedup metric (mean ratio presented under a p50 column)

`reports/r2/R2_FINAL_CN.md` table columns show "GPU p50/p95 / CPU p50",
but the printed 3.46× / 3.63× are `gpu_vs_cpu_speedup_mean`
(ratio of mean times; receipt field name is explicit):

- AMDGPU: mean 9.998/2.888 = 3.46× (p50 ratio would be 3.59×)
- MIGraphX: mean 10.087/2.782 = 3.63× (p50 ratio would be 3.72×)

Both are **warmed, model-only `Session.run`** measurements (n=100 GPU,
n=30 CPU, warmup 10), NOT cold-start-inclusive and NOT full
preprocess+NMS end-to-end throughput. Session creation ~41.8 s is excluded.
Correct phrasing: "warmed model-only mean-time speedup ≈3.5×".

## E3. Stale counts (1314/1319/1320)

`R2_FINAL_CN/EN.md` said "1314 files / manifest 1314 entries" — a
pre-refresh snapshot. Final state (MANIFEST_RECON.json): manifest
**1319 entries**, all hashes verify; run dir 1322 files at finalize time;
the 3 extra files are post-manifest metadata excluded by design
(manifest.sha256 itself, finalize_summary.json, redaction_scan.json).
No R2 receipt was modified for this reconciliation.

## E4. Stale branch HEAD in LATEST.md

`reports/r2/LATEST.md` listed branch HEAD `a4688c9` (an interim R2.5
commit). Last R2-verified HEAD at publication was
`f32f4aa1ffce4872608e5e94874a5ee15f08a634` (verified: local == remote ==
PR #2 headRefOid at 2026-11-17T04:07Z). R2.1 commits after that are
listed in R21_FINAL and verified live via `git ls-remote` / `gh api`
(pinned in the PR description, not in a self-referential commit).

## E5. bus.jpg provenance wording

`MODEL_PROVENANCE.csv` note "NO (gitignored weights; hash only)" is wrong
for bus.jpg: the image **is tracked** at `artifacts/models/bus.jpg`
(commit c497143, present in PR #2 diff). sha256 re-verified
c02019c4979c191eb739ddd944445ef408dad5679acab6fd520ef9d434bfbc63.
License: AGPL-3.0 Ultralytics official public sample image; redistribution
permitted with attribution; no private data. ONNX *weights* remain
gitignored/hash-only — that part of the CSV is correct.

## E6. MODEL_MATRIX.csv empty projection

R2's `MODEL_MATRIX.csv` contained only its header (projection bug).
Corrected projections are **new derived files** in this run dir:
`R2_OPERATOR_DERIVED.csv` (290 rows) and `R2_REAL_MODEL_DERIVED.csv`
(14 rows), cross-checked against ledger identities (0 missing / 0 orphan /
0 duplicate / 0 classification mismatch).

## E7. Soak status vocabulary

R2 MIGraphX soak remains `FAIL_SOAK_NO_CLEAN_EXIT_HEALTHY_IN_WINDOW` —
historical verdict unchanged. R2 prose sometimes leaned toward "exit-path
hang"; precise statement: last heartbeat at iteration 9856 (~6 min), no
progress for ~20 min while the process stayed alive; **no evidence the
process had reached shutdown**. Stage-level isolation is R2.1-C's job
(SOAK_RCA.md).

## E8. CI gate strength

R2 `runner/r2/ci_r2_check.py` accepted any non-empty `nodes_by_provider`,
so a CPU-only receipt could satisfy the profiler gate if the EP name
appeared in `session_providers` (false-positive pattern). Superseded by
`runner/r2_1/r21_validate.py` (wired into r2-cpu-tests workflow):
requires `nodes_by_provider[exact_requested_ep]` to be a finite positive
integer, typed per-output parity, LUID+canary evidence, ledger<->matrix
identity, and manifest byte verification; 19 adversarial unittests prove
each forgery pattern fails.
