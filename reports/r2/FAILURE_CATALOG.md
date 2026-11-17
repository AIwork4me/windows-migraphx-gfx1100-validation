# R2 failure catalog — every non-PASS case, with cause evidence

Run: `r2_20261117T004504Z`. Classifications are the child receipts'; the
finalize step marks CPU-reproducible fixture-graph failures as
`SKIPPED_FIXTURE_DEFECT` (raw child receipt unchanged; ledger records
`classification_raw` + reason).

## A. SKIPPED_FIXTURE_DEFECT — 54 cases (6 fixtures × 2 EP × batches/opt)

Fixture graphs that a **pure CPU session of the same ORT build also fails
to load** — the failure is in the fixture, not in any EP. CPU evidence
command: `venv-winml\Scripts\python.exe -c "import onnxruntime as ort;
ort.InferenceSession('fixtures/<name>.onnx',
providers=['CPUExecutionProvider'])"` (output in R2 work log; identical
error to the EP case).

| fixture | CPU error | cases |
|---|---|---|
| argmax | type error: ArgMax int64 output declared float | 6 |
| reduce_sum | INVALID_GRAPH at load | 6 |
| squeeze | INVALID_GRAPH at load | 6 |
| pad | Pad ShapeInferenceError | 6 |
| topk_gather_concat | TopK ShapeInferenceError | 6 |
| split2 | INVALID_GRAPH at load (incl. DISABLE_ALL/BASIC/EXTENDED variants) | 24 |

These fixtures predate R2 (round-1 manifest); their on-disk bytes and
manifest hashes are unchanged. Multi-output opt-level coverage is
unaffected: two_out_same_shape / two_out_diff_shape / nine_out all
passed at every opt level and batch (b1/b2/b4 × DISABLE_ALL/BASIC/
EXTENDED/ALL × 2 EPs).

## B. FAIL_NO_GPU_COMPUTE — 2 cases (1 model × 2 EPs)

`ssd_mobilenet_v1_10` (ONNX Model Zoo, TF-exported graph): session
ATTACHES both EPs (`providers=['<EP>', 'CPUExecutionProvider']`), parity
vs CPU passes, but **0 of 5587 compute nodes** are attributed to the EP —
the whole graph partitions to CPU (TFLite-style custom ops /
FakeQuant-heavy graph; every partition containing an unsupported op pulls
its connected component to CPU). Not a silent fallback: providers list +
node attribution expose it. DML (round-1 m2) did execute this model —
per-EP capability gap, recorded as-is.

- receipts: `cases/ssd_mobilenet_v1_10__AMDGPU__b1__ALL.json`,
  `cases/ssd_mobilenet_v1_10__MIGraphX__b1__ALL.json`

## C. Input-domain harness fixes (corrected, not EP bugs)

`log` / `sqrt` fixtures initially compared NaN-vs-NaN (random negative
inputs) — 12 cases misclassified FAIL_CORRECTNESS on first pass; harness
fixed (domain-safe feeds, same rule as round-1) and all 12 re-ran PASS.
Similarly SCRFD/SSD first pass fed degenerate 1×1 feature maps (dynamic
dims collapsed) — fixed with explicit input sizes (640/300); SCRFD then
passed full-coverage. Both fixes are test-input generation changes, not
tolerance relaxations; receipts before/after both retained in git history.

## D. BLOCKED (not executed, honest labels)

- **OCR (PP-OCR det+rec)**: `BLOCKED_TOOLCHAIN` — official Paddle CDN
  inference models downloaded; paddle2onnx native module cannot load on
  this host (`DLL load failed ... paddle2onnx_cpp2py_export`; no MSVC
  toolchain; tried paddle2onnx 2.1.0 + 1.2.4, onnx present, protobuf
  pinned). No official prebuilt ONNX from official channels (probed).
- **SAM/SAM2 encoder+decoder**: `BLOCKED_MODEL_ASSET` (no official
  license-clear ONNX asset obtainable this round without torch export
  chain; deprioritized vs P0).
- **X-AnyLabeling minimal adapter (P2)**: not started this round —
  `NOT_TESTED` (see GO/NO-GO).
- **Native C++ build of upstream guard patch**: `BLOCKED_TOOLCHAIN`
  (no MSVC/Windows SDK/HIP SDK; PATCH_PREPARED_UNBUILT — fork PR).
