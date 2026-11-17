# R2 Final Report (EN) — Windows AMD EP device identity & real-model validation

Run `r2_20261117T004504Z` on native Windows 11 26300 + RX 7900 XTX (gfx1100),
onnxruntime-windowsml 1.30.1, official WinML AMD EP MSIX 1.8.65.0 (untouched).
Evidence root: `reports/r2/r2_20261117T004504Z/` (1314 files, self-verified
SHA256 manifest, 0 redaction hits). Chinese master report: `R2_FINAL_CN.md`.

## A. Executive verdict

- **R2-A APPLICATION GPU READINESS: GO (this host).** With the fail-closed
  resolver selecting the PHYSICAL XTX, both AMDGPU and MIGraphX EPs
  executed 234/290 matrix cases with full node coverage and same-build
  ORT-CPU parity, including YOLO26n end-to-end (5/5 identical detections,
  classes {person, bus} matching bus.jpg), SCRFD det_10g (9 outputs) and
  4 ONNX Model Zoo classifiers. ssd_mobilenet_v1_10: 0 GPU nodes on both
  EPs (whole-graph CPU partition — visible, not silent).
- **R2-B DEVICE IDENTITY: CONDITIONAL_GO_HOST_SPECIFIC.** Physical XTX =
  LUID 72346, proven by DXCore Discrete=1 + sole active DXGI output +
  HIP ordinal0 name/memory match. THREE DXGI mirrors of the 7900 XTX
  exist (R1 knew of two); ORT core's remote-display dedup only removes
  the LAST in high-performance order, so the middle mirror (118045)
  survives and amdgpu-ep consumes its DxgiAdapterNumber=1 as HIP ordinal
  → HIP 101. Windows HIP returns placeholder PCI attributes →
  NOT_EXPOSED. GameViewer↔LUID118045 attribution stays
  GAMEVIEWER_CAUSALITY_UNCONFIRMED. Portability NOT proven.
- **R2-C UPSTREAM PATCH: PATCH_PREPARED_UNBUILT (GUARD_ONLY_NOT_FULL_FIX).**
  Fork Draft PR with guard + pure-C++ helper + 8-scenario tests + design
  docs. No MSVC/SDK/HIP SDK on host: not compiled, not run, not claimed.
- **R2-D DELIVERY: done within AIwork4me.** R1 PR #1 merged under gates
  (merge `7156b9b`); R2 branch + Draft PR; fork Draft PR; zero upstream
  issues/PRs/comments.

## B. Remote state

See `LATEST.md` for exact SHAs/URLs after push verification (main repo PR,
R1 merge commit `7156b9b`, R2 remote HEAD, fork PR
AIwork4me/onnxruntime-ep-amdgpu#1 head `e3dd9af`, CI links). CI is CPU-only
and structurally enforces that no GPU PASS exists without providers+node
attribution+parity+device resolution.

## C. Device identity (summary)

DXGI table (validated probe S1–S6): idx0 XTX LUID 72346 (1 active output,
Discrete=1) = physical; idx1 mirror LUID 118045 (0 outputs, flags=0) — the
HIP-101 candidate; idx2 Intel UHD 770; idx3 mirror LUID 617528 (0 outputs)
— silently deduped by ORT core; idx4 Basic Render Driver (SOFTWARE flag).
PnP: XTX at PCI 3:00.0. All GDI `\\.\DISPLAY*` in this session are
RdpIdd indirect displays (RDP active), hosted on the physical XTX.
HIP PCI attrs are placeholders → identity by evidence conjunction, not
HIP BDF. Resolver + 19 unit tests; every GPU case this run used it.

## D. Compatibility matrix

234/290 PASS_GPU_CORRECT_FULL_COVERAGE (117 per EP on synthetic incl. all
opt levels × batches for multi-output fixtures; #150 by-name compare
strict; no swap detected). 54 SKIPPED_FIXTURE_DEFECT (6 fixtures fail on
pure CPU too — evidence kept). Real models: 4 Zoo + YOLO26n + SCRFD PASS
both EPs; ssd FAIL_NO_GPU_COMPUTE both EPs (DML ran it in round-1 —
per-EP capability gap). OCR BLOCKED_TOOLCHAIN (paddle2onnx native DLL);
SAM/SAM2 BLOCKED_MODEL_ASSET; X-AnyLabeling NOT_TESTED.

## E. Performance & stability

GPU p50/p95 vs CPU p50 (batch 1): YOLO26n 2.73/3.34 vs 9.81 ms (3.46×,
AMDGPU) and 2.73/3.05 vs 10.14 ms (3.63×, MIGraphX); SCRFD 4.04/4.55 vs
22.42 ms (5.47×); mobilenetv2 1.65×; squeezenet 1.78×; tiny matmul 0.04×
(launch overhead, reported honestly). Cold: YOLO26n session-create 41.8 s
(EP compile), first-run 0.03 s; SCRFD first-run 24.3 s; mobilenetv2
first-run 16.2 s. VRAM: NOT_MEASURED; working-set instrumentation broken
→ NOT_MEASURED (no fabrication). Soak: yolo26n × 2 EPs × 15 min, varying
inputs, session recreation, 30 s heartbeats — see SOAK.csv/receipts; no
error-reset events observed in-sample (not claimed as "no leaks").

## F. Upstream patch

Upstream main `15ded64` inspected — no existing fix. Fork PR: Windows-only
guard in GetSupportedDevices (hipGetDeviceCount once; strict parse; skip
invalid candidates with actionable WARNING), header-only pure helper,
standalone tests (DXGI=1+HIP=1 mirror, permutations, duplicate LUIDs,
missing/negative/overflow metadata, two-GPU, HIP=0, unknown-count),
design docs incl. full-fix options (hipDeviceProp_t.luid mapping — the
mgx WSL path already uses it — canary-at-init, per-device allocator maps,
ORT-core dedup fix) and 5 maintainer questions. Why not hardcode 0 /
clamp: both wrong-host failure modes; the guard fails closed per
candidate instead. PATCH_PREPARED_UNBUILT.

## G. Evidence audit

MODEL_PROVENANCE.csv (8 assets: URL/license/date/SHA256; weights
gitignored). manifest.sha256 (1314 entries) self-check clean; built
receipts→redaction(0 hits)→manifest→self-check, manifest not hashed into
itself. Round-1/R1 evidence immutable; failed-then-fixed intermediate
receipts preserved in git history. Per-case isolation: subprocess,
timeout, ledger statuses, NATIVE_CRASH/TIMEOUT classified by supervisor.

## H. Remaining gaps (owner authorizations needed)

P0/P1: (1) disable GameViewer adapter → reboot → re-run identity
supervisor (bounded experiment, plan+rollback written, NOT executed);
(2) install VS2022 + Windows SDK + HIP SDK to build the fork patch and
run its tests natively, then consider upstream contact (still needs
authorization); (3) OCR conversion on a host with working MSVC runtime or
official ONNX asset. P2: SAM export chain; X-AnyLabeling adapter
prototype; fixed working-set/VRAM instrumentation and longer soaks.

## I. Final verdict

**READY_FOR_OWNER_REVIEW (conditional).** Application readiness on this
host is GO with evidence-backed device identity; upstream fix is prepared
but unbuilt; generic Windows compatibility and upstream merge are NOT
claimed. Every GPU PASS satisfies all four mission gates; every gap is
classified from raw evidence — nothing CPU is presented as GPU.
