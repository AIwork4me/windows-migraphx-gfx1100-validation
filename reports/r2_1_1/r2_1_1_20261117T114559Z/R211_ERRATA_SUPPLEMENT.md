# R2.1 Errata Supplement (issued by R2.1.1, run r2_1_1_20261117T114559Z)

The historical `reports/r2_1/r21_20261117T040739Z/` receipts are frozen
and remain unchanged; this supplement CORRECTS statements, it does not
rewrite evidence. Corrections E-R2.1.1-1..3 supersede the corresponding
R2.1 conclusions. Full matrix: `SSD_CAUSALITY_TABLE.md` in this dir.

## E-R2.1.1-1 — REVOKED: "an opset-10 Loop IS the barrier"

R2.1 `SSD_PARTITION_RCA.md` §D2 conclusion 2 stated an opset-10 Loop is
the partition barrier, based on `static_loop.onnx` failing on both EPs.

Correction: `static_loop.onnx` violated the ONNX Loop contract
(outer carried `[3,1,4,4]` vs body `b_seq [1,1,4,4]`; the single output
declared with a `scan_len` dim although the body has no scan outputs;
and ai.onnx opset 10 defines no Loop operator at all), so its failure
was consistent with a fixture defect. The contract-valid
`valid_loop_carried.onnx` (opset 13, static shapes, carried-only) **PASSES
on both AMD EPs** (3 EP node events, exact parity, both DISABLE_ALL and
ALL). Loop control flow alone is NOT a barrier.

## E-R2.1.1-2 — RE-ATTRIBUTED: full-SSD whole-graph CPU

R2.1 attributed the SSD whole-graph-CPU outcome to Loop control flow.

Correction: ssd_mobilenet_v1_10 places **all 180 NonZero ops inside its
first NMS Loop body** (subgraph walk, frozen model sha256 `1fbcf476…`).
MIGraphX stderr of the same run already carried
`check_shapes.hpp:90: check_dynamic: nonzero: Dynamic shapes not
supported`. Contract-valid minimal fixtures show NonZero is the defective
family on both EPs: standalone NonZero executes but returns a **fixed-size
index tensor regardless of the true nonzero count** (an all-zero input
must produce `[2,0]`; the EP returns `[2,20]`), and NonZero inside a Loop
body yields wrong per-iteration counts and a wrong scan shape. Loop+the
NonZero defect — not Loop per se — explains the SSD
compile-accept → runtime EP_FAIL → session-level CPU fallback mechanism
(which R2.1 §D2 described correctly and is retained).

## E-R2.1.1-3 — NEW defect classes (not previously reported)

All reproduce on both EPs at both optimization levels (28-case matrix):

1. `LOOP_SCAN_OUTPUT_INCORRECT` — a valid Loop with scan outputs
   executes on the EP but returns wrong scan values (max_abs ≈ data
   magnitude) while its final carried output is exact. Silent wrong
   results without fallback.
2. `NONZERO_FIXED_COUNT_INCORRECT` — see E-R2.1.1-2.
3. `CONV_TO_LOOP_PARSE_STRIDE_FAIL` — Conv feeding a carried-only Loop
   (contract-valid) fails inside the EP with
   `Incorrect shape {float_type,{1,1,8,8},{64,1,8,1}} for parameter:
   b_seq should be: {1,1,8,8},{64,64,8,1}` — an EP-internal parse_loop
   stride/layout mismatch — followed by the same EP_FAIL→CPU-fallback
   signature as full SSD.

## Impact on prior verdicts

- R2.1 `LOOP_BARRIER_ISOLATED` → superseded by
  `NONZERO_DYNAMIC_SHAPE_DEFECT` (+ `CONV_TO_LOOP_PARSE_STRIDE_FAIL`).
- R2.1 `KERNEL_FAILURE_CAUSE_NOT_ISOLATED` → narrowed: the failing kernel
  family is now localized to NonZero dynamic-shape handling (and the
  separate Conv→Loop stride bug); inner MIGraphX instruction still not
  isolated (closed MSIX binary).
- R2's 234/290 matrix and R2.1's soak verdict are unaffected.
