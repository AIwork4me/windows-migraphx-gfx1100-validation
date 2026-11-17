# R2.1-D — SSD Mobilenet v1 whole-graph-CPU partition forensic analysis

Model: `ssd_mobilenet_v1_10.onnx` sha256
`1fbcf47654165f2e0b5f1bdf3f123b9e9e1128cd6463717767b76ab4b5246f9a`
(opset 10, ir 5, 350 top-level nodes, 2 Loop subgraphs, input
`image_tensor:0` NHWC **uint8** with dynamic `unk__*` dims; outputs:
boxes/classes/scores/num_detections). Input fed: 1×300×300×3 uint8
(seed 2026), same as R2 (R2 receipt input_shapes confirms).

## D1 — Reproduction (same host/EP package/driver, R2.1 run)

| case | requested EP | opt | classification | nodes_by_provider |
|---|---|---|---|---|
| ssd×AMDGPU | AMDGPUExecutionProvider | ALL | FAIL_NO_GPU_COMPUTE | {CPU: 5587} |
| ssd×MIGraphX | MIGraphXExecutionProvider | ALL | FAIL_NO_GPU_COMPUTE | {CPU: 5587} |
| ssd×AMDGPU | AMDGPUExecutionProvider | DISABLE_ALL | FAIL_NO_GPU_COMPUTE | {CPU: 6169} |
| ssd×AMDGPU | AMDGPUExecutionProvider | BASIC | FAIL_NO_GPU_COMPUTE | {CPU: 5614} |
| ssd×AMDGPU | AMDGPUExecutionProvider | EXTENDED | FAIL_NO_GPU_COMPUTE | {CPU: 5579} |

All four optimization levels and both AMD EPs reproduce R2 exactly
(R2 receipts: 5,587 CPU events, 0 EP nodes, EP present in
session_providers). DML same-process control: ERROR_RUNTIME
(`UnicodeDecodeError` raised inside `sess.run` with DML+profiling on this
ORT-windowsml build) — recorded as DML_CONTROL_ERROR; the historical
DML-success run was a different process context and is context only.

## The mechanism is NOT a capability/partition rejection

stdout evidence captured for every failing case (and present in the R2
frozen receipts' own stdout logs, now connected):

```
EP Error: [ONNXRuntimeError] : 11 : EP_FAIL : Non-zero status code returned
while running AMDGPUExecutionProvider_2033424792886208047_0 node.
Name:'AMDGPUExecutionProvider_AMDGPUExecutionProvider_2033424792886208047_0_0'
Status Message: Failed to call function using ['AMDGPUExecutionProvider',
'CPUExecutionProvider']
Falling back to ['CPUExecutionProvider'] and retrying.
```

Interpretation (evidence-bounded): the EP's capability check ACCEPTED the
graph — a single whole-graph fused kernel (`..._2033424792886208047_0`) was
created and named after the EP. At `run()` the kernel invocation returned
EP_FAIL; the plugin-EP layer caught it, rebuilt the session CPU-only, and
re-ran. Hence 0 EP nodes and 5,587 CPU events **is the post-fallback CPU
execution**, not proof that the EPs never claimed the graph. MIGraphX
stderr additionally shows it parsed the Loop subgraphs and clamped their
`max_iterations` from 9223372036854775807 (INT64_MAX) to 65535 — i.e. it
compiled the control flow rather than rejecting it.

CPU op distribution (post-fallback events; 5,587 events ≠ 5,587 unique
ops — Loop-body expansion inflates counts):
Cast 964, Gather 558, Min 452, Max 360, Mul 308, Reshape 307, Squeeze 305,
Slice 296, Add 290, Concat 221, Unsqueeze 209, Sub 194, Split 185,
Greater 183, Transpose 182, NonZero 180, Shape 110, Equal 92,
NonMaxSuppression 90, Conv 47, ReorderOutput 18, Tile 11, Less 4,
ConstantOfShape 4, Div 3.

## D2 — Barrier isolation with minimal checker-validated fixtures

Fixtures (`artifacts/models/ssd_partition_fixtures/`, opset 10 like SSD,
all run in separate processes with the fail-closed resolver + canary):

| fixture | graph | AMDGPU | MIGraphX | stdout evidence |
|---|---|---|---|---|
| static_conv3 | Conv→Relu→Conv→Relu, static 1×3×8×8 | **PASS_GPU** ({EP:1}) | **PASS_GPU** ({EP:1}) | none |
| dynamic_conv3 | same, H/W dynamic (dim_param) | **PASS_GPU** ({EP:1}) | **PASS_GPU** ({EP:1}) | none |
| static_loop | single Loop (trip=3, body=Add const), static shapes | CPU-only {CPU:7} | CPU-only {CPU:7} | EP_FAIL + Falling back |
| static_conv_loop | Conv→(padding)→Loop | CPU-only {CPU:9} | CPU-only {CPU:9} | EP_FAIL + Falling back |

Conclusions:

1. **Dynamic input dims are NOT the barrier** (dynamic_conv3 runs on both
   EPs as one fused kernel).
2. **An opset-10 Loop (control-flow subgraph) IS the barrier**, and by the
   SAME mechanism as full SSD: the EP compiles it into its fused kernel,
   the runtime invocation fails (EP_FAIL), and ORT falls back to a
   CPU-only session for the whole graph. A single Loop drags the Conv work
   off the GPU with it (static_conv_loop) — which is exactly why SSD shows
   0 EP nodes rather than a partial partition.

## D3 — Verdict, workaround path, and the next minimal upstream question

**Verdict: `LOOP_BARRIER_ISOLATED` (mechanism: compile-accept → runtime
EP_FAIL → session-level CPU fallback)**, reproduced at 4-node scale on
both AMD EPs, matching full-SSD behavior byte-for-byte in mechanism. The
in-kernel reason the compiled Loop kernel fails to invoke is NOT isolated:
the EP ships as a closed MSIX binary; no symbols, no native toolchain
(blocked), and no ORT verbose capability log exposes it. Honest label for
that inner layer: `KERNEL_FAILURE_CAUSE_NOT_ISOLATED`.

Repro recipe (3 commands, any host with the MSIX EP 1.8.65.0):

```
python runner/r2_1/ssd_build_fixtures.py          # builds static_loop.onnx (4 nodes)
python runner/r2_1/ssd_partition_child.py --model artifacts/models/ssd_partition_fixtures/static_loop.onnx \
    --ep AMDGPUExecutionProvider --opt ALL --out out.json
# observe: stdout 'EP Error ... EP_FAIL ... Falling back', out.json {CPU:7}
```

Numerical workaround for users needing GPU on SSD-class graphs: none at
the ONNX graph level short of eliminating control flow (e.g., exporting
the model with Loop subgraphs unfolded to static ops before EP handoff);
CPU fallback preserves numerics (R2 parity all_ok on CPU outputs), so
correctness is unaffected — only the GPU is idle. Re-export with a static
NMS (NonMaxSuppression is already a plain op — 90 CPU events post-fallback)
is the plausible transform, but producing/validating such a re-export is
outside this run and remains a suggestion, not a tested fix.

Next minimal upstream question (for a future authorized round, NOT filed
now): does `amdgpu-ep`'s kernel invocation path support ONNX control-flow
subgraphs (Scan/Loop) at all, and should its capability check reject them
so ORT partitions around the Loop instead of losing the whole graph to a
post-compile runtime failure? Evidence to attach: this fixture pair +
stdout transcripts.
