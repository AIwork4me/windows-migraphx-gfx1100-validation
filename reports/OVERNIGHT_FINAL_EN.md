# WIN-MIGRAPHX-GFX1100 Overnight Real-Machine Validation — Final Report (EN)

- Window: 2026-11-16 22:54 → 2026-11-17 (UTC+8), physical host (no WSL)
- Repo: https://github.com/AIwork4me/windows-migraphx-gfx1100-validation (public)
- Every claim below is backed by a receipt JSON under `reports/` (command, exit
  code, UTC timestamp, ORT profile, by-name parity). No mock data.

## A. Executive verdict

| Route | Verdict | Basis |
|---|---|---|
| Windows ML `AMDGPUExecutionProvider` | **CONDITIONAL NO-GO (this host's driver)** | Package install + registration + single-device selection all work; the EP's bundled HIP runtime fails `hipSetDevice` with **HIP 101 invalid device ordinal** on driver 32.0.21030.2001 (2025-09-25). Docs require ≥ 25.10.13.09; driver updates were not authorized. |
| Windows ML `MIGraphXExecutionProvider` | **CONDITIONAL NO-GO (same) + Deprecated by Microsoft in WinML 2.x** | Same package (AMD.GPU.EP.Framework 1.8.65.0), same HIP 101 failure. |
| Native ROCm 10.1 Windows build | **BLOCKED_DEPENDENCY (nothing compiled)** | No MSVC/VS2022, no Windows SDK, no HIP SDK; installs need admin elevation (unauthorized). No DLLs fabricated. |
| X-AnyLabeling readiness (Windows AMD EP) | **NO-GO on current driver** | Its classic `providers=[...]` injection silently falls back to CPU with plugin EPs (we reproduced #151 on this machine). Migration plan: docs/X_ANYLABELING_INTEGRATION_GAP.md. |
| Methodology control (DirectML) | **PASS as control — never counted as AMD success** | 52 synthetic operator cases + 8 real-model cases with by-name parity AND profile-attributed GPU compute nodes (mobilenetv2: 300/300 nodes on GPU). |

## B. Repository

Public URL, branch `main`. Final HEAD SHA and remote verification are recorded
at the bottom of this file after the last push. Supervisor checkpoint-commits
every ~30 min; per-task states in `reports/ledger.json` (`supervisor_tasks`).

## C. Physical evidence chain

1. Hardware: `reports/preflight/gpu_pnp_hwid.txt` — `PCI\VEN_1002&DEV_744C`
   (gfx1100, RX 7900 XTX), driver 32.0.21030.2001 (2025-09-25).
2. EP advertisement: catalog C API enumeration lists AMDGPU / MIGraphX /
   OpenVINO / WebGPU for this machine (initially NotPresent).
3. Acquisition: first two `WinMLEpEnsureReady` attempts failed
   (`0x800705B4` timeout; AppX staging `0x80073CF8` "Failed to reach state
   DeQueued", events in `Microsoft-Windows-AppXDeploymentServer/Operational`);
   retry succeeded → `Microsoft.WinML.AMD.GPU.EP.Framework.2 1.8.65.0`
   installed (26 DLLs incl. migraphx.dll, amdhip64_7.dll, MIOpen, hiprtc0714,
   custom_kernels_gfx1151.dll — no gfx1100 prebuilt kernels).
4. Registration: `register_execution_provider_library(name, dll)` OK;
   AMD-vendor devices appear in `get_ep_devices()`.
5. Execution attempt: single-device `add_provider_for_devices([dev], {})` →
   AMD factory runs → `hip\allocator.cc:14 hipSetDevice` → **HIP failure 101**.
   No AMD GPU PASS is claimed anywhere.
6. #151 silent fallback proof: `reports/m5/probe151_AMDGPUExecutionProvider.json`
   (session built in 0.01 s, providers silently CPU-only, inference completes).
7. Multi-device trap: two devices → `only supports selection for a single
   device` → **session-level silent CPU fallback** (verbatim ORT banner kept
   in `reports/m0/m0_migraphx_run4.log`).

## D. Matrix summary (details in reports/support_matrix.csv)

| Phase | AMDGPU EP | MIGraphX EP | DML (control) | CPU |
|---|---|---|---|---|
| M0 minimal graphs ×7 ×b1/2/4 | 21 FAIL (HIP 101) | 21 FAIL (HIP 101) | — | baselines OK |
| M1 operators (34 graphs × b1/2) | 92 BLOCKED*/FAIL(HIP101) | 88 same | **52 PASS_CONTROL_DML** + 12 FAIL (DML graph-load rejections) + 4 FAIL_CORRECTNESS (our log/sqrt feed bug → fixed, documented) | OK |
| M1 multi-output (#150) | FAIL(HIP101)/BLOCKED | same | all by-name correct (same-shape swap detector, 9 outputs, 3 optimization levels) | correct |
| M2 real models (5 official zoo) | 10 FAIL (HIP101) | 10 FAIL | **8 PASS_CONTROL** + 2 FAIL (ssd: UnicodeDecodeError raised inside ORT/DML `sess.run()` — upstream defect evidence) | OK |
| M3 perf | not measurable | not measurable | conv_relu b1 DML p50 0.086 ms vs CPU 0.027 ms (micro-graph overhead; no inflated speedups) | p50 0.005–0.027 ms |
| M3 soak | — | — | 30-min session churn + repeated inference (receipt on disk) | — |

*BLOCKED = runs whose process had not called EnsureReady (EP not registered
in-process); later runs auto-EnsureReady and reach the deeper FAIL (HIP 101).
Both are kept, honestly labeled.

- Fake-GPU (CPU-only masquerading) risk: **none** — every GPU PASS required
  profile-verified `gpu_nodes ≥ 1` plus by-name parity.
- #150 on MIGraphX backend: unverifiable tonight (HIP gate) — honest gap.

## E. Performance / stability

Only measured numbers reported (see D and `reports/performance.csv`; every
timing row carries sample count and rounds). Zero native crashes / TDR events
all night (preflight: no TDR in past 14 days either). The transient failures
were: WU download timeouts (network), immediate HIP 101 failures (no hangs),
and our own ledger self-deadlock bug (fixed, documented).

## F. Native build (M4)

**BLOCKED_DEPENDENCY — nothing compiled.** First concrete blockers, in order:
(1) `cl.exe` absent, no VS2022 / vswhere; Build Tools silent install needs
admin (unauthorized). (2) No Windows SDK. (3) No HIP SDK
(`C:\Program Files\AMD\ROCm` absent; the System32 `amdhip64_6.dll` is a
driver-shipped runtime with no headers/import lib and cannot pose as an SDK).
Upstreams pinned: ep-amdgpu@77bba05, AMDMIGraphX@d34c354,
onnxruntime@368a672. Minimal path forward: authorize VS Build Tools + HIP SDK
10.1, then upstream `build.bat --use_migraphx` Release build.

## G. Next steps (upstream opportunities — nothing submitted)

1. ep-amdgpu: pre-flight `hipGetDeviceCount` + explicit driver-version error
   in EP factory (P1, best evidence).
2. onnxruntime plugin bindings: make multi-device registration failure a
   catchable error instead of silent CPU fallback (P2, #151 family).
3. ep-amdgpu README: document consuming the Windows ML EP package (P3, #164).
   PRs require user authorization.

## H. Reproduction

`docs/REPRODUCE_WINDOWS_GFX1100.md`; minimal reproducers for the two headline
failures: `scripts/reproduce_failure.ps1`.

---
*Redaction + manifest generation by `runner/phases/wrapup.py`; remote HEAD
verification appended below after final push.*
