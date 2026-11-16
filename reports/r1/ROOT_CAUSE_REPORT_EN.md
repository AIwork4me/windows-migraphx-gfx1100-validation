# R1 Root-Cause Report — Windows gfx1100 HIP 101 invalid device ordinal (English)

- run_id: `r1_20261116T232607Z`; branch `investigation/r1-hip101-root-cause`
  (base = main `c597e4f3a4bd4a98dbcca9ea36d0286ccb8bc65f`, matches remote)
- Evidence: `reports/r1/r1_20261116T232607Z/` (all receipts carry
  schema_version / run_id / UTC / exit codes)
- Host: Windows 11 build 26300, RX 7900 XTX (gfx1100, PCI 1002:744C),
  driver 32.0.21030.2001 (2025-09-25)

## Verdict: ROOT_CAUSE_ISOLATED

**Question**: why does a registered AMDGPU/MIGraphX plugin EP fail at
`hip/allocator.cc:14 hipSetDevice(device_id)` with
`HIP failure 101: invalid device ordinal; GPU=0`?

**Root cause (three independent layers of on-metal evidence)**:

> `amdgpu-ep` (WinML AMD EP package 1.8.65.0) uses the WinML hardware-device
> metadata value **`DxgiAdapterNumber` directly as the HIP device ordinal**
> passed to `hipSetDevice` (source `src/amdgpu/gpu_factory.cc`,
> `GetSupportedDevices`: "Use DxgiAdapterNumber from device metadata as the
> device_id"). This host has 4 display adapters (GameViewer virtual display /
> 7900 XTX / Intel UHD 770 / RDP virtual display), and the AMD EP exposes
> **two** device candidates for the same card: `DxgiAdapterNumber=1`
> (LUID 118045, no Discrete flag) and `DxgiAdapterNumber=0` (LUID 72346,
> Discrete=1). Picking the former calls `hipSetDevice(1)`; both on-host HIP
> runtimes report `hipGetDeviceCount()=1` (only ordinal 0 valid) → **101**.
> Picking the latter calls `hipSetDevice(0)` → success → **real GPU execution
> with parity PASS**.

Round-1 code `runner/ep_runner.py::add_ep_for_devices` always took `devs[0]`
(the Dxgi=1 entry), so every session hit HIP 101. **Driver age is not the
proximate cause.**

## Evidence chain

### 1) Independent HIP runtime probe (pure ctypes; no ORT/WinML)

`hip_probe_M*.json` — 4/4 cells `HIP_SET_OK`:

| DLL | SHA256 | Signature | runtime/driver ver | count | hipSetDevice(0) | Negative controls (1/-1/999) |
|---|---|---|---|---|---|---|
| EP-bundled `amdhip64_7.dll` (load path proven via GetModuleFileNameW) | `714990f5…33d6cc` | AMD, Valid | 71460850 / 71460850 | 1 | **success**; hipDeviceGetName="AMD Radeon RX 7900 XTX", 24GiB | 101 invalid device ordinal |
| System32 `amdhip64_6.dll` | `8fdb9c0d…db5b6c` | Microsoft WHCP, Valid | 60450101 / 60450101 | 1 | success | 101 |

Cleared-env and hipInit(0)-first cells identical. The negative controls
reproduce exactly the code+message the EP hit. The struct-based
`hipGetDeviceProperties` call is recorded `SKIPPED_ABI_UNVERIFIED` per the
mission ABI gate (no version-matched headers on host);
`hipDeviceGetName`/`hipDeviceTotalMem` used instead.

### 2) Per-candidate EP device runs (fresh process per candidate)

`epdev_{AMDGPU,MIGraphX}_*.json`:

| EP | candidate | identity | result |
|---|---|---|---|
| AMDGPU | c0 | DxgiAdapterNumber=1, LUID 118045 | **HIP101** (session built, EP in get_providers, first run fails in allocator) |
| AMDGPU | c1 | DxgiAdapterNumber=0, Discrete=1 | **PASS_AMD_GPU_EXECUTED_AND_PARITY** (profile node provider=AMDGPUExecutionProvider, parity 0.0) |
| MIGraphX | c0 | Dxgi=1 | **HIP101** |
| MIGraphX | c1 | Dxgi=0 | **PASS_AMD_GPU_EXECUTED_AND_PARITY** (provider=MIGraphXExecutionProvider, parity 0.0) |
| both | two devices | — | factory `INVALID_ARGUMENT: only supports selection for a single device`; ORT **auto-falls back to a CPU-only session** (block printed on **stdout**) |

`dll_load_audit.json`: the EP child processes mapped the same
`amdhip64_7.dll` (same MSIX path) inventoried in R1.1.

### 3) Source cross-check (deps/onnxruntime-ep-amdgpu @ 77bba05, read-only)

`gpu_factory.cc GetSupportedDevices` maps `DxgiAdapterNumber` →
`device_index` → `CreateMemoryInfo_V2(...device_index...)`; the allocator's
`SetDevice(device_id)` → `hipSetDevice(device_id)`. The shipped build's
error location `C:\j\amdgpu-ep\src\shared\hip\allocator.cc:14` matches this
function (line drift from the local checkout revision).

## Ruled out

| hypothesis | verdict | evidence |
|---|---|---|
| driver too old (<25.10.13.09) | **ruled out as proximate cause**: raw HIP fully works on this driver; correct candidate → real GPU PASS | R1.1 + R1.2 |
| HIP runtime/driver ABI mismatch | ruled out: version queries succeed; enumeration/properties/memory fine | R1.1 |
| EP not registered | ruled out: registration OK, EP in get_providers | R1.2/R1.3 |
| visibility env vars | ruled out: HIP_*/ROCR_*/CUDA_*/HSA_* all absent (snapshot in receipt) | env_fingerprint.json |
| HIP6/HIP7 mixed loading | ruled out: only the MSIX amdhip64_7.dll in EP processes | dll_load_audit.json |

## Remaining unknowns

1. Physical identity of the LUID 118045 (Dxgi=1) adapter: same PCI ID and
   VRAM as the real card but no Discrete flag — most plausibly the GameViewer
   indirect display adapter wrapping the 7900 XTX; disabling it needs user
   authorization (not done) → `COMPATIBILITY_UNVERIFIED`. Does not affect the
   root-cause conclusion.
2. No authoritative 32.0.21030.2001 ↔ Adrenalin mapping (→
   `COMPATIBILITY_UNVERIFIED` in env_fingerprint.json).
3. Internal mechanism for the registered-MIGraphX classic-path drop
   (phenomenon pinned in the #151 matrix; mechanism not traced).

## Round-1 verdict revision (see ERRATA.md)

AMDGPU/MIGraphX on this host move from "CONDITIONAL NO-GO (driver)" to
**GO with the condition that the DxgiAdapterNumber=0 candidate is selected
explicitly** (device API + `runner/r1/fail_closed.py::pick_device_by_dxgi_adapter`).

## Single most valuable authorized follow-up experiment

**Disable the GameViewer virtual display adapter (Device Manager), reboot,
re-run `runner/r1/ep_device_supervisor.py`.** Read-only, reversible.
Expected: the AMD EP then exposes a single DxgiAdapterNumber=0 candidate →
the old `devs[0]` default hits the working ordinal → HIP 101 disappears.
This turns "virtual adapter triggers the ordinal mismatch" from strong
correlation into causal confirmation and gives upstream a clean repro
condition (multi-adapter host where the AMD GPU is not DXGI adapter 0).
Risk: displays attached through that virtual adapter lose output; rollback
= re-enable the driver.
