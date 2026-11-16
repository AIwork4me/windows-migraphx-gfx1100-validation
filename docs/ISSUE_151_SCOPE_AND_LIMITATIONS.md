# Issue #151 scope and limitations — silent CPU fallback on Windows plugin EPs

Upstream: https://github.com/onnxruntime/onnxruntime-ep-amdgpu/issues/151
Environment under test: onnxruntime-windowsml 1.30.1.202610302006,
WinML AMD GPU EP Framework 1.8.65.0, Windows 11 26300, RX 7900 XTX.
Evidence: `reports/r1/r1_20261116T232607Z/probe151_*.{json,stdout.log,stderr.log}`
+ `fallback_behavior_matrix.csv` (18 cells: A,B,C0,C1,D,E,F1,F2,F3 × 2 EPs,
each a fresh process, stdout AND stderr captured — round-1 m5 used DEVNULL
and lost the diagnostic output).

## Measured behavior matrix (AMDGPUExecutionProvider / MIGraphXExecutionProvider)

| Cell | Configuration | AMDGPU EP | MIGraphX EP |
|---|---|---|---|
| A | **unregistered** + classic `providers=[ep,'CPUExecutionProvider']` | CPU-only session; **UserWarning on stderr** ("not in available provider names") + ORT "EP Error / Falling back to ['CPUExecutionProvider']" block **on stdout** | same |
| B | **registered** + classic `providers=[ep,'CPUExecutionProvider']` | EP engaged → **HIP 101** (ordinal bug, see root-cause report) | **silent drop**: CPU-only session, **no warning, no exception** |
| C0 | registered + device API, candidate Dxgi=1 | HIP 101 | HIP 101 |
| C1 | registered + device API, candidate Dxgi=0 | **PASS AMD GPU executed + parity** | **PASS AMD GPU executed + parity** |
| D | registered + device API, **two** devices | factory `INVALID_ARGUMENT "only supports selection for a single device"`; ORT catches it, prints EP Error block (**stdout**) + retry with `['CPUExecutionProvider']`; Python adds UserWarning; final session CPU-only | same |
| E | explicit `providers=['CPUExecutionProvider']` | clean CPU (control) | clean CPU |
| F1 | registered, `providers` arg not passed (plain `InferenceSession(model)`) | CPU-only, no warning | CPU-only, no warning |
| F2 | registered, `providers=[]` | CPU-only, no warning | CPU-only, no warning |
| F3 | registered, `providers=[ep]` (no CPU entry) | EP engaged → HIP 101 | silent drop → CPU-only, no warning |

## Precise statements (what #151-class behavior is and is not)

1. The drop is **never a hard error**: every fallback path yields a working
   CPU session. Callers who only check `sess.run()` output cannot tell.
2. It is **not fully silent** in most cells, but the signals are split across
   two streams people usually discard: the ORT "EP Error … Falling back"
   block is printed on **stdout**; Python `UserWarning`s go to **stderr**.
   Round-1's "zero warning" observation was a capture artifact (DEVNULL).
3. The genuinely warning-free cases on this build:
   - registered MIGraphX EP + classic providers (B/F3): no stdout block,
     no stderr warning, CPU-only session.
   - F1/F2 shape (no/empty providers): arguably caller intent, no signals.
4. `get_available_providers()` ≠ `get_ep_devices()` ≠ session
   `get_providers()`: after registration AMDGPU appears in none of the
   first two until enumeration, but MIGraphX-classic still doesn't reach the
   session — the three APIs answer different questions and were recorded
   separately in the receipts.
5. Multi-device selection (D) is rejected by the AMD EP factory by design
   ("only supports selection for a single device" in
   `gpu_factory.cc CreateEp`); ORT's plugin wrapper converts that into the
   stdout EP Error + CPU retry rather than raising.

## Documented-limitation vs bug (read-only assessment, no upstream action taken)

- ORT core's classic-providers semantics (append_provider for unknown names
  being ignored with at most a warning) is **documented behavior** of the
  Python API; the fix for applications is fail-closed checking, not a core
  change. Our helper: `runner/r1/fail_closed.py` (+ unit tests).
- The AMD EP factory's single-device restriction is explicit in source and
  error text — a limitation, reasonably surfaced.
- **Candidate upstream issue (not filed)**: the AMDGPU EP engaging with
  device_id derived from `DxgiAdapterNumber` (HIP 101 on multi-adapter
  hosts) is a genuine defect with a clean repro; see
  `docs/R1_UPSTREAM_READINESS.md` for the minimal statement we would file
  with user authorization.

## Reproducing any cell

```
venv-winml\Scripts\python.exe runner\r1\probe151_matrix_child.py --cell A --ep AMDGPUExecutionProvider --out %TEMP%\cellA.json --run-id manual
```
stdout/stderr of the child contain the exact captured signals.
