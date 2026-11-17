# R2.1.1 Final Report (English)

Run ID: `r2_1_1_20261117T114559Z` (2026-11-17 UTC). Host: Windows 11
build 26300 · RX 7900 XTX (gfx1100, physical adapter LUID 72346 selected
per case by the fail-closed resolver + canary + `add_provider_for_devices`).
Runtime: ORT-winml 1.30.1 · MSIX `Microsoft.WinML.AMD.GPU.EP.Framework.2`
1.8.65.0 · HIP `amdhip64_6.dll` (System32).

## Verdict summary

1. **P0 freeze** — no drift: PR #2 head `2e8894e` (local = remote),
   base `main@7156b9b` unchanged; protected dirs show 0 modified / 0
   deleted blobs; `r21_validate` OK (234 GPU PASS receipts validated).
2. **P1** — 7 contract-valid control-flow fixtures (opset 13);
   checker + shape inference + real CPUExecutionProvider runs +
   independent NumPy oracle: 19/19 inputs PASS (incl. all-zero NonZero
   and exact-cancellation count edges). The old `static_loop` contract
   violations are hash-recorded in FIXTURE_REGISTRY.
3. **P2 on-metal matrix (28 + 2 cases)** —
   `valid_loop_carried` **PASS_GPU_EXECUTED 4/4** (both EPs, both opt
   levels, 3 EP nodes, parity) → R2.1's "Loop is the barrier" REVOKED;
   scan outputs numerically wrong; NonZero returns fixed-count outputs;
   NonZero-in-loop (SSD-isomorphic) wrong shape+values; Conv→Loop hits
   an EP-internal parse_loop stride error (`{64,1,8,1}` vs packed
   `{64,64,8,1}`) → EP_FAIL → session-level CPU fallback. SSD full graph
   re-run reproduces `check_dynamic: nonzero`; all 180 SSD NonZero ops
   live inside the first NMS Loop body. See `SSD_CAUSALITY_TABLE.md`.
4. **P3** — errata supplement E-R2.1.1-1..3 (revoke / re-attribute /
   new defect classes); frozen R2.1 files untouched; README corrected;
   CI gains the R2.1.1 fixture build + CPU oracle + contract steps.
5. **Merge gate** — all P0 conditions GO at the audited state; merge
   executes only after push+PR CI are green at the exact final HEAD
   (see `finalize_summary.json`, post-manifest metadata by design).
   Decision: **MERGED_GO**.

## Artifacts (this dir)

BASELINE.json · A1_BLOB_AUDIT.json · FIXTURE_REGISTRY.json ·
CPU_ORACLE.json · gpu_matrix/ (28 receipts + raw logs + CSV) ·
ssd_full/ (2 receipts + stderr) · SSD_CAUSALITY_TABLE.md ·
R211_ERRATA_SUPPLEMENT.md · FINAL_CN.md / FINAL_EN.md ·
redaction_scan.json · manifest.sha256

## R3-A handoff

Merged-main SHA recorded in `finalize_summary.json` after merge; fork
PR #1 head `0baccb2` unchanged (Draft, CI green). New EP defect
families (scan numerics, NonZero fixed count, Conv→Loop stride) are
compiler/operator-layer issues, distinct from and unaffected by the
fork's DXGI bounds guard; FULL_FIX_GAP must list them separately.
