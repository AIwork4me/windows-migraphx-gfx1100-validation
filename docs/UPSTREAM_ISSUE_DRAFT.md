# ISSUE DRAFT — do not post without maintainer confirmation of the repro on other hardware
# (This file is a draft kept in the fork. It has NOT been filed upstream.)

**Title:** Windows: DxgiAdapterNumber consumed as HIP ordinal → hipErrorInvalidDevice (101) on hosts with indirect display mirror adapters

## Environment

- OS: Windows 11 (desktop experience, RDP session active)
- GPU: single AMD Radeon RX 7900 XTX (gfx1100)
- EP: onnxruntime-ep-amdgpu (amdgpu-ep / MIGraphXExecutionProvider plugin EP), current `main` at time of writing
- A third-party virtual display driver is installed (indirect display adapter), plus the RDP session's own indirect display
- ORT: 1.2x plugin-EP API, automatic EP/device selection (WinML-style device catalog)

## Summary

`ProviderFactory::GetSupportedDevices` (`src/amdgpu/gpu_factory.cc`) reads the
`DxgiAdapterNumber` device metadata and passes `std::atoi` of it as the
memory-info device id. Downstream, `mgx_factory.cc`'s `CreateAllocator` takes
that id via `memory_info.GetDeviceId()` and hands it to `hip::Allocator`,
whose `SetDevice` calls `hipSetDevice(device_id)`
(`src/shared/hip/allocator.cc`). `DxgiAdapterNumber` is a DXGI enumeration
position, not a HIP ordinal. On hosts where DXGI enumerates zero-output
"mirror" adapters for the same physical GPU (indirect display drivers:
virtual display tools, RDP indirect display), the mirror's DXGI index can be
`>= hipGetDeviceCount()`, and selecting that ep_device fails with HIP error
101 (`hipErrorInvalidDevice`).

## Repro (minimal)

1. On Windows, install any indirect display driver (virtual display adapter)
   and/or connect via RDP so that `EnumAdapters1` lists extra zero-output
   adapters mirroring the AMD GPU's vendor:device:subsystem (1002:744c),
   with the real card not last in enumeration order.
2. Register the amdgpu plugin EP and enumerate its devices (automatic
   selection as done by the WinML device catalog / ORT ep device listing).
3. Create a session targeting the ep_device whose `DxgiAdapterNumber` is the
   mirror's index (or let automatic selection pick it when it sorts first).

### Expected

Every ep_device advertised by `GetSupportedDevices` is usable; an adapter
that cannot be mapped to a valid HIP ordinal is not advertised (or is
reported as incompatible with a reason).

### Actual

The mirror adapter is advertised as a normal AMD GPU device; using it fails
with `hipSetDevice(n) → hipErrorInvalidDevice` (101) because
`hipGetDeviceCount() == 1` while the mirror's `DxgiAdapterNumber == 1`.

## Evidence (single-GPU host, one boot)

| DXGI idx | LUID | outputs | DXCore Discrete | role | EP result |
|---|---|---|---|---|---|
| 0 | 72346 | 1 (active) | 1 | physical RX 7900 XTX | OK (ordinal 0 coincides) |
| 1 | 118045 | 0 | absent | indirect display mirror | advertised → HIP 101 |
| 2 | 80099 | 0 | 0 | Intel iGPU | vendor-filtered |
| 3 | 617528 | 0 | absent | second mirror | erased by ORT core dedup |
| 4 | 79992 | 0 | — | software adapter | flag-filtered |

- `hipGetDeviceCount() == 1`; `hipSetDevice(0)` OK; `hipSetDevice(1)` → 101.
  Verified with both the EP's `amdhip64_7.dll` and the system
  `amdhip64_6.dll`.
- All three 1002:744c DXGI entries report byte-identical VRAM — the mirrors
  copy the real card's identity, so vendor:device filtering cannot separate
  them; only LUID (+ output / DXCore evidence) can.
- ORT core's indirect-adapter dedup (onnxruntime `device_discovery.cc`)
  erases only the LAST adapter in `EnumAdapterByGpuPreference` order, so the
  mirror at position 1 escapes and reaches the EP.

## Why the obvious fixes are wrong

- Hardcoding ordinal 0: single-GPU-only; loses the selected device's identity
  on multi-GPU hosts.
- Clamping to `hipGetDeviceCount() - 1`: turns a wrong ordinal into a
  silently different wrong ordinal (the mirror aliases the real card and all
  error signal disappears).

## Suggested direction

Fail closed per candidate: query `hipGetDeviceCount()` in
`GetSupportedDevices`, validate `DxgiAdapterNumber` (strictly parsed, in
range) before creating the memory info, skip + warn (LUID, adapter number,
HIP count, recommended action) otherwise. Longer term, map LUID → ordinal
(the MIGraphX backend already compares ORT `LUID` metadata against
`hipGetDeviceProperties().luid` on Windows) instead of consuming a DXGI
position. A guard patch and design notes exist in this fork
(`UPSTREAM_FIX_DESIGN.md`, branch `fix/windows-dxgi-hip-device-identity`);
maintainer guidance wanted on the LUID mapping question before a real PR is
proposed upstream.
