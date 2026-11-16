# R1 reproduction guide (Windows gfx1100 host)

Everything below ran on the validated host (Win11 26300, RX 7900 XTX,
driver 32.0.21030.2001, WinML AMD GPU EP Framework 1.8.65.0,
onnxruntime-windowsml 1.30.1). No admin rights required anywhere.

## 0) Prerequisites

- repo checked out; venvs exist (`venv-winml` with onnxruntime-windowsml,
  `venv-cpu` with PyPI onnxruntime — see `scripts/setup-winml.ps1` from round 1)
- AMD EP package installed & Ready (WinML catalog `EnsureReady`), otherwise
  the catalog lookup in the children reports NotReady
- plain Python 3.12 x64 available for the ORT-free HIP probe

## 1) R1.1 independent HIP runtime probe (~1 min)

```
set R1_SOURCE_HEAD=<git rev-parse HEAD>
py -3.12 runner\r1\hip_probe_supervisor.py --run-id <RUN_ID>
```
Outputs in `reports\r1\<RUN_ID>\`: `hip_runtime_inventory.json`
(paths/SHA256/signatures), `hip_probe_M*.json` (per-cell receipts),
`hip_probe_matrix.csv`, separate `.stdout.log`/`.stderr.log` per cell.

Expected on this host: all four cells `HIP_SET_OK`, count=1,
negative-control ordinals returning 101.

## 2) R1.2 EP device audit + per-candidate runs (~3 min)

```
py -3.12 runner\r1\ep_device_supervisor.py --run-id <RUN_ID> --timeout 240
```
Enumerates every ep device (full identity dump incl. DxgiAdapterNumber/LUID),
then runs one fresh process per matching candidate and a two-device negative
control. `dll_load_audit.json` cross-checks which amdhip64 the EP mapped.

Expected here: 2 candidates per EP; Dxgi=1 → HIP101, Dxgi=0 →
PASS_AMD_GPU_EXECUTED_AND_PARITY; two-device → CPU-only fallback.

## 3) R1.3 #151 fallback matrix (~4 min)

```
py -3.12 runner\r1\probe151_matrix_supervisor.py --run-id <RUN_ID> --timeout 200
```
18 cells (A,B,C0,C1,D,E,F1,F2,F3 × AMDGPU/MIGraphX), stdout and stderr
captured separately. See `docs/ISSUE_151_SCOPE_AND_LIMITATIONS.md`.

## 4) Unit tests + stats (~10 s)

```
venv-cpu\Scripts\python.exe -m unittest discover -s tests
py -3.12 runner\r1\derive_stats.py --run-id <RUN_ID>
```

## 5) Using the finding in your own code

```python
from runner.r1.fail_closed import (
    GPUUnavailableError, create_session_fail_closed,
    assert_ep_executed, pick_device_by_dxgi_adapter)

dev = pick_device_by_dxgi_adapter(ort, "AMDGPUExecutionProvider", "0")
sess = create_session_fail_closed(ort, model, "AMDGPUExecutionProvider", device=dev)
stats = assert_ep_executed(sess, "AMDGPUExecutionProvider",
                           lambda s: s.run(None, {"x": x}))
```

## Interpreting receipts

Every receipt carries `schema_version` (`r1/1`), `run_id`, `started/finished_utc`,
`source_head`, argv, exit_code, `outcome`, and (where applicable) the exact
exception text, module list and profile node attribution. Outcomes are
classified strings — see the vocabulary in the module docstrings; a
`PASS_*` always implies profile node attribution by the EP and parity 0.
