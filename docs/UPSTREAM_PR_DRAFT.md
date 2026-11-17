# PR DRAFT (for the fork-internal draft PR; NOT to be opened against onnxruntime/onnxruntime-ep-amdgpu without maintainer contact first)

**Title (exact):** R2: Windows AMD EP device identity mapping (draft for review)

**Base:** fork `main` (same repository) — this PR stays inside the fork.

---

## PR body

### Summary

On Windows hosts with indirect display adapters (virtual display drivers,
RDP indirect display), DXGI enumerates zero-output "mirror" adapters that
copy the physical AMD GPU's vendor:device:subsystem and survive ORT core's
platform-level dedup. `ProviderFactory::GetSupportedDevices`
(`src/amdgpu/gpu_factory.cc`) passes `std::atoi(DxgiAdapterNumber)` — a DXGI
enumeration position — into the memory-info device id, which the backends
consume as a HIP ordinal (`memory_info.GetDeviceId()` →
`hip::Allocator::SetDevice` → `hipSetDevice`). When the mirror's DXGI index
is `>= hipGetDeviceCount()` (e.g. DXGI 1 with a single HIP device), session
creation fails with `hipErrorInvalidDevice` (HIP 101).

This PR adds a fail-closed per-candidate guard, not a full fix:

- `GetSupportedDevices` (Windows only) queries `hipGetDeviceCount()` once and
  validates each AMD GPU candidate's `DxgiAdapterNumber` before creating any
  memory info: missing metadata, non-canonical/negative/overflowing values
  (strict parse — `std::atoi`'s silent truncation is part of the original
  bug), or values `>=` the HIP count cause the candidate to be skipped with
  an explicit WARNING naming the LUID, raw adapter number, HIP count, reason,
  and recommended user action. Valid candidates still register.
- If `hipGetDeviceCount()` itself fails, the guard logs a warning and
  degrades to metadata-only validation (fail-open on HIP unavailability —
  the umbrella factory also serves DirectML-profile routing; see design doc
  open question 5).
- Non-Windows builds are unchanged (legacy position fallback; no DXGI
  metadata exists there).
- Decision logic lives in a new pure, header-only
  `src/amdgpu/device_identity_guard.h` (no Windows/ORT/HIP API calls) with a
  standalone test `tests/device_identity_guard_test.cc`. A TODO(multi-device)
  comment documents the known `gpu_memory_info_`/`pinned_memory_info_`
  single-slot overwrite hazard that this PR intentionally does not fix.

Design doc with root-cause analysis, the four-namespaces model
(DXGI-index / LUID / PCI-BDF / HIP-ordinal), why hardcoding 0 or clamping to
`hipGetDeviceCount()-1` is wrong, full-fix options (LUID→ordinal via
`hipGetDeviceProperties().luid` as the MIGraphX backend already does,
per-device memory-info maps, canary-at-init, ORT-core dedup improvements),
and open questions for maintainers: `UPSTREAM_FIX_DESIGN.md`.
Issue-shaped repro write-up: `UPSTREAM_ISSUE_DRAFT.md`.

### Status labels (honesty section)

- **PATCH_PREPARED_UNBUILT**: this branch was authored on a host without
  MSVC/clang, the Windows SDK, or a HIP SDK. Nothing here has been compiled,
  linked, or run. The code review is source-only.
- **BLOCKED_TOOLCHAIN**: `build.bat` cannot run on the authoring host
  (missing MSVC / Windows SDK / HIP SDK / MIGraphX SDK). Local build+test is
  blocked until a suitable host is available.
- The standalone unit test is written to compile with any C++11 compiler
  without the repo build system (`tests/device_identity_guard_test.cc`
  header shows the exact commands); it has NOT been compiled on this host
  (no compiler installed).

### Formatting note

The repository's `.clang-format` referenced by CONTRIBUTING.md is not present
in the tree at the base commit, and no clang-format binary is available on
the authoring host; the C++ changes were hand-formatted to match the
surrounding style (4-space indent, brace style of neighboring code). Please
run `git clang-format` over the branch if anything drifts.

### Test plan (for a host with a working toolchain)

1. Build the EP on Windows per CONTRIBUTING.md; run
   `tests/device_identity_guard_test.cc` standalone (expected: exit 0).
2. Single-GPU host, no mirrors: registration and inference unchanged
   (regression check); log shows no warnings.
3. Single-GPU host with an indirect display driver (mirror at DXGI 1):
   EP device list contains only the physical GPU; the mirror is skipped with
   a WARNING naming LUID/DxgiAdapterNumber/hipGetDeviceCount; session
   creation and inference succeed on the surviving device.
4. Optional multi-GPU host: both physical GPUs register with correct
   ordinals (the guard only validates range; LUID→ordinal mapping is the
   follow-up).
5. HIP-less host (DirectML profile): registration still works; the
   hipGetDeviceCount failure path logs one warning.

### CI

No CI exists on this fork; none of the links below imply CI runs.

### Evidence links

- Root-cause source chain: `src/amdgpu/gpu_factory.cc` (GetSupportedDevices),
  `src/migraphx/mgx_factory.cc` (CreateAllocator,
  `memory_info.GetDeviceId()`), `src/shared/hip/allocator.cc`
  (`SetDevice` → `hipSetDevice`).
- Host audit receipts summarized in `UPSTREAM_FIX_DESIGN.md` section 8
  (DXGI table, LUIDs, output counts, hipGetDeviceCount=1, hipSetDevice(1) →
  101 across two HIP runtimes).
- No upstream issue/PR is linked because none exists yet for this
  conflation (searched: DxgiAdapterNumber, HIP ordinal, invalid device
  ordinal, LUID — no hits in onnxruntime/onnxruntime-ep-amdgpu issues/PRs;
  nearest change is #170, which synthesizes devices from HIP on WSL and does
  not touch the Windows DXGI path).

### Not in this PR (deliberately)

- Per-device `gpu_memory_info_`/allocator maps (multi-GPU lifetime hazard) —
  flagged TODO, design doc section 6B.
- LUID→ordinal resolution and canary-at-init — design doc sections 6A/6C,
  pending maintainer answers to the open questions (section 7).
