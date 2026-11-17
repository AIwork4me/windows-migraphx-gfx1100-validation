#!/usr/bin/env python3
"""R2.1-D: build minimal fixture models isolating SSD partition barriers.

Hypotheses for ssd_mobilenet_v1_10 whole-graph-CPU on both AMD EPs:
  H-loop:   opset-10 Loop (control-flow subgraph) is not supported by the
            EPs; if the barrier were only "one unsupported op", ORT would
            still place the surrounding Conv chains on the EP, so a whole-
            graph CPU result needs a stronger explanation.
  H-dyn:    the model input has dynamic dims (dim_param 'unk__*'); if the
            EPs require static shapes, every node's capability check fails.

Fixtures (all opset 10, matching SSD):
  static_conv3.onnx    Conv->Add(bias)->Relu chain, static 1x8x8 input
  dynamic_conv3.onnx   same graph, H/W declared dynamic (dim_param)
  static_loop.onnx     tiny Loop computing a running max over a sequence,
                       static shapes
  static_conv_loop.onnx  Conv chain + one Loop: does ONE Loop push the
                       Conv chain off the EP too?
"""
from __future__ import annotations

import os

import numpy as np
import onnx
from onnx import TensorProto, helper

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "artifacts", "models", "ssd_partition_fixtures")


def conv_node(inp, outp, wname, bname):
    n = onnx.helper.make_node("Conv", [inp, wname, bname], [outp],
                              kernel_shape=[3, 3], pads=[1, 1, 1, 1],
                              strides=[1, 1])
    return n


def make_conv_chain(name: str, dynamic: bool) -> str:
    path = os.path.join(OUT_DIR, name)
    W1 = np.full((4, 3, 3, 3), 0.05, dtype=np.float32)
    W2 = np.full((4, 4, 3, 3), 0.05, dtype=np.float32)
    B1 = np.zeros(4, dtype=np.float32)
    B2 = np.zeros(4, dtype=np.float32)
    h = "h" if dynamic else 8
    w = "w" if dynamic else 8
    x = helper.make_tensor_value_info("X", TensorProto.FLOAT, [1, 3, h, w])
    y = helper.make_tensor_value_info("Y", TensorProto.FLOAT, [1, 4, h, w])
    nodes = [
        conv_node("X", "C1", "W1", "B1"),
        onnx.helper.make_node("Relu", ["C1"], ["R1"]),
        conv_node("R1", "C2", "W2", "B2"),
        onnx.helper.make_node("Relu", ["C2"], ["Y"]),
    ]
    inits = [
        helper.make_tensor("W1", TensorProto.FLOAT, W1.shape, W1.tobytes(), raw=True),
        helper.make_tensor("B1", TensorProto.FLOAT, B1.shape, B1.tobytes(), raw=True),
        helper.make_tensor("W2", TensorProto.FLOAT, W2.shape, W2.tobytes(), raw=True),
        helper.make_tensor("B2", TensorProto.FLOAT, B2.shape, B2.tobytes(), raw=True),
    ]
    graph = helper.make_graph(nodes, name, [x], [y], inits)
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 10)],
                              producer_name="r21_ssd_partition")
    model.ir_version = 5
    onnx.save(model, path)
    return path


def make_loop(name: str, with_conv: bool) -> str:
    """Loop body: iterates over sequence dim, adds elementwise. If with_conv,
    a Conv runs BEFORE the loop inside the same graph."""
    path = os.path.join(OUT_DIR, name)
    # body graph: (iter, cond, seq[1,C,S,S]) -> (cond_out, seq_out)
    # body: seq_out = seq + 0.01 (constant, shape-static; keeps the fixture
    # about control flow, not about dynamic shapes inside the body)
    s = 8 if with_conv else 4
    body_nodes = [
        onnx.helper.make_node("Identity", ["b_cond"], ["b_cond_out"]),
        onnx.helper.make_node("Add", ["b_seq", "one"], ["b_out"]),
    ]
    b_iter = helper.make_tensor_value_info("b_iter", TensorProto.INT64, [])
    b_cond = helper.make_tensor_value_info("b_cond", TensorProto.BOOL, [])
    b_seq = helper.make_tensor_value_info("b_seq", TensorProto.FLOAT, [1, 1, s, s])
    b_cond_out = helper.make_tensor_value_info("b_cond_out", TensorProto.BOOL, [])
    b_out = helper.make_tensor_value_info("b_out", TensorProto.FLOAT, [1, 1, s, s])
    one = helper.make_tensor("one", TensorProto.FLOAT, [1, 1, 1, 1],
                             np.full((1, 1, 1, 1), 0.01, np.float32).tobytes(), raw=True)
    body = helper.make_graph(
        body_nodes, "body",
        [b_iter, b_cond, b_seq],
        [b_cond_out, b_out],
        [one])

    nodes = []
    inits = []
    ins = []
    if with_conv:
        W1 = np.full((1, 1, 3, 3), 0.05, dtype=np.float32)
        B1 = np.zeros(1, dtype=np.float32)
        nodes.append(conv_node("X", "C1", "W1", "B1"))
        inits += [
            helper.make_tensor("W1", TensorProto.FLOAT, W1.shape, W1.tobytes(), raw=True),
            helper.make_tensor("B1", TensorProto.FLOAT, B1.shape, B1.tobytes(), raw=True),
        ]
        seq_in = "C1"
        x = helper.make_tensor_value_info("X", TensorProto.FLOAT, [1, 1, 8, 8])
        ins.append(x)
    else:
        seq_in = "X"
        x = helper.make_tensor_value_info("X", TensorProto.FLOAT, [3, 1, 4, 4])
        ins.append(x)
    trip = helper.make_tensor("trip", TensorProto.INT64, [], (3).to_bytes(8, "little"), raw=True)
    cond_true = helper.make_tensor("cond_true", TensorProto.BOOL, [], (1).to_bytes(1, "little"), raw=True)
    inits += [trip, cond_true]
    nodes.append(onnx.helper.make_node(
        "Loop", ["trip", "cond_true", seq_in], ["Y"],
        body=body, name="loop1"))
    y = helper.make_tensor_value_info("Y", TensorProto.FLOAT, ["scan_len", 1, 4, 4]
                                      if not with_conv else ["scan_len", 1, 8, 8])
    graph = helper.make_graph(nodes, name, ins, [y], inits)
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 10)],
                              producer_name="r21_ssd_partition")
    model.ir_version = 5
    onnx.checker.check_model(model)
    onnx.save(model, path)
    return path


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    made = {
        "static_conv3.onnx": make_conv_chain("static_conv3.onnx", False),
        "dynamic_conv3.onnx": make_conv_chain("dynamic_conv3.onnx", True),
        "static_loop.onnx": make_loop("static_loop.onnx", with_conv=False),
        "static_conv_loop.onnx": make_loop("static_conv_loop.onnx", with_conv=True),
    }
    for name, path in made.items():
        m = onnx.load(path)
        print(name, "ops:", [n.op_type for n in m.graph.node],
              "opset:", m.opset_import[0].version, "ok")


if __name__ == "__main__":
    main()
