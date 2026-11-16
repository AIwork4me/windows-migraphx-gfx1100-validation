# R1 upstream readiness (read-only proposals; nothing filed without authorization)

Per mission rules nothing below has been posted upstream. All statements are
backed by receipts under `reports/r1/r1_20261116T232607Z/` and the source
checkout `deps/onnxruntime-ep-amdgpu` @ 77bba05.

## Candidate 1 (highest value): amdgpu-ep uses DxgiAdapterNumber as HIP ordinal

- **Repo**: onnxruntime/onnxruntime-ep-amdgpu
- **Defect**: `src/amdgpu/gpu_factory.cc` `GetSupportedDevices` takes
  `DxgiAdapterNumber` from WinML device metadata and uses it as the
  `device_index`/`device_id` that later reaches
  `hip::SetDevice → hipSetDevice`. DXGI adapter numbers and HIP device
  ordinals are unrelated numbering spaces; on any multi-adapter host where
  the AMD GPU is not DXGI adapter 0, session creation/first-run fails with
  `HIP failure 101: invalid device ordinal; GPU=0` — even though
  `hipGetDeviceCount()=1` and `hipSetDevice(0)` succeed on the same machine
  with the same EP-bundled `amdhip64_7.dll`.
- **Repro condition** (minimal): Windows host, ≥2 display adapters (e.g.
  iGPU + virtual/indirect display driver), AMD dGPU not at DXGI index 0,
  WinML AMD GPU EP 1.8.65.0, select either advertised ep-device: the
  DxgiAdapterNumber≠0 entry always HIP-101s; the DxgiAdapterNumber=0 entry
  works (GPU execution + parity). We can attach both receipts.
- **Minimal fix scope**: validate/translate the index against
  `hipGetDeviceCount()` (and/or enumerate HIP devices by PCI bus id /
  `hipDeviceGetAttribute` matching rather than DXGI number); emit a clear
  error naming the mismatch otherwise. Regression test: unit test that the
  chosen device id is < hipGetDeviceCount before CreateMemoryInfo.
- **Risk**: low — pure bounds/mapping check in factory path; no ABI change.
- **Uncertainties to flag to maintainers**: why WinML lists the same PCI
  device twice (indirect display adapter) and whether they consider the
  second entry theirs to filter.

## Candidate 2: silent CPU fallback surfaces (issue #151 adjacent)

- **Repo**: onnxruntime-ep-amdgpu #151 (and ORT core behavior).
- **R1 adds**: an 18-cell differential matrix showing exactly which
  configurations drop the EP silently (see
  `docs/ISSUE_151_SCOPE_AND_LIMITATIONS.md`). Narrowest genuinely-silent
  case: **registered MIGraphX EP + classic providers** → CPU-only session,
  no warning, no exception. Also: the ORT plugin wrapper prints
  "EP Error … Falling back" on **stdout** only, so stderr-only capture
  misses it.
- **Possible ask**: (a) MIGraphX classic path should either engage or warn
  like AMDGPU does; (b) the fallback block should also go to stderr / be
  surfaced via the session provider list API docs.
- **Risk**: documentation/UX change; no semantics break.

## Candidate 3: fail-closed helper pattern (blog/docs, not a code PR)

`runner/r1/fail_closed.py` + tests — a portable recipe for apps (e.g.
X-AnyLabeling adapters) to make GPU absence loud. Could become an ORT docs
snippet or a small `onnxruntime` python convenience wrapper after
maintainer feedback.

## What we would need from the user to file

Explicit authorization to open issues/PRs upstream (mission forbids it by
default), plus a decision on whether to attach host fingerprint details
(adapter list) — redaction is already prepared.
