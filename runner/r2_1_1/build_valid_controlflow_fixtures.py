#!/usr/bin/env python3
"""R2.1.1-P1: build CONTRACT-VALID control-flow fixtures with CPU oracles.

Supersedes runner/r2_1/ssd_build_fixtures.py (kept verbatim for history).
The R2.1 static_loop fixture violated the ONNX Loop contract twice:

  * outer initial carried value X had shape [3,1,4,4] while the body's
    loop-carried input b_seq was declared [1,1,4,4]  -> runtime
    "Incorrect shape for b_seq" is a *fixture* defect, not EP evidence;
  * the (single) Loop output Y was declared ['scan_len',1,4,4], i.e. as a
    scan output, but the body had NO scan outputs -- with M carried
    inputs and K=0 scan outputs Loop produces exactly M final-carried
    outputs whose shape equals the carried shape.

Fixtures built here (opset 13, ir_version 8 -- Loop-13/NonZero-13 are the
versions ORT 1.30+/MIGraphX EP actually target; opset-10 Loop does not
exist in ai.onnx schema at all, see PROVENANCE):

  valid_loop_carried.onnx      X[1,1,4,4], trip=3, body adds 0.01/iter;
                               Y(final carried)=X+0.03.            [carried-only]
  valid_loop_scan.onnx         same body + a real scan output s=b_seq;
                               Z(final)=X+0.03, Y(scan)=[3,1,1,4,4]
                               stacked per-iteration values.       [scan pair]
  valid_conv_then_loop.onnx    Conv(3x3,pad1)->[1,1,8,8] carried;
                               Y=conv(X)+0.03.                     [mixed]
  valid_nonzero_static.onnx    NonZero on static [4,5] input;
                               Y int64 [2,N], N dynamic.           [dynamic-out]
  valid_nonzero_dynamic.onnx   NonZero on [h,w] dynamic input dims.[dynamic-in+out]
  valid_loop_plus_nonzero.onnx valid_loop_carried -> NonZero;
                               Z int64 [4,N].                      [combination]

Each fixture ships independent NumPy oracle lambdas (see oracle fn table in
cpu_oracle_r211.py) and is verified by onnx.checker + shape_inference +
CPUExecutionProvider execution on multiple seeded inputs before any GPU
case may reference it. A fixture that fails CPU is INVALID_FIXTURE and is
never used to attribute EP behavior.
"""
from __future__ import annotations

import hashlib
import json
import os

import numpy as np
import onnx
from onnx import TensorProto, helper

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT_DIR = os.path.join(REPO, "artifacts", "models", "r2_1_1_fixtures")
OPSET = 13
IR = 8
F = TensorProto.FLOAT
I64 = TensorProto.INT64
B = TensorProto.BOOL


def _save(model, name):
    path = os.path.join(OUT_DIR, name)
    onnx.save(model, path)
    return path


def _mk(name, nodes, inputs, outputs, inits):
    graph = helper.make_graph(nodes, name, inputs, outputs, inits)
    model = helper.make_model(
        graph, opset_imports=[helper.make_opsetid("", OPSET)],
        producer_name="r2_1_1_valid_controlflow")
    model.ir_version = IR
    onnx.checker.check_model(model)
    onnx.shape_inference.infer_shapes(model, strict_mode=False)
    return model


def _scalar_i64(name, v):
    return helper.make_tensor(name, I64, [],
                              int(v).to_bytes(8, "little"), raw=True)


def _scalar_bool(name, v):
    return helper.make_tensor(name, B, [], bytes([1 if v else 0]), raw=True)


def _loop_body(spatial: int, add_per_iter: float, with_scan: bool):
    """Body: (iter, cond, seq[1,1,s,s]) -> (cond_out, seq_out[, scan_out])."""
    nodes = [
        helper.make_node("Identity", ["b_cond"], ["b_cond_out"]),
        helper.make_node("Add", ["b_seq", "one"], ["b_out"]),
    ]
    outputs = [
        helper.make_tensor_value_info("b_cond_out", B, []),
        helper.make_tensor_value_info("b_out", F, [1, 1, spatial, spatial]),
    ]
    if with_scan:
        nodes.append(helper.make_node("Identity", ["b_seq"], ["b_scan"]))
        outputs.append(helper.make_tensor_value_info(
            "b_scan", F, [1, 1, spatial, spatial]))
    body = helper.make_graph(
        nodes, "body",
        [helper.make_tensor_value_info("b_iter", I64, []),
         helper.make_tensor_value_info("b_cond", B, []),
         helper.make_tensor_value_info(
             "b_seq", F, [1, 1, spatial, spatial])],
        outputs,
        [helper.make_tensor("one", F, [1, 1, 1, 1],
                            np.full((1, 1, 1, 1), add_per_iter,
                                    np.float32).tobytes(), raw=True)])
    return body


def valid_loop_carried() -> tuple[str, str]:
    body = _loop_body(4, 0.01, with_scan=False)
    nodes = [helper.make_node(
        "Loop", ["trip", "cond_true", "X"], ["Y"], body=body, name="loop1")]
    m = _mk("valid_loop_carried", nodes,
            [helper.make_tensor_value_info("X", F, [1, 1, 4, 4])],
            [helper.make_tensor_value_info("Y", F, [1, 1, 4, 4])],
            [_scalar_i64("trip", 3), _scalar_bool("cond_true", True)])
    return _save(m, "valid_loop_carried.onnx"), "Y=X+0.03"


def valid_loop_scan() -> tuple[str, str]:
    body = _loop_body(4, 0.01, with_scan=True)
    nodes = [helper.make_node(
        "Loop", ["trip", "cond_true", "X"], ["Z", "Y"], body=body,
        name="loop_scan")]
    m = _mk("valid_loop_scan", nodes,
            [helper.make_tensor_value_info("X", F, [1, 1, 4, 4])],
            [helper.make_tensor_value_info("Z", F, [1, 1, 4, 4]),
             helper.make_tensor_value_info("Y", F, ["trip", 1, 1, 4, 4])],
            [_scalar_i64("trip", 3), _scalar_bool("cond_true", True)])
    return _save(m, "valid_loop_scan.onnx"), "Z=X+0.03; Y=stack(X+.01k,k=1..3)"


def valid_conv_then_loop() -> tuple[str, str]:
    body = _loop_body(8, 0.01, with_scan=False)
    W = np.full((1, 1, 3, 3), 0.05, dtype=np.float32)
    nodes = [
        helper.make_node("Conv", ["X", "W1", "B1"], ["C1"],
                         kernel_shape=[3, 3], pads=[1, 1, 1, 1], strides=[1, 1]),
        helper.make_node("Loop", ["trip", "cond_true", "C1"], ["Y"],
                         body=body, name="loop_after_conv"),
    ]
    m = _mk("valid_conv_then_loop", nodes,
            [helper.make_tensor_value_info("X", F, [1, 1, 8, 8])],
            [helper.make_tensor_value_info("Y", F, [1, 1, 8, 8])],
            [helper.make_tensor("W1", F, W.shape, W.tobytes(), raw=True),
             helper.make_tensor("B1", F, [1],
                                np.zeros(1, np.float32).tobytes(), raw=True),
             _scalar_i64("trip", 3), _scalar_bool("cond_true", True)])
    return _save(m, "valid_conv_then_loop.onnx"), "Y=conv3x3(X)+0.03"


def valid_nonzero_static() -> tuple[str, str]:
    nodes = [helper.make_node("NonZero", ["X"], ["Y"])]
    m = _mk("valid_nonzero_static", nodes,
            [helper.make_tensor_value_info("X", F, [4, 5])],
            [helper.make_tensor_value_info("Y", I64, [2, "nz_count"])], [])
    return _save(m, "valid_nonzero_static.onnx"), "Y=indices(X!=0)"


def valid_nonzero_dynamic() -> tuple[str, str]:
    nodes = [helper.make_node("NonZero", ["X"], ["Y"])]
    m = _mk("valid_nonzero_dynamic", nodes,
            [helper.make_tensor_value_info("X", F, ["h", "w"])],
            [helper.make_tensor_value_info("Y", I64, [2, "nz_count"])], [])
    return _save(m, "valid_nonzero_dynamic.onnx"), "Y=indices(X!=0)"


def valid_loop_plus_nonzero() -> tuple[str, str]:
    body = _loop_body(4, 0.01, with_scan=False)
    nodes = [
        helper.make_node("Loop", ["trip", "cond_true", "X"], ["V"],
                         body=body, name="loop1"),
        helper.make_node("NonZero", ["V"], ["Z"]),
    ]
    m = _mk("valid_loop_plus_nonzero", nodes,
            [helper.make_tensor_value_info("X", F, [1, 1, 4, 4])],
            [helper.make_tensor_value_info("Z", I64, [4, "nz_count"])],
            [_scalar_i64("trip", 3), _scalar_bool("cond_true", True)])
    return _save(m, "valid_loop_plus_nonzero.onnx"), "Z=indices(X+0.03!=0)"


def valid_nonzero_in_loop() -> tuple[str, str]:
    """SSD-isomorphic: NonZero INSIDE the loop body (SSD's NMS loops run
    NonZero per iteration). Body counts nonzero elements of the carried
    input per iteration; all body output shapes are static (scalar), so
    the graph is contract-valid. Loop outputs: Z = final carried
    [1,1,4,4]; S = scan [3] int64 per-iteration nonzero counts."""
    body_nodes = [
        helper.make_node("Identity", ["b_cond"], ["b_cond_out"]),
        helper.make_node("Add", ["b_seq", "one"], ["b_out"]),
        helper.make_node("NonZero", ["b_seq"], ["nz"]),
        helper.make_node("Shape", ["nz"], ["nz_shape"]),
        helper.make_node("Gather", ["nz_shape", "g_idx"], ["nz_n1"]),
        helper.make_node("Squeeze", ["nz_n1", "sq_axes"], ["b_count"]),
    ]
    body = helper.make_graph(
        body_nodes, "body_nz",
        [helper.make_tensor_value_info("b_iter", I64, []),
         helper.make_tensor_value_info("b_cond", B, []),
         helper.make_tensor_value_info("b_seq", F, [1, 1, 4, 4])],
        [helper.make_tensor_value_info("b_cond_out", B, []),
         helper.make_tensor_value_info("b_out", F, [1, 1, 4, 4]),
         helper.make_tensor_value_info("b_count", I64, [])],
        [helper.make_tensor("one", F, [1, 1, 1, 1],
                            np.full((1, 1, 1, 1), 0.01,
                                    np.float32).tobytes(), raw=True),
         helper.make_tensor("g_idx", I64, [1],
                            np.array([1], np.int64).tobytes(), raw=True),
         helper.make_tensor("sq_axes", I64, [1],
                            np.array([0], np.int64).tobytes(), raw=True)])
    nodes = [helper.make_node("Loop", ["trip", "cond_true", "X"], ["Z", "S"],
                              body=body, name="loop_nonzero")]
    m = _mk("valid_nonzero_in_loop", nodes,
            [helper.make_tensor_value_info("X", F, [1, 1, 4, 4])],
            [helper.make_tensor_value_info("Z", F, [1, 1, 4, 4]),
             helper.make_tensor_value_info("S", I64, ["trip"])],
            [_scalar_i64("trip", 3), _scalar_bool("cond_true", True)])
    return _save(m, "valid_nonzero_in_loop.onnx"), \
        "Z=X+0.03; S=[count(X!=0),count(X+.01!=0),count(X+.02!=0)]"


BUILDERS = {
    "valid_loop_carried.onnx": valid_loop_carried,
    "valid_loop_scan.onnx": valid_loop_scan,
    "valid_conv_then_loop.onnx": valid_conv_then_loop,
    "valid_nonzero_static.onnx": valid_nonzero_static,
    "valid_nonzero_dynamic.onnx": valid_nonzero_dynamic,
    "valid_loop_plus_nonzero.onnx": valid_loop_plus_nonzero,
    "valid_nonzero_in_loop.onnx": valid_nonzero_in_loop,
}


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    reg = {"schema": "r2_1_1/fixture_registry/1",
           "opset": OPSET, "ir_version": IR,
           "supersedes": {
               "path": "runner/r2_1/ssd_build_fixtures.py",
               "defect": ("static_loop carried shape [3,1,4,4] != body b_seq "
                          "[1,1,4,4]; output declared with scan_len but body "
                          "has no scan outputs; opset 10 has no Loop schema"),
               "invalid_fixture_hashes": {}}}
    old_dir = os.path.join(REPO, "artifacts", "models", "ssd_partition_fixtures")
    for old in ("static_loop.onnx", "static_conv_loop.onnx"):
        p = os.path.join(old_dir, old)
        if os.path.isfile(p):
            h = hashlib.sha256(open(p, "rb").read()).hexdigest()
            reg["supersedes"]["invalid_fixture_hashes"][old] = h
    reg["fixtures"] = {}
    for name, fn in BUILDERS.items():
        path, contract = fn()
        m = onnx.load(path)
        reg["fixtures"][name] = {
            "path": os.path.relpath(path, REPO).replace("\\", "/"),
            "sha256": hashlib.sha256(open(path, "rb").read()).hexdigest(),
            "contract": contract,
            "ops": [n.op_type for n in m.graph.node],
            "checker": "PASS",
            "shape_inference": "PASS",
        }
        print(f"{name:32s} ops={reg['fixtures'][name]['ops']} ok")
    out = os.path.join(REPO, "reports", "r2_1_1",
                       __import__("sys").argv[1] if len(__import__("sys").argv) > 1
                       else "", "FIXTURE_REGISTRY.json")
    out = os.path.normpath(out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(reg, f, indent=1)
    print("wrote", out)


if __name__ == "__main__":
    main()
