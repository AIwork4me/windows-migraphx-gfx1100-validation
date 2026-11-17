# R3-A — FULL_FIX_GAP: what is fixed, what is not, what was never in scope

Layered verdicts (R3-A exit state):

```
GUARD_HELPER_CI_PASS          ✅  fork PR #1 @0baccb2, 6 checks green
NATIVE_COMPILED               ❌ BLOCKED_DEPENDENCY (BUILD_LEDGER.json)
NATIVE_PLUGIN_REGISTERED      ❌ (requires the above)
PATCHED_GPU_EXECUTED_WITH_PARITY ❌ (requires the above)
IDENTITY_MAPPING_PORTABLE_VERIFIED ❌ (requires the above + second host)
```

## Problem → status map

| # | problem | layer | status |
|---|---|---|---|
| 1 | DXGI adapter-number used as HIP ordinal reaches hipSetDevice(n≥count) → HIP 101 | EP factory bounds | FIXED in fork patch (bounds guard, skips out-of-range candidates) — helper CI only, not native-verified |
| 2 | Same-LUID mirror duplicates | EP factory dedup | FIXED (EvaluateAll; not yet wired in factory — factory uses per-candidate Evaluate; wiring EvaluateAll is a follow-up, see test header note) |
| 3 | Mirrors with DIFFERENT LUIDs surviving platform dedup | EP factory range | MITIGATED by the same bounds guard (host evidence: LUID 72346/118045/617528 all distinct; only range check catches 118045) |
| 4 | DXGI index < hipGetDeviceCount may still point at the WRONG physical GPU (2 dGPU, virtual at index 0, permuted orders) | identity mapping | NOT FIXED — needs true DXGI LUID ↔ HIP ordinal identity join (e.g. via hipDeviceGetAttribute/adapter LUID query or DXCore + HIP properties). Guard explicitly does not claim this (TestGuardLimits) |
| 5 | Allocator single-slot overwrite in multi-GPU registration | EP factory memory info | NOT FIXED (pre-existing; TODO(multi-device) comment added by fork) |
| 6 | hipGetDeviceCount failure → fail-open accept | EP factory | DOCUMENTED tradeoff, kept (warning logged) |
| 7 | Loop scan outputs numerically wrong (both EPs) | EP compiler/ops | NOT FIXED, NOT IN FORK SCOPE — discovered by R2.1.1 (reports/r2_1_1/…/SSD_CAUSALITY_TABLE.md); needs AMD EP compiler fix |
| 8 | NonZero fixed-count wrong results (both EPs) | EP compiler/ops | same as 7 |
| 9 | Conv→Loop parse stride EP_FAIL → session CPU fallback | EP compiler/ops | same as 7 |
| 10 | #151 classic-providers silent EP drop | ORT core behavior | DOCUMENTED (R1/R2 evidence); fail-closed resolver is the app-side mitigation; not an EP-source fix |

Items 4–9 are why a bounds guard must never be marketed as "fixes Windows
multi-adapter AMD EP". The honest upstream story: the guard prevents a
crash-class registration failure (HIP 101) on mirror-adapter hosts; it
does not implement identity mapping, does not fix EP numerics defects,
and its factory integration is not yet native-verified.

## What a REAL native verification would require

1. Owner-authorized installs: VS2022 + Windows SDK, Windows HIP SDK
   (matches amdhip64_7 ABI shipped in MSIX 1.8.65.0), and MIGraphX dev
   headers/libs (only obtainable by building AMDMIGraphX from source on
   Windows — no public standalone dev SDK).
2. `build.bat --config Release --cmake_generator Ninja --onnxrt_home
   <matching ORT> --use_amdgpu --migraphx_home <built SDK>` from a clean
   fork checkout; record full commands, versions, warnings, binary SHA.
3. Isolated subprocess A/B: stock MSIX EP vs patched DLL proven loaded
   via GetModuleFileNameW + absolute path; canary/YOLO26n/SCRFD/SSD-loop
   matrix identical to R2/R2.1.1 methodology.
4. None of the above may touch the signed MSIX package in WindowsApps.
