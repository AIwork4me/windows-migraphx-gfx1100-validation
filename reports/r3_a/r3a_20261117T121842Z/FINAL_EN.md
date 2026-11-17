# R3-A Final Report (English)

Run ID: `r3a_20261117T121842Z` · branch `mission/r3a-native-ep-validation`
(based on main `6b780a4`, after R2.1.1 merge `7ae279c`).

## Verdict: `GUARD_HELPER_CI_PASS` (the only achieved layer);
`NATIVE_COMPILED` and above are **BLOCKED_DEPENDENCY** (a high-quality
blocked conclusion, not a fake pass).

## A0 evidence (all measured, nothing inferred)

- Fork: head `0baccb2` (local = remote = PR #1 head); base `15ded64` ==
  upstream/main (**zero drift**); PR #1 Draft, MERGEABLE, 6 CI checks
  SUCCESS (g++/clang++/MSVC).
- Host toolchain: cl / cmake / ninja / hipcc / clang++ / g++ all
  NOT_FOUND; no vswhere (no Visual Studio instance); no
  `C:\Program Files\AMD\ROCm`; no HIP SDK registry key.
- **WinML MSIX dev-file probe (legal read-only walk)**: the 1.8.65.0
  ExecutionProvider directory contains **0 headers, 0 import libs** —
  a runtime package is not a dev SDK; nothing was extracted from it.
- Classifications: `BLOCKED_MIGRAPHX_DEV_LIB` + `BLOCKED_HIP_SDK` +
  `BLOCKED_TOOLCHAIN`; admin installs are `NEEDS_OWNER_AUTH` (not
  granted; no system changes made).
- GitHub-hosted Windows runners: ship MSVC but lack the Windows HIP SDK
  and MIGraphX dev libs (no public standalone distribution), so the CI
  environment cannot reach NATIVE either; guard-helper-cpp remains the
  maximal genuine CI verification.

## A1 source audit — 10/10 checklist items verified (GUARD_ABI_AUDIT.md)

Strict parsing / bounds check / same-LUID dedup correct; TestGuardLimits
honestly encodes "in-range ≠ same physical GPU"; fail-open on count
failure documented; no behavior change on DML-only hosts; allocator
single-slot overwrite is pre-existing upstream (TODO comment added);
no hardcoded 0, no clamping, no MATCHED_IDENTITY claims anywhere.
No source defects found. ABI claim ceiling: header-only compile+run on
three compilers — never presented as a link-level proof.

## A2/A3 native build & stock-vs-patched A/B

Build not attempted on host (dependencies measurably absent);
no native binary exists (recorded). A/B comparison: **NOT_TESTED**
(nothing to compare); ON_METAL_MATRIX.csv marks stock as VALIDATED by
R2/R2.1.1 frozen evidence and patched as NOT_BUILT/NOT_TESTED.

## A4 delivery

All artifacts in this dir + manifest + redaction scan; branch pushed to
the validation repo as a Draft PR. Fork untouched; no onnxruntime/*
upstream submissions; nothing merged.

## R3-B handoff

**`STOCK_MSIX_VALIDATED`** (WinML AMD EP 1.8.65.0). Native patched EP
NOT validated (`PATCHED_NATIVE_BLOCKED`). R3-B uses only the stock MSIX;
GUARD_ONLY artifacts must not be presented as an official WinML build.
