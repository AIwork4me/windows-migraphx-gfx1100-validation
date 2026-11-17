# R2.1-B — Guard helper validation (fork PR #1)

Branch: `fix/windows-dxgi-hip-device-identity` @ `0baccb2`
(pushed non-force from `e3dd9af`; fork Draft PR #1, targets the fork's own
`main`, NOT `onnxruntime/onnxruntime-ep-amdgpu`):
https://github.com/AIwork4me/onnxruntime-ep-amdgpu/pull/1

## What changed in R2.1

1. `tests/device_identity_guard_test.cc`: fixed the deterministic wrong
   assertion — `Evaluate(Candidate{"A","2147483647"}, hip_count=100)` MUST
   be `kSkipOutOfRange`, the draft expected `kAccept`. INT_MAX *parsing* is
   now proven independently (`ParseAdapterNumber("2147483647")` succeeds,
   `"2147483648"` fails). Added exact range-edge tests (count-1 accept /
   count reject), re-enumerated same-LUID/different-DXGI-index dedup case,
   unknown-count INT_MAX fail-open documentation, and `TestGuardLimits`
   encoding what a bounds guard CANNOT establish (distinct LUIDs aliasing
   one ordinal; valid-range ordinal pointing at the wrong HIP GPU) — stated
   as accepted-today so any future identity verification is noticed.
2. `.github/workflows/guard-helper-cpp.yml`: fork-only CI compiling and
   RUNNING the standalone test on three real toolchains. No HIP SDK needed.

## Actual compile+run evidence (not paper)

Workflow run **44016624979** (push, commit `0baccb2`, 2026-11-17T04:21Z) and
**44016625272** (same commit via PR event) — both `guard-helper-cpp`,
conclusion **success**, all 3 jobs green:

| job | toolchain | compile command | test output |
|---|---|---|---|
| linux-gcc | g++ (Ubuntu) | `g++ -std=c++11 -Wall -Wextra -Werror -o guard_test tests/device_identity_guard_test.cc` | `device_identity_guard_test: all checks passed` |
| linux-clang | Ubuntu clang version 21.1.8 | `clang++ -std=c++11 -Wall -Wextra -Werror -o guard_test_clang tests/device_identity_guard_test.cc` | `device_identity_guard_test: all checks passed` |
| windows-msvc | MSVC via `ilammy/msvc-dev-cmd@v1` | `cl /nologo /W4 /WX /EHsc /std:c++14 /Fe:guard_test.exe tests\device_identity_guard_test.cc` | `device_identity_guard_test: all checks passed` |

Each job's exit code is checked by `bash -e` / pwsh `$LASTEXITCODE`; the
test binary exits 0 only when zero CHECK failures.

## Status taxonomy (kept precise)

- `GUARD_HELPER_COMPILED_TESTED` — yes, 3 compilers × compile+run × exit 0.
- `PATCH_NATIVE_BUILD_BLOCKED_TOOLCHAIN` — the full native EP (`build.bat`
  with MSVC + Windows SDK + HIP SDK + MIGraphX SDK) is NOT built anywhere;
  authoring host has no toolchain (inventory in BASELINE.json: cl/clang/
  gcc/cmake/ninja all absent, vswhere NOT_INSTALLED) and CI does not
  attempt the native build.
- `NO_ON_METAL_PATCH_VALIDATION` — no GPU ran a build containing this
  patch; gfx1100 evidence in the validation repo uses the official MSIX
  package.
- `EvaluateAll` (LUID dedup) remains unintegrated: factory wires per-candidate
  `Evaluate` only; the test file states this explicitly.

## Remaining guard risks (B3, unchanged by R2.1)

1. Range check is necessary but insufficient for physical identity.
2. `hipGetDeviceCount` failure ⇒ bounds check disabled (fail-open with
   warning); DirectML-profile routing would regress if made globally
   fail-closed without testing — deliberately left as-is.
3. `GetSupportedDevices` may call HIP during enumeration; WinML package
   load/probe implications unverified without native build.
4. `gpu_memory_info_`/`pinned_memory_info_` single-slot overwrite hazard on
   >1 accepted GPU — out of scope for this focused PR (TODO in code).
5. Guard logs must not push users toward disabling virtual adapters as a
   default remedy when explicit device selection exists.

## Formatting divergence note

`CONTRIBUTING.md` asks for `git clang-format origin/main`, but the base
commit has no `.clang-format` file and no clang-format ran; changes are
hand-formatted to neighboring style. Documented, not invented.
