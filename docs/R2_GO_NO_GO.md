# R2 GO / NO-GO (per module, 2026-11-17, run r2_20261117T004504Z)

CPU CI green does NOT upgrade any hardware verdict. Each row cites its
evidence class.

| module | verdict | basis |
|---|---|---|
| Device identity mapping | **GO (host-specific)** = `CONDITIONAL_GO_HOST_SPECIFIC` | DXGI/LUID/PCI/PnP/HIP joined with self-validating ABI probes (S1–S6); physical XTX = LUID 72346 via Discrete=1 + sole active output + HIP ordinal0 name/mem match; HIP PCI BDF itself NOT_EXPOSED on Windows; GameViewer↔LUID118045 attribution GAMEVIEWER_CAUSALITY_UNCONFIRMED (portability NOT proven — no public LUID→PnP map for output-less mirrors) |
| Fail-closed resolver | **GO** | 19/19 unit tests across mirror/dual-GPU/iGPU/permutation/metadata shapes; used by EVERY GPU case this run (receipts carry device_resolution) |
| Synthetic operator matrix | **GO** | 234/290 cases PASS full coverage (117 per EP incl. 6 defective fixtures excluded as SKIPPED_FIXTURE_DEFECT with CPU-reproducible evidence; split2 defective — remaining 3 multi-output fixtures cover all opt levels × batches) |
| Real models — classification (mnist/squeezenet/mobilenet/emotion) | **GO** | 4 zoo models × 2 EPs, full-coverage GPU PASS + by-name CPU parity |
| Real models — detection YOLO26n | **GO** | official Ultralytics export; tensor parity max_abs 0.0044 (declared 1e-2); 5/5 identical detections GPU vs CPU; classes {person, bus} match bus.jpg; full node coverage, both EPs |
| Real models — multi-output detector SCRFD det_10g | **GO** | 9-output detector, full-coverage PASS both EPs @640; #150 by-name compare strict (all 9 outputs distinct shapes; no swap detected) |
| Real model — ssd_mobilenet_v1_10 | **NO_GO (this model on AMD EPs)** | FAIL_NO_GPU_COMPUTE both EPs (0/5587 nodes on EP, whole-graph CPU partition; visible, not silent; DML ran it in round-1) |
| Performance | **GO (measured)** | YOLO26n p50 2.73 ms vs CPU 9.81 ms (3.5×), SCRFD 5.5×, mobilenetv2 1.65×, squeezenet 1.78×; tiny matmul slower on GPU (launch overhead) reported honestly; cold session-create 41.8 s for YOLO26n (EP compile) |
| Stability soak | see final SOAK.csv | 15 min × 2 EPs yolo26n loop, varying inputs, session lifecycle, heartbeats; verdicts in receipts |
| Upstream patch | **PATCH_PREPARED_UNBUILT** | guard + pure-C++ helper + tests written, NOT compiled (BLOCKED_TOOLCHAIN: no MSVC/SDK/HIP SDK); fork Draft PR AIwork4me/onnxruntime-ep-amdgpu#1; `GUARD_ONLY_NOT_FULL_FIX` |
| OCR (P1) | **BLOCKED_TOOLCHAIN** | paddle2onnx native DLL cannot load; assets + exact error recorded |
| SAM/SAM2 (P1) | **BLOCKED_MODEL_ASSET** | no license-clear official ONNX obtainable this round |
| X-AnyLabeling adapter (P2) | **NOT_TESTED** | deferred; fail-closed resolver + lifecycle API exist for it |
| R1 PR #1 | **MERGED** | merge commit 7156b9b after r1.4 hardening; CI green |

## Aggregate application readiness (R2-A)

**GO for detection-class workloads on this host with explicit device
resolution** (YOLO26n end-to-end + SCRFD + 4 classifiers, both EPs, with
profiler-proven GPU nodes and CPU parity). SSD-class TFLite-exported
graphs remain CPU-partitioned on both AMD EPs. Applications must use the
fail-closed resolver (or equivalent explicit selection); classic
`providers=[...]` remains unsafe (#151).
