#!/usr/bin/env python3
"""R2.1.1-P1: CPU oracle for the valid control-flow fixtures.

Every fixture must PASS here (CPUExecutionProvider + independent NumPy
oracle, multiple seeded inputs incl. edge patterns: zero nonzero-count,
all-nonzero, negatives) before any GPU case may cite it. Output:
reports/r2_1_1/<run_id>/CPU_ORACLE.json
"""
from __future__ import annotations

import hashlib
import json
import os
import sys

import numpy as np
import onnx
import onnxruntime as ort

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIX_DIR = os.path.join(REPO, "artifacts", "models", "r2_1_1_fixtures")
ATOL = 1e-5


def conv2d_3x3(x: np.ndarray, w: np.ndarray) -> np.ndarray:
    """Independent conv oracle: x[1,1,H,W], w[1,1,3,3], pad=1, stride=1."""
    _, _, h, wd = x.shape
    xp = np.pad(x, ((0, 0), (0, 0), (1, 1), (1, 1)))
    out = np.zeros_like(x)
    for i in range(h):
        for j in range(wd):
            out[0, 0, i, j] = float(
                (xp[0, 0, i:i + 3, j:j + 3] * w[0, 0]).sum())
    return out.astype(np.float32)


def inputs_for(name: str) -> list[dict]:
    rng = np.random.default_rng(2117)
    if name == "valid_nonzero_static.onnx":
        a = rng.standard_normal((4, 5)).astype(np.float32)
        a[0, :] = 0.0                       # guarantee some zeros
        b = np.zeros((4, 5), np.float32)    # zero nonzero-count edge
        c = rng.standard_normal((4, 5)).astype(np.float32)  # all-nonzero-ish
        c[c == 0] = 1.0
        return [{"X": a}, {"X": b}, {"X": c}]
    if name == "valid_nonzero_dynamic.onnx":
        a = rng.standard_normal((6, 7)).astype(np.float32)
        a[2, :] = 0.0
        b = np.zeros((3, 4), np.float32)
        c = np.ones((2, 9), np.float32)
        return [{"X": a}, {"X": b}, {"X": c}]
    if name == "valid_loop_plus_nonzero.onnx":
        a = rng.standard_normal((1, 1, 4, 4)).astype(np.float32)
        b = np.full((1, 1, 4, 4), -0.03, np.float32)  # X+0.03 == 0 -> N=0
        return [{"X": a}, {"X": b}]
    if name == "valid_nonzero_in_loop.onnx":
        a = rng.standard_normal((1, 1, 4, 4)).astype(np.float32)
        # elements that cancel exactly at iteration k=1,2,3 (fp32 c + (-c)
        # == +0.0): per-iteration nonzero counts vary 16 -> 12 -> 12
        b = rng.standard_normal((1, 1, 4, 4)).astype(np.float32)
        b[0, 0, 0, 0:4] = np.float32(-0.01)
        b[0, 0, 1, 0:4] = np.float32(-0.02)
        b[0, 0, 2, 0:4] = np.float32(-0.03)
        return [{"X": a}, {"X": b}]
    # loop fixtures: standard_normal [1,1,4,4] / [1,1,8,8]
    shape = (1, 1, 8, 8) if "conv" in name else (1, 1, 4, 4)
    return [{"X": rng.standard_normal(shape).astype(np.float32) * 0.5}
            for _ in range(3)]


def oracle_for(name: str, feeds: dict) -> dict:
    x = feeds["X"]
    if name == "valid_loop_carried.onnx":
        return {"Y": (x + np.float32(0.03)).astype(np.float32)}
    if name == "valid_loop_scan.onnx":
        # b_scan = Identity(b_seq): scan collects the carried INPUT of each
        # iteration, i.e. [X, X+0.01, X+0.02]; final carried Z = X+0.03.
        zs = (x + np.float32(0.03)).astype(np.float32)
        stack = np.stack([(x + np.float32(0.01 * k)).astype(np.float32)
                          for k in (0, 1, 2)])
        return {"Z": zs, "Y": stack}
    if name == "valid_conv_then_loop.onnx":
        w = np.full((1, 1, 3, 3), 0.05, np.float32)
        c = conv2d_3x3(x, w)
        return {"Y": (c + np.float32(0.03)).astype(np.float32)}
    if name in ("valid_nonzero_static.onnx", "valid_nonzero_dynamic.onnx"):
        idx = np.nonzero(x)               # tuple of per-dim index arrays
        return {"Y": np.stack(idx).astype(np.int64)}
    if name == "valid_loop_plus_nonzero.onnx":
        v = (x + np.float32(0.03)).astype(np.float32)
        return {"Z": np.stack(np.nonzero(v)).astype(np.int64)}
    if name == "valid_nonzero_in_loop.onnx":
        zs = (x + np.float32(0.03)).astype(np.float32)
        counts = [int(np.count_nonzero(
            (x + np.float32(0.01 * k)).astype(np.float32))) for k in (0, 1, 2)]
        return {"Z": zs, "S": np.array(counts, np.int64)}
    raise KeyError(name)


def main() -> int:
    run_id = sys.argv[1]
    out_dir = os.path.join(REPO, "reports", "r2_1_1", run_id)
    os.makedirs(out_dir, exist_ok=True)
    so = ort.SessionOptions()
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_DISABLE_ALL
    report = {"schema": "r2_1_1/cpu_oracle/1",
              "ort": ort.__version__, "provider": ["CPUExecutionProvider"],
              "atol": ATOL, "cases": []}
    all_ok = True
    names = sorted(f for f in os.listdir(FIX_DIR) if f.endswith(".onnx"))
    for name in names:
        path = os.path.join(FIX_DIR, name)
        sess = ort.InferenceSession(path, so,
                                    providers=["CPUExecutionProvider"])
        for k, feeds in enumerate(inputs_for(name)):
            case = {"fixture": name, "input_index": k,
                    "input_sha256": hashlib.sha256(
                        feeds["X"].tobytes()).hexdigest()[:16],
                    "input_shape": list(feeds["X"].shape)}
            try:
                got = sess.run(None, feeds)
                out_names = [o.name for o in sess.get_outputs()]
                got_map = dict(zip(out_names, got))
                want = oracle_for(name, feeds)
                case["outputs"] = {}
                ok = True
                for oname, wv in want.items():
                    gv = np.asarray(got_map[oname])
                    exact = (gv.dtype == np.int64)
                    if exact:
                        match = (gv.shape == np.asarray(wv).shape and
                                 np.array_equal(gv, wv))
                        max_abs = 0.0 if match else -1.0
                    else:
                        if gv.shape != np.asarray(wv).shape:
                            match, max_abs = False, -1.0
                        else:
                            max_abs = float(np.max(np.abs(
                                gv.astype(np.float64) -
                                np.asarray(wv, np.float64))))
                            match = max_abs <= ATOL
                    case["outputs"][oname] = {
                        "shape": list(gv.shape), "want_shape":
                            list(np.asarray(wv).shape),
                        "exact": bool(exact), "match": bool(match),
                        "max_abs": max_abs}
                    ok = ok and match
                case["verdict"] = "CPU_ORACLE_PASS" if ok else \
                    "INVALID_FIXTURE"
            except Exception as e:  # noqa: BLE001
                case["verdict"] = "INVALID_FIXTURE"
                case["error"] = f"{type(e).__name__}: {e}"
                ok = False
            all_ok = all_ok and case["verdict"] == "CPU_ORACLE_PASS"
            report["cases"].append(case)
            print(f"{name:32s} in{k} -> {case['verdict']}")
    report["all_ok"] = all_ok
    with open(os.path.join(out_dir, "CPU_ORACLE.json"), "w",
              encoding="utf-8", newline="\n") as f:
        json.dump(report, f, indent=1)
    print("CPU oracle:", "OK" if all_ok else "FAILED")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
