# R2.1 — Open gaps and authorization boundaries

Everything below stays OUT of R2.1 scope by owner authorization; listed so
the residual risk surface is explicit and no future reader mistakes
"not done" for "done and fine".

## Host/system-level (admin or owner action required)

- **Driver upgrade** (32.0.21030.2001 → newer Adrenalin): not performed;
  R1 evidence showed the driver is not the proximate cause of HIP 101, and
  the MIGraphX EP documented minimum (≥25.10.13.09) remains UNVERIFIED
  against this driver (no version-mapping evidence).
- **GameViewer virtual display adapter disablement + reboot**: the owner's
  proposed decisive experiment for the mirror-adapter population; requires
  device-manager/admin and reboot — outside this session's authorization.
- **RDP/Remote Display Adapter**: present on this host (SWD\REMOTEDISPLAYENUM);
  same handling as above.
- **System software installs** (VS Build Tools, HIP SDK, Windows SDK,
  CMake/Ninja): prohibited; local native build therefore remains
  `PATCH_NATIVE_BUILD_BLOCKED_TOOLCHAIN`.

## Toolchain / verification gaps

- **Native Windows AMD EP build** of the fork patch (`build.bat` path) not
  executed anywhere; guard helper is compiled+tested (3 compilers via fork
  CI) but `gpu_factory.cc` integration is unverified — statuses
  `GUARD_HELPER_COMPILED_TESTED` vs `NATIVE_FACTORY_NOT_BUILT` vs
  `NO_ON_METAL_PATCH_VALIDATION` must not be conflated.
- **paddle2onnx OCR track**: still BLOCKED_TOOLCHAIN (native DLL load fails
  without MSVC runtime); unchanged from R2.
- **VRAM/thermal telemetry**: no instrumentation was validated this round;
  no memory/power numbers are claimed anywhere in R2.1 (no fake -1/0
  placeholders published).

## Evidence-domain gaps

- **SSD Mobilenet v1 partition root cause**: whole-graph-CPU is reproduced
  and barrier hypotheses are isolated via minimal fixtures (see
  SSD_PARTITION_RCA.md); the exact unsupported-operator list for the full
  350-node graph remains `CAPABILITY_GAP_NOT_ISOLATED` at the full-graph
  level unless the fixture matrix demonstrates otherwise — no invented root
  cause.
- **R2 historical MIGraphX stall**: `RCA_NOT_REPRODUCED` after 6 controlled
  attempts (see SOAK_RCA.md); the single historical occurrence stays
  `FAIL_SOAK_NO_CLEAN_EXIT_HEALTHY_IN_WINDOW`. No upstream issue from a
  single confounded stall.
- **1314-vs-1319 manifest counts**: reconciled (stale snapshot); the R2
  round-1 manifest's 4 known mismatches remain preserved-with-errata, never
  silently repaired.

## Credential hygiene

- The `r2-deploy-key` noted in R2's LATEST.md: normal HTTPS push worked for
  all R2.1 pushes (via local proxy), so the deploy key appears no longer
  needed; per mission rules it is NOT revoked automatically — owner should
  remove it in repo settings after confirming (no action taken here).
- No credentials, tokens, private keys, or user-identifying paths are
  present in published R2.1 artifacts (redaction scan: 0 hits required
  before manifest; see redaction_scan.json).
