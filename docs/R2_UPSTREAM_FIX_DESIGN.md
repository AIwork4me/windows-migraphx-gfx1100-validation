# Windows AMD GPU device identity: DxgiAdapterNumber is not a HIP ordinal

Status: design + minimal guard patch (`fix/windows-dxgi-hip-device-identity`).
Patch status: **PATCH_PREPARED_UNBUILT** — authored on a host without
MSVC/clang, the Windows SDK, or a HIP SDK, so nothing in this branch has been
compiled or executed. All claims below about current behavior are from source
reading and from a host audit of a stock Windows 11 + RX 7900 XTX machine.

## 1. Summary

On Windows, ORT core discovers GPUs through DXGI and hands each adapter to
plugin EP factories with metadata `DxgiAdapterNumber`, the adapter's position
in `IDXGIFactory1::EnumAdapters1` order. `src/amdgpu/gpu_factory.cc`
(`ProviderFactory::GetSupportedDevices`) copies that number, via
`std::atoi`, straight into the memory-info device id:

```cpp
int device_index = i;
for (...) if (strcmp(keys[idx], "DxgiAdapterNumber") == 0)
    device_index = std::atoi(values[idx]);
...
ort_api.CreateMemoryInfo_V2("default", OrtMemoryInfoDeviceType_GPU,
    amd::VendorId, device_index, ...);
```

That device id is later consumed as a **HIP ordinal**: the backends'
`CreateAllocator` extracts it with `memory_info.GetDeviceId()`
(`src/migraphx/mgx_factory.cc`) and passes it to `hip::Allocator`, whose
`SetDevice` calls `hipSetDevice(device_id)` (`src/shared/hip/allocator.cc`).
A DXGI enumeration position is not a HIP ordinal. When they disagree, session
creation dies with `hipErrorInvalidDevice` (HIP 101).

## 2. The four namespaces (never interchangeable)

| namespace | what it is | stability |
|---|---|---|
| DXGI adapter index | position in `EnumAdapters1` order; becomes ORT metadata `DxgiAdapterNumber` | changes whenever indirect adapters appear/disappear (RDP connect, virtual display drivers) |
| DXGI LUID | per-adapter local unique id (ORT metadata `LUID`) | stable within one boot |
| PCI BDF | physical slot location | stable forever; **not exposed by HIP on Windows** (placeholder attributes observed: bus 0, device 1, inconsistent domain) |
| HIP ordinal | index into `hipGetDeviceCount()` of one runtime DLL | per-runtime, per-process |

Measured on the validation host (single RX 7900 XTX, Windows 11, RDP session
active, third-party virtual display driver installed):

| DXGI idx | description | LUID | outputs | DXCore `Discrete` | result today |
|---|---|---|---|---|---|
| 0 | AMD Radeon RX 7900 XTX (physical) | 72346 | 1 active (`\\.\DISPLAY1`) | 1 | registers, works (ordinal 0 happens to be right) |
| 1 | AMD Radeon RX 7900 XTX (mirror) | 118045 | 0 | absent | registers → `hipSetDevice(1)` → **HIP 101** (`hipGetDeviceCount()==1`) |
| 2 | Intel UHD 770 | 80099 | 0 | 0 (iGPU) | not AMD; skipped by vendor filter |
| 3 | AMD Radeon RX 7900 XTX (mirror) | 617528 | 0 | absent | dropped by ORT core dedup (see below) |
| 4 | Microsoft Basic Render Driver | 79992 | 0 | — | SOFTWARE flag; filtered |

The two mirror adapters are zero-output DXGI adapters that copy the real
card's vendor:device:subsystem (1002:744c) and VRAM. They are created by
indirect display drivers (an RDP indirect display session and a virtual
display driver were both present). Which driver owns which mirror LUID could
not be confirmed — no public API maps LUID → PnP instance for output-less
adapters — but the fix does not depend on the answer.

## 3. Why the mirrors survive ORT core's filters

ORT core `GetDeviceInfoD3D12` (onnxruntime `device_discovery.cc`):

1. The `EnumAdapters1` loop skips `DXGI_ADAPTER_FLAG_SOFTWARE/REMOTE`. The
   mirrors set flags = 0 → not filtered.
2. A second pass walks `EnumAdapterByGpuPreference(HIGH_PERFORMANCE)` order
   and erases an adapter only if it is the **last** one AND an
   `RdpIdd_IndirectDisplay` hardware id was seen AND it has no outputs AND its
   vendor:device appeared under a different LUID. LUID 617528 (hp-order last)
   is erased; **LUID 118045 at hp-order index 1 escapes** because the
   heuristic only inspects the last position.
3. `DxgiAdapterNumber` is written as the raw `EnumAdapters1` index — an
   enumeration position, not a device identity.

So the EP receives two indistinguishable-looking AMD GPU candidates for one
physical card, one of which carries an ordinal that cannot work.

## 4. Why the obvious "fixes" are wrong

- **Hardcode device 0**: works only for single-GPU hosts; silently targets
  the wrong card on multi-GPU hosts (HIP ordinal 0 is not necessarily the
  adapter the user selected; first-device-that-works proves availability, not
  identity). Also breaks the moment the EP must honor ORT's device selection.
- **Clamp to `hipGetDeviceCount() - 1`**: converts a wrong ordinal into a
  *different* wrong ordinal. On the mirror host, DXGI 1 clamps to 0, and the
  mirror's ep_device silently aliases the real card — all error signal is
  lost, session creation appears to succeed while device selection is
  meaningless. A mapping failure must be reported, not papered over.
- **Dedup by vendor:device only**: the mirrors share the real card's
  vendor:device:subsys; only LUID (+ physical evidence) separates them.

## 5. What this patch does (guard, not full fix)

`ProviderFactory::GetSupportedDevices` on Windows now:

1. Queries `hipGetDeviceCount()` once at the top (the amdgpu-ep target
   already links `hip-shared`; `gpu_ep.cc` in the same DLL already calls
   `hipGetDeviceProperties` for backend routing; the `/DELAYLOAD` of
   `amdhip64_7.dll` exists only to keep ORT's provider *probe* path HIP-free,
   and `GetSupportedDevices` is never on that probe path).
2. For each AMD GPU candidate, reads `DxgiAdapterNumber` and `LUID` metadata
   and runs the pure decision function from
   `src/amdgpu/device_identity_guard.h`:
   - metadata missing → skip candidate (fail closed; no ordinal can be
     derived without guessing — the legacy `i`-position fallback is exactly
     the namespace conflation under repair);
   - not a canonical non-negative decimal int (empty, signed, padded,
     trailing garbage, overflow) → skip; `std::atoi`'s silent truncation is
     part of the original bug;
   - `>= hipGetDeviceCount()` (or HIP count 0) → skip: cannot be a valid
     ordinal, would fail with HIP 101;
   - otherwise accept with the parsed value as the ordinal.
3. Every skip logs a WARNING through the factory's `OrtLogger` naming the
   LUID, the raw `DxgiAdapterNumber`, the HIP count, the reason, and the
   recommended user action (disable/remove the indirect display driver that
   mirrors the GPU, or select the physical GPU explicitly).
4. Failure is per-candidate: remaining valid candidates still register.
   If `hipGetDeviceCount()` itself fails, the count is treated as unknown,
   a warning is logged, and only the metadata checks run (fail-open on HIP
   unavailability — see open questions — because this umbrella factory also
   serves DirectML-profile routing where a broken HIP runtime must not take
   down DML device registration).
5. Non-Windows builds keep the exact legacy behavior (position fallback +
   `std::atoi`); Linux device discovery has no DXGI metadata, and changing
   Linux semantics belongs with the full fix, not the guard.

The decision logic lives in a header of pure functions over injected views
(`{luid, dxgi_adapter_number}` candidates + a HIP count) so it is testable
without Windows/HIP/ORT:
`tests/device_identity_guard_test.cc` (standalone, C++11, no repo build
system; **PATCH_PREPARED_UNBUILT**, see file header). The batch function
`EvaluateAll` additionally deduplicates candidates by LUID (first accepted
candidate per LUID wins; distinct-LUID mirrors that pass the range check on
multi-GPU hosts are the case it protects). The factory currently wires the
per-candidate `Evaluate` only, to keep the runtime diff minimal; switching
the loop to `EvaluateAll` is a mechanical follow-up if maintainers want
LUID-level dedup at registration time.

Known and intentionally not fixed in this patch (marked with a
`TODO(multi-device)` in `gpu_factory.cc`): `gpu_memory_info_` /
`pinned_memory_info_` are single slots overwritten per matched device. With
more than one accepted candidate the earlier `OrtMemoryInfo*` is leaked
(overwritten without release) while the earlier ep_device still references
it, and the factory-level `gpu_allocator_` / `pinned_allocator_` wrappers can
only represent one device. The upstream comment already calls this
single-GPU-acceptable; the guard makes the surviving set well-defined but
does not introduce per-device maps.

## 6. Full-fix options (post-guard, need maintainer input)

- **A. LUID → ordinal resolution inside the umbrella factory.**
  `hipGetDeviceProperties(ordinal).luid` is populated on Windows; the
  MIGraphX backend factory already relies on this
  (`src/migraphx/mgx_factory.cc` compares the ORT `LUID` metadata against
  `device_prop.luid` and only registers matching devices). The umbrella
  `gpu_factory.cc` links `hip-shared` too, so it could enumerate HIP devices,
  match each ORT candidate's LUID, and derive the true ordinal per candidate —
  no DXGI position involved. This is the natural completion of the guard and
  would also fix multi-GPU selection. Open question: is
  `hipDeviceProp_t::luid` contractual on Windows across ROCm versions, or an
  implementation detail the MIGraphX backend just happens to rely on?
- **B. Per-device memory info + allocator maps.** Replace the single
  `gpu_memory_info_`/`pinned_memory_info_` slots with per-ordinal maps (the
  `gpu_memory_infos_`/`pinned_memory_infos_` + `MemoryInfoPtr` pattern in
  `src/migraphx/mgx_factory.h`) so multiple accepted candidates each keep a
  valid, releasable memory info. Required before option A is meaningful on
  multi-GPU hosts.
- **C. Canary-at-init.** After mapping a candidate to an ordinal, prove the
  ordinal executable (e.g. allocate/free a token buffer or run a null kernel
  via the backend) before advertising the ep_device. Catches every
  "in-range but wrong/unusable ordinal" class (mirrors on multi-GPU hosts,
  driver-refused devices). Cost: real GPU work at registration time; needs a
  maintainers' call on whether that is acceptable in `GetSupportedDevices`.
- **D. ORT core: identity metadata and dedup.** The platform layer could
  (1) dedup indirect display mirrors by LUID/vendor:device instead of only
  inspecting the last hp-order adapter, and/or (2) publish a stable,
  documented adapter identity (LUID as uint64 key already exists as `LUID`)
  that EPs are told to use for device mapping instead of
  `DxgiAdapterNumber`. That is an onnxruntime/onnxruntime change; from this
  repo the actionable half is to stop treating `DxgiAdapterNumber` as an
  identity.
- **E. Metadata escape hatch.** Publish, on each ep_device, which ordinal it
  mapped to and why (e.g. ep metadata `hip_ordinal`, `identity_luid`), so
  applications can diagnose mirror-adapter hosts without ORT logs.

## 7. NEEDS_UPSTREAM_API_CLARIFICATION

1. Is there a supported Windows mapping from a DXGI LUID to a HIP ordinal
   (i.e. is `hipDeviceProp_t::luid` contractual), or is the MIGraphX
   backend's LUID comparison the only blessed pattern?
2. Is `DxgiAdapterNumber` intended by ORT core to be consumed as a device id
   by EPs, or is it informational (for DX12/DML adapter selection only)?
   The amdgpu umbrella uses it as both a DML adapter index and a HIP ordinal
   through one shared `OrtMemoryInfo` — the two backends disagree about what
   the number means.
3. When `GetSupportedDevices` cannot safely map a candidate, is
   skip-and-warn (this patch) the preferred contract, or should
   `GetHardwareDeviceIncompatibilityDetails` be used to surface the reason?
4. For the DirectML profile: what device id should the umbrella's memory
   infos carry when the DML backend is selected — DXGI index (today) or
   something backend-neutral? This determines whether the HIP-count guard
   could ever skip a DML-usable adapter (theoretical today: any AMD DXGI
   adapter that DML can use but HIP cannot enumerate).
5. Should `hipGetDeviceCount()` failing inside `GetSupportedDevices` be
   fail-open (this patch: warn, validate metadata only) or fail-closed
   (register nothing)? Fail-closed would take down DML-profile registration
   on hosts with a broken HIP runtime, which is why fail-open was chosen.

## 8. Reproduction receipts (summary)

- Host: Windows 11, one RX 7900 XTX (gfx1100), RDP session active, a
  third-party virtual display driver installed.
- `IDXGIFactory1::EnumAdapters1`: five adapters; three report
  1002:744c with identical VRAM (see table in section 2). Only DXGI 0 has an
  active output; DXCore reports Discrete = 1 only for LUID 72346.
- `hipGetDeviceCount() == 1`; `hipSetDevice(0)` succeeds; ordinals ≥ 1
  return 101 (re-verified across two HIP runtimes, the EP's `amdhip64_7.dll`
  and the system `amdhip64_6.dll`).
- With the AMDGPU/MIGraphX plugin EP registered under automatic device
  selection, the EP's device list contains both AMD candidates (LUID 72346
  at DXGI 0 and LUID 118045 at DXGI 1); selecting the DXGI 1 ep_device
  produces `hipErrorInvalidDevice` (101) at session creation / first
  allocation.
- HIP on this host returns placeholder PCI attributes (bus 0, device 1,
  runtime-dependent domain), so HIP-side BDF cannot be used to identify the
  physical card; LUID + output/DXCore evidence is the reliable basis.

## 9. Portability statement

Everything measured is host- and boot-specific. The portable claim is the
algorithm: map devices by stable identity (LUID) joined with physical
evidence and an execution canary, never by DXGI enumeration position, never
"first device that works", never a clamped ordinal. The guard implements
only the fail-closed half of that statement; the identity-joined mapping is
left to the full fix (section 6).
