# R2.1.1 — SSD Causality Table (contract-valid fixtures, real RX 7900 XTX)

Run: `r2_1_1_20261117T114559Z` · host Windows 11 · RX 7900 XTX (gfx1100,
LUID 72346, fail-closed resolver + canary, `add_provider_for_devices`) ·
ORT-winml 1.30.1 · MSIX `Microsoft.WinML.AMD.GPU.EP.Framework.2 1.8.65.0`.

All 28 GPU cases executed in separate subprocesses with per-case receipts
(`gpu_matrix/*.json`), raw stdout/stderr logs, and ORT profile attribution.
GPU PASS = target EP in session providers AND `nodes_by_provider[ep] > 0`
AND by-name per-output parity vs a CPU session of the SAME ORT build.

## Matrix (7 fixtures × 2 EPs × {DISABLE_ALL, ALL} = 28)

| fixture | isolates | AMDGPU EP | MIGraphX EP |
|---|---|---|---|
| valid_loop_carried | static-shape carried-only Loop | **PASS_GPU_EXECUTED** ×2 | **PASS_GPU_EXECUTED** ×2 |
| valid_loop_scan | Loop **scan outputs** | FAIL_CORRECTNESS ×2 (3 EP nodes, Y wrong) | FAIL_CORRECTNESS ×2 |
| valid_conv_then_loop | Conv feeding a carried-only Loop | FAIL_NO_GPU_COMPUTE ×2 (EP_FAIL→fallback, `Incorrect shape b_seq` strides `{64,1,8,1}` vs packed `{64,64,8,1}`) | FAIL_NO_GPU_COMPUTE ×2 (same) |
| valid_nonzero_static | standalone NonZero, static input | FAIL_CORRECTNESS ×2 (executes; returns **fixed count** `[2,20]` even for all-zero input that must be `[2,0]`) | FAIL_CORRECTNESS ×2 |
| valid_nonzero_dynamic | NonZero with dynamic input dims | FAIL_CORRECTNESS ×2 | FAIL_CORRECTNESS ×2 |
| valid_loop_plus_nonzero | Loop followed by NonZero (top level) | FAIL_CORRECTNESS ×2 (2 EP nodes; Z wrong via NonZero defect) | FAIL_CORRECTNESS ×2 |
| valid_nonzero_in_loop | **NonZero INSIDE Loop body** (SSD-isomorphic) | FAIL_CORRECTNESS ×2 (2 EP nodes; carried Z correct, scan counts S wrong **and shape `[3,1]` vs `[3]`**) | FAIL_CORRECTNESS ×2 |

SSD full graph re-run under R2.1.1 provenance (`ssd_full/`): both EPs
FAIL_NO_GPU_COMPUTE with `EP Error … EP_FAIL … Falling back to
['CPUExecutionProvider']`; MIGraphX stderr reproduces
`check_shapes.hpp:90: check_dynamic: nonzero: Dynamic shapes not supported`.

## Causal chain for ssd_mobilenet_v1_10 (opset 10)

1. The model's NMS post-processing is 2 top-level Loops whose **first
   loop body contains all 180 NonZero ops** (per-class filtering,
   tf2onnx lowering). Verified by subgraph walk over the frozen model
   sha256 `1fbcf476…`.
2. The EP **accepts** the whole graph (capability check passes, single
   fused kernel created; PARSE_LOOP clamps max_iterations to 65535).
3. At run() the compiled kernel trips MIGraphX `check_dynamic: nonzero`
   (dynamic shapes not supported) → EP_FAIL → ORT plugin layer falls
   back to a whole-session CPU rerun (5,587 CPU node events, 0 EP).
4. Minimal fixtures localize the barrier precisely:
   - a **contract-valid carried-only Loop PASSES on GPU** on both EPs →
     R2.1's "an opset-10 Loop IS the barrier" is **REVOKED** (below);
   - **NonZero is defective on both EPs** — it executes but returns a
     fixed-size index tensor regardless of the true nonzero count
     (all-zero input still yields `[2,20]`), and inside a Loop its
     dynamic count poisons the scan output (wrong shape and values);
   - an additional, separate EP defect: Conv→Loop (carried-only, valid)
     fails inside parse_loop with an internal **stride/layout mismatch
     on b_seq** (`{64,1,8,1}` vs packed `{64,64,8,1}`) → same
     EP_FAIL→fallback signature as full SSD.

## Corrections to R2.1 statements (errata E-R2.1.1-1..3, see R211_ERRATA)

- **E-R2.1.1-1 (revocation)**: "An opset-10 Loop (control-flow subgraph)
  IS the barrier" is wrong as stated. A contract-valid static-shape
  carried-only Loop executes correctly on both AMD EPs (3 EP node
  events, exact parity). The R2.1 `static_loop` fixture was itself
  contract-invalid (outer carried `[3,1,4,4]` vs body `b_seq [1,1,4,4]`;
  output declared with a scan dim although the body has no scan outputs;
  opset 10 has no Loop schema at all), so its failure could not license
  a claim about Loop per se.
- **E-R2.1.1-2 (re-attribution)**: the full-SSD whole-graph-CPU outcome
  is attributable to **NonZero's dynamic-shape handling inside the NMS
  Loop** (`check_dynamic: nonzero`, reproduced in SSD stderr) — not to
  Loop control flow itself.
- **E-R2.1.1-3 (new defect classes, previously unreported)**: (a) Loop
  **scan outputs** return wrong values while the final carried output is
  exact (silent wrong results, no fallback — worse than a fallback for
  correctness); (b) standalone NonZero returns a fixed-size output
  independent of the true count; (c) Conv→Loop hits an EP-internal
  parse_loop stride error on the carried input. All three reproduce on
  both EPs at both optimization levels.

## Honest labels

- `LOOP_CARRIED_STATIC_SUPPORTED` (both EPs)
- `LOOP_SCAN_OUTPUT_INCORRECT` (both EPs)
- `NONZERO_FIXED_COUNT_INCORRECT` (both EPs)
- `CONV_TO_LOOP_PARSE_STRIDE_FAIL` (both EPs)
- `SSD_FULL_GRAPH = NONZERO_IN_LOOP + CONV_TO_LOOP defect families
  → compile-accept → runtime EP_FAIL → session-level CPU fallback`
  (mechanism as R2.1 D2, attribution corrected)

## Reproduction

```
python runner/r2_1_1/build_valid_controlflow_fixtures.py <run_id>
python runner/r2_1_1/cpu_oracle_r211.py <run_id>            # 19/19 PASS
python runner/r2_1_1/gpu_matrix_driver.py <run_id>          # 28 cases
```
