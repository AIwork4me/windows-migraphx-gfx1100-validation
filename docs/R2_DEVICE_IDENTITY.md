# R2 Device Identity — Windows gfx1100 host (RX 7900 XTX)

Evidence: `reports/r2/r2_20261117T004504Z/device_identity/` (run of
2026-11-17, all steps fresh subprocesses, non-destructive host audit).
Source code behind the behavior: `deps/onnxruntime` @ core
`onnxruntime/core/platform/windows/device_discovery.cc` and
`deps/onnxruntime-ep-amdgpu` @ `src/amdgpu/gpu_factory.cc` (R1 checkout).

## 1. The four namespaces (never interchangeable)

| namespace | what it is | stability on this host |
|---|---|---|
| DXGI adapter index | `EnumAdapters1` order; becomes ORT metadata `DxgiAdapterNumber` | changes when indirect adapters appear/disappear (RDP connect, virtual display drivers) |
| DXGI LUID | per-adapter local unique id (ORT metadata `LUID`, uint64 key) | stable within one boot |
| PCI BDF | physical location from PnP `DEVPKEY_Device_LocationInfo` | stable forever (slot) |
| HIP ordinal | index into `hipGetDeviceCount()` of ONE runtime DLL | per-runtime, per-process |

## 2. Measured identity map (this boot, validated)

| DXGI idx | description | LUID | flags | outputs (active) | DXCore Discrete | in ORT ep list | identity verdict |
|---|---|---|---|---|---|---|---|
| 0 | AMD Radeon RX 7900 XTX | **72346** | 0 | 1 (**yes**, `\\.\DISPLAY1`) | **1** | yes (AMDGPU+MIGraphX+DML) | **physical XTX** (see basis) |
| 1 | AMD Radeon RX 7900 XTX | 118045 | 0 | 0 | absent | yes → `hipSetDevice(1)`→HIP 101 | indirect-display mirror |
| 2 | Intel UHD 770 | 80099 | 0 | 0 | 0 (iGPU) | yes (DML only) | iGPU |
| 3 | AMD Radeon RX 7900 XTX | 617528 | 0 | 0 | absent | **no** (deduped) | indirect-display mirror |
| 4 | Microsoft Basic Render Driver | 79992 | 2 (SOFTWARE) | 0 | — | no (flag filter) | CPU fallback adapter |

- PnP: XTX = `PCI\VEN_1002&DEV_744C&SUBSYS_0E3B1002` at **bus 3, device 0,
  function 0**; Intel UHD 770 = bus 0, device 2, function 0.
- All three 1002:744c DXGI entries report byte-identical VRAM
  (25 753 026 560 B = 24560 MB) — mirrors copy the real card's identity.
- **New R2 finding vs R1:** there are THREE 1002:744c DXGI adapters, not
  two. The third (LUID 617528) is silently dropped by ORT core's dedup
  (below), which is why R1's ep-device list showed only two.

## 3. Why the mirrors survive ORT's filters (source-verified)

ORT core `GetDeviceInfoD3D12` (device_discovery.cc):

1. EnumAdapters1 loop skips `DXGI_ADAPTER_FLAG_SOFTWARE/REMOTE` and
   1414:008c. The two 1002:744c mirrors set **flags=0** → not filtered.
2. A second pass walks `EnumAdapterByGpuPreference(HIGH_PERFORMANCE)`
   order and erases an adapter **only if it is the LAST one** AND
   `have_remote_display_adapter` (an `RdpIdd_IndirectDisplay` hardware id
   was seen by the SetupAPI pass) AND it has no outputs AND its
   vendor:device was seen under a different LUID.
   → LUID 617528 (hp-order last) is erased; **LUID 118045 (hp index 1)
   escapes because the heuristic only inspects the last position.**
3. `DxgiAdapterNumber` is written as the raw EnumAdapters1 index — an
   **enumeration position, not a device identity**.

amdgpu-ep `gpu_factory.cc::GetSupportedDevices` then does
`device_index = atoi(DxgiAdapterNumber)` → `CreateMemoryInfo_V2(...,
device_index, ...)` → the memory-info device id is used as the HIP
ordinal by the allocator/HIP backend → mirror candidate becomes
`hipSetDevice(1)` while `hipGetDeviceCount()==1` → **HIP 101**.

## 4. Physical identity basis for LUID 72346

Windows HIP (both `amdhip64_7.dll` EP copy and System32 `amdhip64_6.dll`)
returns **placeholder PCI attributes** (`PciBusId=0, PciDeviceId=1`,
`PciDomainId=64` vs `2` across the two runtimes — real domain is 0), so
HIP-side BDF is `NOT_EXPOSED` on Windows. Identity is instead proven by
the conjunction, each leg independently sourced:

1. **DXCore metadata `Discrete=1`** appears only on LUID 72346 among the
   AMD entries (mirrors lack the key).
2. **Sole active DXGI output**: only LUID 72346 has an attached output
   (`\\.\DISPLAY1`); mirrors have zero outputs. GDI shows every
   `\\.\DISPLAY*` device in this session is an `RdpIdd_IndirectDisplay` —
   the interactive desktop itself is hosted through the physical XTX.
3. **HIP ordinal 0** of every HIP runtime on the host reports name
   "AMD Radeon RX 7900 XTX" / gfx1100 and total memory exactly equal to
   the DXGI DedicatedVideoMemory, and `hipSetDevice(0)` succeeds while
   ordinals ≥1 return 101 (R1 receipts + R2 re-run).

The fail-closed resolver (`runner/r2/device_resolver.py`) encodes exactly
this: candidates joined by LUID, physical evidence = Discrete+active
output, an injected isolated canary must prove execution, and ambiguity or
absence of proof raises `AmbiguousGpuError`/`GpuUnavailableError` instead
of guessing. 19 unit tests cover mirror/dual-GPU/iGPU/permuted-order/
missing-metadata/duplicate-LUID shapes.

## 5. GameViewer causality — honestly labeled

Two indirect display adapters exist (PnP `ROOT\DISPLAY\0000` GameViewer
Virtual Display Adapter; `SWD\REMOTEDISPLAYENUM\RDPIDD_INDIRECTDISPLAY`
session 2 — an RDP session is active). The two zero-output mirrors (LUID
118045, 617528) plausibly correspond to these two adapters (creation
order suggests 118045=GameViewer, 617528=RDP), but **no available public
API maps LUID→PnP instance for output-less adapters**, so:

    GAMEVIEWER_CAUSALITY_UNCONFIRMED

The bounded A/B experiment that would settle it (disable GameViewer
adapter, reboot, re-run) requires user authorization — see
`docs/EXPERIMENT_REQUIRES_AUTHORIZATION.md`. It does NOT affect the R2
conclusions: whatever owns LUID 118045, consuming `DxgiAdapterNumber` as
a HIP ordinal is wrong, and LUID 72346 is proven to be the physical card
by evidence that does not depend on the mirror's owner.

## 6. Portability statement

Everything above is **host- and boot-specific**. The portable statement is
the algorithm, not any number: applications must resolve via
LUID-joined physical evidence + execution canary (or an explicit user
choice), never `DxgiAdapterNumber=0`, never "first device that works"
(first-canary-pass proves availability, not the intended physical card).
