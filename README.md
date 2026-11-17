# windows-migraphx-gfx1100-validation

> **R2.1.1 correction (2026-11-17, run `r2_1_1_20261117T114559Z`) — SSD
> causality corrected:** R2.1's "an opset-10 Loop IS the barrier" is
> **REVOKED** — a contract-valid carried-only static Loop **PASSES on
> both AMD EPs** on the real GPU. The full-SSD whole-graph-CPU outcome is
> re-attributed to **NonZero's dynamic-shape handling inside the NMS Loop**
> (SSD places all 180 NonZero ops inside its first Loop body; MIGraphX
> `check_dynamic: nonzero` reproduced) plus a separate EP-internal
> Conv→Loop parse stride defect. New defect classes isolated on both EPs:
> `LOOP_SCAN_OUTPUT_INCORRECT` (silent wrong scan values, carried output
> exact), `NONZERO_FIXED_COUNT_INCORRECT` (fixed-size output regardless
> of true count), `CONV_TO_LOOP_PARSE_STRIDE_FAIL`. Evidence:
> `reports/r2_1_1/r2_1_1_20261117T114559Z/` (28-case matrix, CPU oracle
> 19/19, SSD full-graph re-run). `reports/r1/LATEST.md` /
> `reports/r2/LATEST.md` record the SHAs **as published at their run
> time**, not the current HEAD.

> **R2 verdict (2026-11-17, run `r2_20261117T004504Z`) — READ THIS FIRST:**
> - **R2 supersedes the overnight CONDITIONAL NO-GO below.** With the
>   fail-closed device resolver selecting the PHYSICAL XTX (LUID 72346,
>   proven via DXCore Discrete=1 + sole active DXGI output + HIP ordinal0
>   name/mem match), **AMDGPU EP and MIGraphX EP both execute real models
>   on the real GPU**: 234/290 matrix cases PASS with full node coverage
>   and same-build ORT-CPU parity, including **YOLO26n end-to-end**
>   (5/5 identical detections vs CPU on bus.jpg, classes {person, bus}),
>   **SCRFD det_10g (9 outputs)** and 4 ONNX Model Zoo classifiers.
>   Measured speedups: YOLO26n 3.5x, SCRFD 5.5x (p50, batch 1).
> - **What is still NOT proven:** this is one host's official WinML EP
>   MSIX package -- NOT generic Windows compatibility, NOT an upstream
>   merge, NOT a native ROCm build. Upstream guard patch status per R2.1:
>   GUARD_HELPER_COMPILED_TESTED (g++/clang++/MSVC in CI); native
>   full-EP build still BLOCKED_TOOLCHAIN (no MSVC/HIP SDK on host).
> - Key evidence: `reports/r2/r2_20261117T004504Z/`, summaries:
>   `docs/R2_GO_NO_GO.md`, `docs/R2_DEVICE_IDENTITY.md`,
>   `reports/r2/R2_FINAL_CN.md` / `_EN.md`.


Real-machine validation of AMD MIGraphX / AMDGPU Execution Providers on
**native Windows 11 (build 26300) + Radeon RX 7900 XTX (gfx1100, DEV_744C)**,
against Windows ML plugin EPs, ONNX Runtime CPU parity, multi-output
correctness (upstream #150), silent CPU fallback (#151), performance,
stability, and the feasibility of a native ROCm 10.1 Windows build.

> **Executive verdict (2026-11-17 overnight run):**
> - **Windows ML AMDGPU EP / MIGraphX EP: CONDITIONAL NO-GO on this host.**
>   The full chain works — catalog advertises both EPs, the official EP package
>   (`Microsoft.WinML.AMD.GPU.EP.Framework.2` 1.8.65.0) downloads and installs,
>   `register_execution_provider_library()` registers devices, single-device
>   `add_provider_for_devices()` reaches AMD's own code — but the EP's bundled
>   HIP runtime fails `hipSetDevice` with **HIP error 101 (invalid device
>   ordinal)** on driver 32.0.21030.2001 (2025-09-25). Docs require driver
>   ≥ 25.10.13.09 for MIGraphX EP; driver updates were out of scope (not
>   authorized). No fake GPU PASS is claimed anywhere.
> - **#151 confirmed**: classic `providers=["AMDGPUExecutionProvider", ...]`
>   silently drops the unregistered plugin EP — session builds in 0.01 s,
>   `get_providers()` returns CPU only, inference completes. Evidence:
>   `reports/m5/probe151_*.json`.
> - **Methodology validated on real GPU via DirectML control**: 52 synthetic
>   operator cases + 8 real-model cases PASS with by-name parity AND
>   profile-attributed GPU compute nodes (never counted as AMD success).
> - **Native ROCm 10.1 build: BLOCKED_DEPENDENCY** (no MSVC/VS2022, no Windows
>   SDK, no HIP SDK; installs need admin elevation which was not authorized).
>   Upstream SHAs pinned; see `reports/m4/`.

Everything above is backed by receipts under `reports/` captured on the
physical machine (commands, exit codes, UTC timestamps, profile JSONs).
No mock data; no upstream PRs sent.

## Quick smoke (clean Windows gfx1100 box)

```powershell
git clone https://github.com/AIwork4me/windows-migraphx-gfx1100-validation.git
cd windows-migraphx-gfx1100-validation
powershell -ExecutionPolicy Bypass -File scripts\collect_env.ps1    # preflight
powershell -ExecutionPolicy Bypass -File scripts\setup-winml.ps1    # venvs
venv-winml\Scripts\python.exe runner\phases\m0_ep_discovery.py --repo . --ep AMDGPUExecutionProvider --download yes
```

Full reproduction (including both key failures): `docs/REPRODUCE_WINDOWS_GFX1100.md`.
Status board: `reports/STATUS.md`. Final reports: `reports/OVERNIGHT_FINAL_CN.md` / `_EN.md`.

## Layout

- `scripts/` — PowerShell entry points (setup, run_overnight, resume, status, reproduce_failure)
- `runner/` — supervisor + per-phase executors + shared EP runner
- `fixtures/` — 34 original synthetic ONNX graphs (MIT), sha256 manifest
- `reports/` — immutable evidence receipts per milestone + ledger.json
- `docs/` — provenance, hardware matrix, limitations, redaction, upstream PR candidates

License: MIT for original code (LICENSE). Third-party components remain under
their own licenses (docs/PROVENANCE.md).
