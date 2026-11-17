# R2 upstream patch review sheet (for the owner's review of the fork PR)

Fork PR: https://github.com/AIwork4me/onnxruntime-ep-amdgpu/pull/1
(Draft; base = fork's own `main`; head `e3ddafc27ae47bb343240137b7c22f301dcce81`;
branch `fix/windows-dxgi-hip-device-identity`; upstream base SHA
`15ded64bd998a317dfe0ccf0ead8033574dd5b86` = onnxruntime-ep-amdgpu main
at fork time, 2026-11-17).

## Status: PATCH_PREPARED_UNBUILT — GUARD_ONLY_NOT_FULL_FIX

- **Built/run natively?** NO. Host has no MSVC/Windows SDK/HIP SDK
  (`BLOCKED_TOOLCHAIN`). No claim is made that this patch compiles or
  runs; the Windows ML EP package in use is the official MSIX, untouched.
- **What it IS:** a minimal fail-closed guard + pure decision logic +
  tests, structured so maintainers can review the semantics without a
  Windows box.

## Changes (897 insertions / 7 deletions)

1. `src/amdgpu/gpu_factory.cc` — Windows-only guard in
   `GetSupportedDevices`: query `hipGetDeviceCount()` once; strictly
   parse `DxgiAdapterNumber`; SKIP candidates whose value is missing/
   negative/non-numeric/overflowing/`>= hip count`, logging a WARNING
   with LUID, raw value, HIP count, reason, recommended action. Valid
   candidates still register (fail-closed per candidate, not per
   factory). Non-Windows path unchanged (Linux has no DxgiAdapterNumber;
   skipping there would brick registration — documented deviation).
   `hipGetDeviceCount` failure = warn + metadata-only validation
   (fail-open, flagged as maintainer question #5).
   Plus `TODO(multi-device)` comment on the `gpu_memory_info_`/
   `pinned_memory_info_` overwrite/leak hazard (NOT fixed here).
2. `src/amdgpu/device_identity_guard.h` (new) — header-only pure
   functions (`Evaluate`, `EvaluateAll` with LUID dedup,
   `ParseAdapterNumber`), no Windows/ORT/HIP calls.
3. `tests/device_identity_guard_test.cc` (new) — standalone C++11 test
   (single file, no repo build system), covering: DXGI=1+HIP=1 mirror;
   order permutation; duplicate LUIDs; missing metadata; negative/
   overflow/non-numeric; DXGI=0+HIP=1 pass; two real GPUs; HIP count=0;
   unknown HIP count. Header-marked PATCH_PREPARED_UNBUILT.
4. `UPSTREAM_FIX_DESIGN.md` / `UPSTREAM_ISSUE_DRAFT.md` /
   `UPSTREAM_PR_DRAFT.md` — four-namespaces root cause, why
   hardcode-0/clamp-to-count is wrong, full-fix options
   (`hipDeviceProp_t.luid` mapping — already used by `mgx_factory.cc`
   on the WSL path — canary-at-init, per-device allocator maps, ORT-core
   dedup fix), 5 maintainer questions.

## Why NOT simply hardcode 0 / clamp to hipGetDeviceCount()-1

Hardcode 0 fails on hosts where the AMD GPU is not DXGI 0 (proven: this
host's mirror occupies DXGI 1; other hosts can have the real card at 1+).
Clamping would SILENTLY MAP a wrong candidate (e.g. mirror Dxgi=1 → HIP 0)
— trading a loud crash for wrong-device execution, the worst failure
mode for device identity. The guard skips unverifiable candidates and
says why; identity mapping is left to the documented full-fix options.

## Deviations from the R2.4 tasking (deliberate, documented in PR)

1. Missing-metadata skip is Windows-only (Linux keeps legacy behavior).
2. `hipGetDeviceCount` failure is fail-open with warning (maintainer
   question; fail-closed would also block DML-profile routing).
3. Runtime wires per-candidate `Evaluate` (not the batch LUID-dedup
   `EvaluateAll`) to keep the diff minimal; swap documented as follow-up.
4. No `.clang-format` exists in the repo tree (CONTRIBUTING references
   it; file absent) and no clang-format binary on host — hand-formatted
   to neighboring style; stated in the PR body.
5. Repo has no tests/ infrastructure; standalone test file created.

## Verification performed (all read-only / CPU)

- Upstream main inspected: no existing fix (searched PRs/issues for
  DxgiAdapterNumber / HIP ordinal / LUID; reviewed #139–#171; only
  gpu_factory.cc touch is #165 initializers).
- Patch review against the R2 root-cause evidence (docs/R2_DEVICE_IDENTITY.md):
  the guard rejects exactly the candidate class (mirror Dxgi=1 with
  HIP count=1) that produced HIP 101 on this host.
- Fork PR base verified = fork main, NOT onnxruntime:main; no upstream
  issue/PR/comment created anywhere.
