"""Shared test infrastructure for WIN-MIGRAPHX-GFX1100.

Design rules (from mission spec):
- No silent CPU fallback: every GPU case must record get_providers(), an ORT
  profile with per-node provider attribution, and gpu_nodes/total_nodes.
- Multi-output correctness: compare outputs BY NAME for dtype, shape, content.
- Receipts are immutable evidence: command, cwd, exit code, UTC+local time,
  versions, seeds; written as JSON next to per-case logs.
- Pre-registered tolerances; never loosened post-hoc.
"""
from __future__ import annotations
import dataclasses
import datetime, json, os, platform, subprocess, sys, time, hashlib, uuid

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
REPORTS = os.path.join(REPO, "reports")
FIXTURES = os.path.join(REPO, "fixtures")
LEDGER = os.path.join(REPORTS, "ledger.json")

# Pre-registered numeric gates (fixed BEFORE execution; do not loosen)
TOL_FP32 = dict(atol=1e-3, rtol=1e-3)
TOL_FP16 = dict(atol=1e-2, rtol=1e-2)

# ORT graph optimization levels
ORT_OPT_LEVELS = {
    "ORT_DISABLE_ALL": 0, "ORT_ENABLE_BASIC": 1, "ORT_ENABLE_EXTENDED": 2,
    "ORT_ENABLE_ALL": 99,  # default
}

COMPUTE_EXCLUDE = {"Memcpy", "MemcpyFromHost", "MemcpyToHost", "FusedMemcpy",
                   "Source", "Sink", "Constant"}


def now_pair() -> dict:
    u = datetime.datetime.now(datetime.timezone.utc)
    return {"utc": u.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "local": datetime.datetime.now().astimezone().isoformat(timespec="seconds")}


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def atomic_write_json(path: str, obj) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
        f.flush(); os.fsync(f.fileno())
    os.replace(tmp, path)


def new_receipt(phase: str, case_id: str) -> dict:
    r = {
        "mission": "WIN-MIGRAPHX-GFX1100",
        "case_id": case_id,
        "phase": phase,
        "host": platform.node(),
        "os": platform.platform(),
        "python": sys.version,
        "argv": sys.argv,
        "cwd": os.getcwd(),
        "run_id": uuid.uuid4().hex[:12],
        "started": now_pair(),
        "seed_policy": "numpy default_rng(seed=1100) unless stated",
    }
    return r


def save_receipt(phase: str, receipt: dict, out_dir: str | None = None) -> str:
    d = out_dir or os.path.join(REPORTS, phase)
    os.makedirs(d, exist_ok=True)
    receipt["finished"] = now_pair()
    p = os.path.join(d, receipt["case_id"] + ".json")
    atomic_write_json(p, receipt)
    return p


# ---------------------------------------------------------------- ONNX models
def build_models():
    """Original synthetic ONNX graphs. Returns dict name -> (onnx.ModelProto builder, meta).

    All graphs deterministic given the seeded inputs produced in make_feeds().
    """
    import onnx
    from onnx import TensorProto, helper, numpy_helper
    import numpy as np

    M: dict[str, dict] = {}

    def reg(name, m: onnx.ModelProto, inputs_meta, desc):
        m.opset_import[0].version = 17
        m.ir_version = 9
        M[name] = {"model": m, "inputs": inputs_meta, "desc": desc}

    # identity_add: Y = X + C
    X = helper.make_tensor_value_info("X", TensorProto.FLOAT, ["b", 8])
    Y = helper.make_tensor_value_info("Y", TensorProto.FLOAT, ["b", 8])
    C = numpy_helper.from_array(np.full((8,), 0.25, np.float32), "C")
    n = helper.make_node("Add", ["X", "C"], ["Y"])
    reg("identity_add",
        helper.make_model(helper.make_graph([n], "g", [X], [Y], [C])),
        [("X", np.float32, ("b", 8))], "Y=X+C")

    # matmul: Y = X(b,64) @ W(64,32)
    X = helper.make_tensor_value_info("X", TensorProto.FLOAT, ["b", 64])
    Y = helper.make_tensor_value_info("Y", TensorProto.FLOAT, ["b", 32])
    rng = np.random.default_rng(1100)
    W = numpy_helper.from_array(rng.standard_normal((64, 32)).astype(np.float32) * 0.05, "W")
    n = helper.make_node("MatMul", ["X", "W"], ["Y"])
    reg("matmul",
        helper.make_model(helper.make_graph([n], "g", [X], [Y], [W])),
        [("X", np.float32, ("b", 64))], "Y=X@W")

    # conv_relu: Conv(1,3,32,32)->Relu
    X = helper.make_tensor_value_info("X", TensorProto.FLOAT, ["b", 3, 32, 32])
    Y = helper.make_tensor_value_info("Y", TensorProto.FLOAT, ["b", 8, 30, 30])
    w = rng.standard_normal((8, 3, 3, 3)).astype(np.float32) * 0.1
    Wc = numpy_helper.from_array(w, "Wc")
    n1 = helper.make_node("Conv", ["X", "Wc"], ["T"])
    n2 = helper.make_node("Relu", ["T"], ["Y"])
    reg("conv_relu",
        helper.make_model(helper.make_graph([n1, n2], "g", [X], [Y], [Wc])),
        [("X", np.float32, ("b", 3, 32, 32))], "Conv+Relu 3ch")

    # softmax over last dim
    X = helper.make_tensor_value_info("X", TensorProto.FLOAT, ["b", 16])
    Y = helper.make_tensor_value_info("Y", TensorProto.FLOAT, ["b", 16])
    n = helper.make_node("Softmax", ["X"], ["Y"], axis=-1)
    reg("softmax",
        helper.make_model(helper.make_graph([n], "g", [X], [Y])),
        [("X", np.float32, ("b", 16))], "Softmax axis=-1")

    # reshape_transpose: (b,8,4) -> transpose(0,2,1) -> reshape (b, 32)
    X = helper.make_tensor_value_info("X", TensorProto.FLOAT, ["b", 8, 4])
    T = helper.make_tensor_value_info("T", TensorProto.FLOAT, ["b", 4, 8])
    Y = helper.make_tensor_value_info("Y", TensorProto.FLOAT, ["b", 32])
    n1 = helper.make_node("Transpose", ["X"], ["T"], perm=[0, 2, 1])
    shp = numpy_helper.from_array(np.array([0, -1], np.int64), "shape")
    n2 = helper.make_node("Reshape", ["T", "shape"], ["Y"])
    reg("reshape_transpose",
        helper.make_model(helper.make_graph([n1, n2], "g", [X], [Y], [shp])),
        [("X", np.float32, ("b", 8, 4))], "Transpose+Reshape")

    # two outputs with SAME shape (swap detector): A = X+C1, B = X+C2  (C1=+1, C2=+10)
    X = helper.make_tensor_value_info("X", TensorProto.FLOAT, ["b", 8])
    A = helper.make_tensor_value_info("A", TensorProto.FLOAT, ["b", 8])
    B = helper.make_tensor_value_info("B", TensorProto.FLOAT, ["b", 8])
    C1 = numpy_helper.from_array(np.full((8,), 1.0, np.float32), "C1")
    C2 = numpy_helper.from_array(np.full((8,), 10.0, np.float32), "C2")
    n1 = helper.make_node("Add", ["X", "C1"], ["A"])
    n2 = helper.make_node("Add", ["X", "C2"], ["B"])
    reg("two_out_same_shape",
        helper.make_model(helper.make_graph([n1, n2], "g", [X], [A, B], [C1, C2])),
        [("X", np.float32, ("b", 8))], "2 outputs same shape, offsets +1/+10 (swap detector)")

    # nine outputs (SCRFD-like shape variety), distinct offsets 1..9
    ins = [helper.make_tensor_value_info("X", TensorProto.FLOAT, ["b", 8])]
    outs, nodes, inits = [], [], []
    for i in range(1, 10):
        on = f"out{i}"
        outs.append(helper.make_tensor_value_info(on, TensorProto.FLOAT, ["b", 8]))
        ci = numpy_helper.from_array(np.full((8,), float(i), np.float32), f"C{i}")
        inits.append(ci)
        nodes.append(helper.make_node("Add", ["X", f"C{i}"], [on]))
    reg("nine_out",
        helper.make_model(helper.make_graph(nodes, "g", ins, outs, inits)),
        [("X", np.float32, ("b", 8))], "9 outputs, offsets +1..+9 (#150 probe)")

    # mixed shapes multi-output: small + big
    X = helper.make_tensor_value_info("X", TensorProto.FLOAT, ["b", 64])
    Ys = helper.make_tensor_value_info("small", TensorProto.FLOAT, ["b", 4])
    Yb = helper.make_tensor_value_info("big", TensorProto.FLOAT, ["b", 128])
    rng2 = np.random.default_rng(7)
    W1 = numpy_helper.from_array(rng2.standard_normal((64, 4)).astype(np.float32) * 0.05, "W1")
    W2 = numpy_helper.from_array(rng2.standard_normal((64, 128)).astype(np.float32) * 0.05, "W2")
    n1 = helper.make_node("MatMul", ["X", "W1"], ["small"])
    n2 = helper.make_node("MatMul", ["X", "W2"], ["big"])
    reg("two_out_diff_shape",
        helper.make_model(helper.make_graph([n1, n2], "g", [X], [Ys, Yb], [W1, W2])),
        [("X", np.float32, ("b", 64))], "2 outputs different shapes")

    # operator coverage set (M1)
    def simple(name, op: str, ishape, oshape, attrs=None, extra_inits=None, desc=""):
        Xi = helper.make_tensor_value_info("X", TensorProto.FLOAT, ishape)
        Yo = helper.make_tensor_value_info("Y", TensorProto.FLOAT, oshape)
        nd = helper.make_node(op, ["X"], ["Y"], **(attrs or {}))
        inits2 = extra_inits or []
        reg(name, helper.make_model(helper.make_graph([nd], "g", [Xi], [Yo], inits2)),
            [("X", np.float32, tuple(ishape))], desc or op)

    simple("relu", "Relu", ["b", 16], ["b", 16])
    simple("sigmoid", "Sigmoid", ["b", 16], ["b", 16])
    simple("tanh", "Tanh", ["b", 16], ["b", 16])
    simple("sqrt", "Sqrt", ["b", 16], ["b", 16])
    simple("exp", "Exp", ["b", 16], ["b", 16])
    simple("erf", "Erf", ["b", 16], ["b", 16])
    simple("neg", "Neg", ["b", 16], ["b", 16])
    simple("abs", "Abs", ["b", 16], ["b", 16])
    simple("floor", "Floor", ["b", 16], ["b", 16])
    simple("log", "Log", ["b", 16], ["b", 16])
    simple("sin", "Sin", ["b", 16], ["b", 16])
    simple("cos", "Cos", ["b", 16], ["b", 16])
    # reduce ops
    simple("reduce_mean", "ReduceMean", ["b", 16, 4], ["b", 16, 1], {"axes": [2], "keepdims": 1})
    simple("reduce_max", "ReduceMax", ["b", 16, 4], ["b", 16], {"axes": [2], "keepdims": 0})
    simple("reduce_sum", "ReduceSum", ["b", 16, 4], ["b", 16], {"axes": [2], "keepdims": 0})
    simple("argmax", "ArgMax", ["b", 16, 4], ["b", 16], {"axis": 2, "keepdims": 0})
    simple("squeeze", "Squeeze", ["b", 16, 1], ["b", 16], {"axes": [1]})

    # gemm: Y = alpha*X@W + beta*C
    X = helper.make_tensor_value_info("X", TensorProto.FLOAT, ["b", 32])
    Y = helper.make_tensor_value_info("Y", TensorProto.FLOAT, ["b", 16])
    Wg = numpy_helper.from_array(rng.standard_normal((32, 16)).astype(np.float32) * 0.05, "Wg")
    Cg = numpy_helper.from_array(np.zeros((16,), np.float32), "Cg")
    n = helper.make_node("Gemm", ["X", "Wg", "Cg"], ["Y"], alpha=1.0, beta=1.0)
    reg("gemm", helper.make_model(helper.make_graph([n], "g", [X], [Y], [Wg, Cg])),
        [("X", np.float32, ("b", 32))], "Gemm")

    # layer_norm combo: Mean+Sub+Pow+Mean+Add+Sqrt+Div+Mul+Add
    X = helper.make_tensor_value_info("X", TensorProto.FLOAT, ["b", 64])
    Y = helper.make_tensor_value_info("Y", TensorProto.FLOAT, ["b", 64])
    nodes = [
        helper.make_node("ReduceMean", ["X"], ["mu"], axes=[-1], keepdims=1),
        helper.make_node("Sub", ["X", "mu"], ["xc"]),
        helper.make_node("Pow", ["xc", "two"], ["x2"]),
        helper.make_node("ReduceMean", ["x2"], ["var"], axes=[-1], keepdims=1),
        helper.make_node("Add", ["var", "eps"], ["ve"]),
        helper.make_node("Sqrt", ["ve"], ["sd"]),
        helper.make_node("Div", ["xc", "sd"], ["norm"]),
        helper.make_node("Mul", ["norm", "gamma"], ["ng"]),
        helper.make_node("Add", ["ng", "beta"], ["Y"]),
    ]
    inits = [
        numpy_helper.from_array(np.array(2.0, np.float32), "two"),
        numpy_helper.from_array(np.array(1e-5, np.float32), "eps"),
        numpy_helper.from_array(np.ones((64,), np.float32), "gamma"),
        numpy_helper.from_array(np.zeros((64,), np.float32), "beta"),
    ]
    reg("layernorm", helper.make_model(helper.make_graph(nodes, "g", [X], [Y], inits)),
        [("X", np.float32, ("b", 64))], "LayerNorm composite")

    # depthwise conv: groups=8
    X = helper.make_tensor_value_info("X", TensorProto.FLOAT, ["b", 4, 16, 16])
    Y = helper.make_tensor_value_info("Y", TensorProto.FLOAT, ["b", 4, 16, 16])
    wd = rng.standard_normal((4, 1, 3, 3)).astype(np.float32) * 0.1
    Wd = numpy_helper.from_array(wd, "Wd")
    n1 = helper.make_node("Conv", ["X", "Wd"], ["T2"], group=4, pads=[1, 1, 1, 1])
    n2 = helper.make_node("Relu", ["T2"], ["Y"])
    reg("depthwise_conv",
        helper.make_model(helper.make_graph([n1, n2], "g", [X], [Y], [Wd])),
        [("X", np.float32, ("b", 4, 16, 16))], "DepthwiseConv+Relu pad=1")

    # batchnorm inference pattern: BN = (X-mu)/sqrt(var+eps)*gamma+beta folded as ops
    X = helper.make_tensor_value_info("X", TensorProto.FLOAT, ["b", 8, 8, 8])
    Y = helper.make_tensor_value_info("Y", TensorProto.FLOAT, ["b", 8, 8, 8])
    nodes = [
        helper.make_node("Sub", ["X", "mu"], ["t1"]),
        helper.make_node("Add", ["var", "eps2"], ["t2"]),
        helper.make_node("Sqrt", ["t2"], ["t3"]),
        helper.make_node("Div", ["t1", "t3"], ["t4"]),
        helper.make_node("Mul", ["t4", "gam"], ["t5"]),
        helper.make_node("Add", ["t5", "bet"], ["Y"]),
    ]
    inits = [
        numpy_helper.from_array(np.zeros((1, 8, 1, 1), np.float32), "mu"),
        numpy_helper.from_array(np.ones((1, 8, 1, 1), np.float32), "var"),
        numpy_helper.from_array(np.array(1e-5, np.float32), "eps2"),
        numpy_helper.from_array(np.ones((1, 8, 1, 1), np.float32), "gam"),
        numpy_helper.from_array(np.zeros((1, 8, 1, 1), np.float32), "bet"),
    ]
    reg("batchnorm", helper.make_model(helper.make_graph(nodes, "g", [X], [Y], inits)),
        [("X", np.float32, ("b", 8, 8, 8))], "BatchNorm inference")

    # gather + topk + concat
    X = helper.make_tensor_value_info("X", TensorProto.FLOAT, ["b", 32])
    Y = helper.make_tensor_value_info("Y", TensorProto.FLOAT, ["b", 4])
    nodes = [
        helper.make_node("TopK", ["X", "k"], ["tv", "ti"], axis=-1),
        helper.make_node("Gather", ["X", "gi"], ["gg"], axis=1),
        helper.make_node("Concat", ["tv", "gg"], ["Y"], axis=1),
    ]
    inits = [numpy_helper.from_array(np.array(2, np.int64), "k"),
             numpy_helper.from_array(np.array([0, 2], np.int64), "gi")]
    reg("topk_gather_concat",
        helper.make_model(helper.make_graph(nodes, "g", [X], [Y], inits)),
        [("X", np.float32, ("b", 32))], "TopK+Gather+Concat")

    # resize (nearest, downsample 2x)
    X = helper.make_tensor_value_info("X", TensorProto.FLOAT, ["b", 4, 16, 16])
    Y = helper.make_tensor_value_info("Y", TensorProto.FLOAT, ["b", 4, 8, 8])
    nodes = [
        helper.make_node("Resize", ["X", "", "scales"], ["Y"],
                         mode="nearest", nearest_mode="floor", coordinate_transformation_mode="asymmetric"),
    ]
    inits = [numpy_helper.from_array(np.array([1.0, 1.0, 0.5, 0.5], np.float32), "scales")]
    reg("resize_nearest",
        helper.make_model(helper.make_graph(nodes, "g", [X], [Y], inits)),
        [("X", np.float32, ("b", 4, 16, 16))], "Resize nearest 2x down")

    # split then concat (multi-output 2)
    X = helper.make_tensor_value_info("X", TensorProto.FLOAT, ["b", 16])
    A = helper.make_tensor_value_info("A", TensorProto.FLOAT, ["b", 8])
    B = helper.make_tensor_value_info("B", TensorProto.FLOAT, ["b", 8])
    n = helper.make_node("Split", ["X"], ["A", "B"], axis=1, num_outputs=2)
    reg("split2",
        helper.make_model(helper.make_graph([n], "g", [X], [A, B])),
        [("X", np.float32, ("b", 16))], "Split into 2 outputs")

    # pad
    X = helper.make_tensor_value_info("X", TensorProto.FLOAT, ["b", 8])
    Y = helper.make_tensor_value_info("Y", TensorProto.FLOAT, ["b", 12])
    n = helper.make_node("Pad", ["X", "pads"], ["Y"], mode="constant")
    inits = [numpy_helper.from_array(np.array([0, 2, 2], np.int64), "pads")]
    reg("pad", helper.make_model(helper.make_graph([n], "g", [X], [Y], inits)),
        [("X", np.float32, ("b", 8))], "Pad constant")

    # cast to int32 (dtype correctness probe)
    X = helper.make_tensor_value_info("X", TensorProto.FLOAT, ["b", 8])
    Y = helper.make_tensor_value_info("Y", TensorProto.INT32, ["b", 8])
    n = helper.make_node("Cast", ["X"], ["Y"], to=TensorProto.INT32)
    reg("cast_f32_i32",
        helper.make_model(helper.make_graph([n], "g", [X], [Y])),
        [("X", np.float32, ("b", 8))], "Cast float32->int32")

    # dynamic batch conv small (dynamic shape via symbolic dims already in others)
    return M


def write_fixtures(force: bool = False) -> dict[str, str]:
    """Materialize fixtures/*.onnx + fixtures/manifest.json (sha256)."""
    import onnx
    os.makedirs(FIXTURES, exist_ok=True)
    M = build_models()
    man = {}
    for name, ent in M.items():
        p = os.path.join(FIXTURES, name + ".onnx")
        if force or not os.path.exists(p):
            onnx.save(ent["model"], p)
        man[name] = {"sha256": sha256_file(p), "desc": ent["desc"],
                     "inputs": [[n, getattr(dt, "__name__", str(dt)), list(sh)]
                                for n, dt, sh in ent["inputs"]]}
    atomic_write_json(os.path.join(FIXTURES, "manifest.json"), man)
    return man


def meta_from_model(path: str) -> dict:
    """Build a model_meta (inputs list) for an arbitrary ONNX file."""
    import onnx
    from onnx import TensorProto, helper
    m = onnx.load(path)
    _DT = {TensorProto.FLOAT: __import__("numpy").float32,
           TensorProto.INT64: __import__("numpy").int64,
           TensorProto.INT32: __import__("numpy").int32,
           TensorProto.DOUBLE: __import__("numpy").float64,
           TensorProto.UINT8: __import__("numpy").uint8}
    inits = {i.name: i for i in m.graph.initializer}
    inputs = []
    for i in m.graph.input:
        if i.name in inits:
            continue
        dt = _DT.get(i.type.tensor_type.elem_type, __import__("numpy").float32)
        shape = []
        for d in i.type.tensor_type.shape.dim:
            shape.append(d.dim_param if d.dim_param else d.dim_value or "b")
        if not shape:
            shape = ["b"]
        if isinstance(shape[0], int) and shape[0] == 0:
            shape[0] = "b"
        inputs.append((i.name, dt, tuple(shape)))
    man = {"inputs": [list(x) for x in inputs], "desc": "from " + os.path.basename(path)}
    return {"inputs": [tuple(x) for x in man["inputs"]], "desc": man["desc"]}


def make_feed(name: str, model_meta: dict, batch: int, seed: int = 1100):
    """Deterministic feeds per (model, batch): rng seeded by hash."""
    import numpy as np
    rng = np.random.default_rng(seed + (hash(name) % 10000) + batch)
    feeds = {}
    for entry in model_meta["inputs"]:
        iname, dtype, shape = entry[0], entry[1], tuple(entry[2])
        if isinstance(dtype, str):
            dtype = np.dtype(dtype).type
        shape = [batch if (isinstance(s, str) or s is None) else s for s in shape]
        if np.issubdtype(dtype, np.floating):
            feeds[iname] = rng.standard_normal(shape).astype(np.float32)
        else:
            feeds[iname] = rng.integers(0, 4, size=shape).astype(dtype)
    return feeds


# ---------------------------------------------------------------- parity
def parity_check(expected: dict, actual: dict, atol: float, rtol: float) -> dict:
    """BY-NAME output comparison: names, dtype, shape, content. Returns verdict detail."""
    import numpy as np
    res = {"outputs_expected": sorted(expected.keys()),
           "outputs_actual": sorted(actual.keys()),
           "names_match": sorted(expected.keys()) == sorted(actual.keys()),
           "per_output": {}, "all_ok": True}
    for k in sorted(expected.keys()):
        if k not in actual:
            res["per_output"][k] = {"status": "MISSING"}
            res["all_ok"] = False
            continue
        e, a = np.asarray(expected[k]), np.asarray(actual[k])
        d = {"dtype_expected": str(e.dtype), "dtype_actual": str(a.dtype),
             "shape_expected": list(e.shape), "shape_actual": list(a.shape)}
        if e.dtype != a.dtype or e.shape != a.shape:
            d["status"] = "META_MISMATCH"
            res["all_ok"] = False
        else:
            diff = np.abs(e.astype(np.float64) - a.astype(np.float64))
            ok = bool(np.allclose(e, a, atol=atol, rtol=rtol))
            d.update({"status": "OK" if ok else "VALUE_MISMATCH",
                      "max_abs_diff": float(diff.max()) if diff.size else 0.0,
                      "mean_abs_diff": float(diff.mean()) if diff.size else 0.0,
                      "allclose": ok})
            if not ok:
                res["all_ok"] = False
                d["expected_sample"] = e.flatten()[:4].tolist()
                d["actual_sample"] = a.flatten()[:4].tolist()
        res["per_output"][k] = d
    return res


# ---------------------------------------------------------------- profiling
def analyze_profile(profile_path: str, target_ep: str) -> dict:
    """Count compute nodes by provider from an ORT chrome-trace profile.

    Node events appear twice (start/stop); count unique node names per provider.
    Returns gpu_nodes (target_ep), cpu_nodes, other_ep nodes, total compute nodes.
    """
    import json as _json
    with open(profile_path, "r", encoding="utf-8") as f:
        trace = _json.load(f)
    seen: dict[str, str] = {}
    for ev in trace.get("traceEvents", []):
        if ev.get("cat") != "Node":
            continue
        name = ev.get("name", "")
        op = ev.get("args", {}).get("op_name", "")
        prov = ev.get("args", {}).get("provider", "")
        if any(x in name for x in COMPUTE_EXCLUDE) and not prov:
            continue
        seen[f"{ev.get('pid')}:{ev.get('tid')}:{name}#{len(seen)}"] = prov or "cpu"
        _ = op
    provs = list(seen.values())
    gpu = sum(1 for p in provs if target_ep.lower() in p.lower())
    cpu = sum(1 for p in provs if p == "cpu" or p == "CPUExecutionProvider")
    other = len(provs) - gpu - cpu
    return {"total_node_events": len(provs), "gpu_nodes": gpu, "cpu_nodes": cpu,
            "other_ep_nodes": other, "providers_seen": sorted(set(provs)),
            "gpu_node_ratio": (gpu / len(provs)) if provs else 0.0}


def summarize_session(ort_sess, target_ep: str) -> dict:
    try:
        return {"get_providers": list(ort_sess.get_providers())}
    except Exception as e:
        return {"get_providers_error": repr(e)}
