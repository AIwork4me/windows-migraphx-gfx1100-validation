"""R1.0.5 sanity control: CPU and (if available) DML on a deterministic tiny graph.

DML here is a *methodological control only* -- it never counts as an AMD
GPU/MIGraphX pass. Runs in whichever interpreter invokes it; the supervisor
calls it once per venv.
"""

from __future__ import annotations

import argparse
import numpy as np
import onnxruntime as ort

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from r1.r1_common import atomic_write_json, finish_receipt, new_receipt  # noqa: E402

import onnx
from onnx import helper, TensorProto


def tiny_model(tmpdir: str) -> str:
    x = helper.make_tensor_value_info("x", TensorProto.FLOAT, ["b", 3])
    y = helper.make_tensor_value_info("y", TensorProto.FLOAT, ["b", 3])
    node = helper.make_node("Add", ["x", "x"], ["y"])
    m = helper.make_model(helper.make_graph([node], "r1_add", [x], [y]),
                          opset_imports=[helper.make_opsetid("", 17)])
    m.ir_version = 9
    p = os.path.join(tmpdir, "r1_add.onnx")
    onnx.save(m, p)
    return p


def run_one(provider: str) -> dict:
    so = ort.SessionOptions()
    so.enable_profiling = True
    sess = ort.InferenceSession(THE_MODEL, so, providers=[provider])
    got = sess.get_providers()
    rng = np.random.default_rng(1100)
    xin = rng.standard_normal((4, 3)).astype(np.float32)
    out = sess.run(None, {"x": xin})[0]
    exp = xin + xin
    prof = sess.end_profiling()
    import json as _j
    ev = _j.load(open(prof, encoding="utf-8"))
    events = ev if isinstance(ev, list) else ev.get("traceEvents", [])
    op_nodes = [e for e in events if e.get("cat") == "Node"]
    return {"requested": provider, "session_providers": got,
            "parity_max_abs_diff": float(np.max(np.abs(out - exp))),
            "profile_events": len(events), "node_events": len(op_nodes),
            "profile_file": os.path.basename(prof)}


THE_MODEL = None

def main() -> int:
    global THE_MODEL
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--tag", required=True)
    args = ap.parse_args()

    import tempfile
    THE_MODEL = tiny_model(tempfile.gettempdir())
    r = new_receipt(f"r1_sanity_{args.tag}", args.run_id, sys.argv,
                    {"ort_version": ort.__version__,
                     "available_providers": list(ort.get_available_providers())})
    results = {}
    results["CPU"] = run_one("CPUExecutionProvider")
    if "DmlExecutionProvider" in ort.get_available_providers():
        results["DML"] = run_one("DmlExecutionProvider")
        r["note"] = "DML is a methodological control, not an AMD GPU pass"
    else:
        results["DML"] = {"status": "NOT_AVAILABLE_IN_THIS_BUILD"}
    r["results"] = results
    ok = results["CPU"]["session_providers"] and results["CPU"]["parity_max_abs_diff"] == 0.0
    finish_receipt(r, 0, "SANITY_OK" if ok else "SANITY_CHECK_FAILED")
    atomic_write_json(args.out, r)
    print(args.tag, "->", r["outcome"], {k: v.get("session_providers", v.get("status")) for k, v in results.items()})
    return 0


if __name__ == "__main__":
    sys.exit(main())
