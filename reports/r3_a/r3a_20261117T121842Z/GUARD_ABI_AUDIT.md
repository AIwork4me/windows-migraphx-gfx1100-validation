# R3-A A1 — Guard source & ABI-safety audit (source-only, evidence-linked)

Scope: fork `AIwork4me/onnxruntime-ep-amdgpu` @ `0baccb25eb607fe43255bf4d5b09a93df8571c91`
(base = upstream/main `15ded64bd998a317dfe0ccf0ead8033574dd5b86`, drift 0 commits).
Files: `src/amdgpu/device_identity_guard.h` (165 lines),
`src/amdgpu/gpu_factory.cc` (+84), `tests/device_identity_guard_test.cc` (292).

This is a SOURCE audit. No native build exists on this host (BUILD_LEDGER),
so "ABI-safe" below means *verified by code reading + header-only tests on
three compilers*; it is NOT a linked-ABI proof. Nothing here upgrades the
layered verdict past `GUARD_HELPER_CI_PASS`.

## Audit checklist vs findings

1. **Evaluate / EvaluateAll / ParseAdapterNumber** — strict decimal parse
   (rejects empty/signed/whitespace/trailing/overflow; std::atoi pitfalls
   avoided), range check `adapter_number < hip_device_count` when count
   >= 0, LUID dedup first-in-list-wins in EvaluateAll. Sound.
2. **TestGuardLimits** — present and honest: documents that bounds-proof ≠
   identity-proof; valid-range ordinal may still point at the wrong
   physical GPU; two distinct LUIDs aliasing one ordinal remain possible.
   The mission's "DXGI index < hipGetDeviceCount does not imply same
   physical GPU" counterexample is encoded as the documented limit, and
   the patch's PR drafts must (and do, UPSTREAM_PR_DRAFT.md) scope the
   change as a bounds guard, not an identity mapping or a universal fix.
3. **Fail-open on hipGetDeviceCount failure** — real and deliberate:
   count<0 → metadata check only → ACCEPT with WARNING log. Acceptable
   tradeoff (if the HIP count query fails, HIP is unusable anyway and
   legacy behavior is preserved); flagged so a future change could make
   it configurable. Registered as a limitation, not a defect.
4. **DML-only host** — guard code runs inside the AMD-vendor branch and
   the one-time count query happens on the real registration path only;
   with no AMD candidates the loop body never executes. The count query
   does force amdhip64 delay-load earlier than before on registration —
   the factory comment documents that provider PROBE path stays
   HIP-free. Behavior change limited to registration-time logging.
5. **GPU probe path / HIP delay-load** — no change to the DLL probe path
   (source-verified: the new call is inside GetSupportedDevices only).
6. **Windows ABI / ORT API version** — uses only OrtEpFactoryV2 API
   already used by the file; no new ORT ABI surface. Header is pure
   C++11 (no Windows/ORT/HIP includes) → no ABI exposure from the guard
   itself. Full-ABI proof requires the blocked native build.
7. **Allocator single-slot overwrite (multi-candidate)** — pre-existing
   upstream limitation; the fork ADDS a TODO(multi-device) comment
   documenting the leak/overwrite hazard and the per-device-map design
   needed (pattern: src/migraphx/mgx_factory.h). Not fixed here —
   correct minimal-change decision; listed in FULL_FIX_GAP.
8. **Same-card mirrors with DIFFERENT LUIDs cannot be deduped** — true
   and by-design: LUID dedup only catches same-LUID duplicates. On the
   R2 host the three 1002:744c adapters carried different LUIDs and are
   caught by the RANGE check instead (DxgiAdapterNumber 1,2 >= count 1
   → kSkipOutOfRange). TestMirrorCaseSingleGpu encodes exactly this.
9. **Hardcoded 0 / clamp / MATCHED_IDENTITY claims** — none present. No
   clamping (out-of-range → skip, never min(index,count-1)); no
   identity claims anywhere in code or drafts.
10. **Unit tests green** — 6 CI checks SUCCESS (g++, clang++ -Wall
    -Wextra -Werror; MSVC /W4 /WX) at head 0baccb2; 10 test scenarios
    incl. two-real-GPUs, HIP count 0, unknown count, order permutation,
    duplicate LUIDs, range edges, documented limits.

## Verdict

Source-level audit: PASS with documented limitations (3, 7, 8 above are
limitations inherent to a bounds guard, all disclosed). No source errors
found requiring change. ABI claim ceiling: `GUARD_HELPER_CI_PASS`
(compiled logic only); `NATIVE_COMPILED` and above remain BLOCKED
(BUILD_LEDGER.json).
