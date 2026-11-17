# R2 model-matrix reproduction guide (Windows, RX 7900 XTX)

Everything below runs on the physical host (Win11 + RX 7900 XTX + WinML
AMD EP MSIX). CI has no GPU; CI only checks structure, unit logic and
receipt contracts.

## One-shot entry

```powershell
powershell -File scripts\run_r2.ps1 -RunId r2_<timestamp>
# resume after interruption / environment unchanged:
powershell -File scripts\resume_r2.ps1 -RunId r2_<timestamp>
# re-run previously FAILED cases (only after a deliberate fix):
powershell -File scripts\resume_r2.ps1 -RunId r2_<timestamp> -RetryFailed
```

Case identity = (model sha256, EP, batch, opt level, ORT build, device
fingerprint). A case DONE under a different fingerprint re-runs.

## What a GPU PASS requires (enforced by runner/r2/model_child.py)

1. target EP in session `get_providers()` (session built via
   `register_execution_provider_library` + `add_provider_for_devices([the
   resolved device], {})` — never classic `providers=[...]`, which drops
   the EP silently, see #151)
2. >=1 compute node attributed to the EP in the ORT profile
   (`nodes_by_provider` in every receipt)
3. by-name per-output parity vs an ORT CPU reference from the SAME build,
   identical inputs, PREDECLARED tolerance (synthetic fp32: atol 1e-4 /
   rtol 1e-3; real models fp32: atol 1e-2 / rtol 1e-2; never relaxed
   after the fact)
4. device resolution evidence (LUID join + Discrete + active output +
   canary) in the receipt

## Single-case manual run

```powershell
venv-winml\Scripts\python.exe -I runner\r2\model_child.py `
  --model fixtures\two_out_same_shape.onnx --ep MIGraphXExecutionProvider `
  --batch 2 --opt EXTENDED --family synthetic `
  --out %TEMP%\case.json
```

YOLO end-to-end (preprocess -> GPU -> NMS -> detections):

```powershell
venv-winml\Scripts\python.exe -I runner\r2\yolo_e2e_child.py `
  --ep AMDGPUExecutionProvider --out %TEMP%\yolo.json
```

Perf / soak (GPU-correct models only):

```powershell
venv-cpu\Scripts\python.exe runner\r2\perf_soak_driver.py --run-id <id>
venv-cpu\Scripts\python.exe runner\r2\perf_soak_driver.py --run-id <id> --soak --soak-minutes 15
```

## Model assets

Tracked by hash only in `reports/r2/<run_id>/MODEL_PROVENANCE.csv`
(weights themselves are gitignored). Re-acquire:

- ONNX Model Zoo five (mnist-8, squeezenet1.0-12, mobilenetv2-12,
  emotion-ferplus-8, ssd_mobilenet_v1_10): round-1 assets, rehashed.
- yolo26n.onnx: official export — `yolo export model=yolo26n.pt
  format=onnx opset=17 imgsz=640 dynamic=False simplify=True`
  (ultralytics 8.4.215, torch 2.15.0+cpu).
- scrfd_det_10g.onnx: insightface release v0.7 `buffalo_l.zip`
  (`det_10g.onnx`, 9 outputs). Non-commercial research license.
- bus.jpg: ultralytics official sample asset.

## Classification vocabulary

`PASS_GPU_CORRECT_FULL_OR_KNOWN_COVERAGE`, `PASS_GPU_CORRECT_PARTIAL_
COVERAGE`, `FAIL_CORRECTNESS`, `FAIL_NO_GPU_COMPUTE`, `FAIL_EP_NOT_
ATTACHED`, `FAIL_COMPILATION`, `FAIL_RUNTIME`, `FAIL_UNSUPPORTED_OP`,
`SWAP_SUSPECTED_150`, `NATIVE_CRASH`, `TIMEOUT`, `SKIPPED_FIXTURE_DEFECT`
(fixture graph itself fails on a pure CPU session — CPU evidence kept),
`SKIPPED_INPUT_TYPE`, `BLOCKED_*`.
