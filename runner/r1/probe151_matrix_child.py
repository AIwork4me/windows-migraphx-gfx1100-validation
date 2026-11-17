"""R1.3 child: one cell of the #151 fallback differential matrix.

Cells (fresh process each; stdout AND stderr both captured -- never DEVNULL):
  A  EP library NOT registered + classic providers=[ep, CPU]
  B  EP registered + classic providers=[ep, CPU]
  C0 EP registered + device API, candidate 0 (DxgiAdapterNumber=1 device)
  C1 EP registered + device API, candidate 1 (DxgiAdapterNumber=0 device)
  D  EP registered + device API, TWO devices (negative control)
  E  explicit CPU-only providers=['CPUExecutionProvider']
  F1 EP registered + providers not passed at all (None)
  F2 EP registered + providers=[] (empty list)
  F3 EP registered + providers=[ep] only, no CPU fallback entry

Outcome vocabulary (mission R1.3.4):
  EP_UNREGISTERED_SILENT_CPU_FALLBACK / REGISTERED_CLASSIC_UNSUPPORTED /
  SESSION_FACTORY_ERROR / EXPLICIT_CPU_FALLBACK / HIP101 /
  PASS_AMD_GPU_EXECUTED_AND_PARITY / EP_MISSING_FROM_SESSION /
  EP_IN_SESSION_NO_GPU_NODES / SESSION_RAISED
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
import traceback

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from r1.r1_common import atomic_write_json, finish_receipt, new_receipt, redact_json  # noqa: E402


def make_model() -> str:
    from onnx import TensorProto, helper, save
    x = helper.make_tensor_value_info("x", TensorProto.FLOAT, ["b", 3])
    y = helper.make_tensor_value_info("y", TensorProto.FLOAT, ["b", 3])
    n = helper.make_node("Add", ["x", "x"], ["y"])
    m = helper.make_model(helper.make_graph([n], "r1_add", [x], [y]),
                          opset_imports=[helper.make_opsetid("", 17)])
    m.ir_version = 9
    p = os.path.join(tempfile.gettempdir(), "r1_add_151.onnx")
    save(m, p)
    return p


def classify(ep: str, registered: bool, providers_in: list, exc_text: str,
             got: list, ep_nodes: int) -> str:
    if "HIP failure 101" in exc_text or "invalid device ordinal" in exc_text:
        return "HIP101"
    if "INVALID_ARGUMENT" in exc_text and "single device" in exc_text:
        return "SESSION_FACTORY_ERROR"
    if exc_text:
        return "SESSION_RAISED"
    if ep in got:
        return "PASS_AMD_GPU_EXECUTED_AND_PARITY" if ep_nodes > 0 else "EP_IN_SESSION_NO_GPU_NODES"
    if not registered and ep in (providers_in or []):
        return "EP_UNREGISTERED_SILENT_CPU_FALLBACK"
    return "EP_MISSING_FROM_SESSION"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cell", required=True)
    ap.add_argument("--ep", default="AMDGPUExecutionProvider")
    ap.add_argument("--out", required=True)
    ap.add_argument("--run-id", required=True)
    args = ap.parse_args()

    r = new_receipt(f"r1_151_{args.cell}", args.run_id, sys.argv,
                    {"cell": args.cell, "ep": args.ep})
    ep = args.ep
    registered = args.cell not in ("A",)

    # Python warnings print to stderr by default; supervisor captures both streams.
    try:
        import numpy as np
        import onnxruntime as ort
        r["ort_version"] = ort.__version__
        model = make_model()

        matches = []
        if registered:
            from runner.ep_runner import register_ep, find_winml_ep_library
            lib = find_winml_ep_library(ep)
            reg = register_ep(ep, [lib])
            r["registration_ok"] = bool(reg.get("ok"))
            matches = [d for d in ort.get_ep_devices() if d.ep_name == ep]
            r["matching_devices"] = len(matches)

        so = ort.SessionOptions()
        so.enable_profiling = True
        providers_in = None
        use_device_api = False
        picks = []
        if args.cell == "A" or args.cell == "B":
            providers_in = [ep, "CPUExecutionProvider"]
        elif args.cell in ("C0", "C1"):
            use_device_api = True
            picks = [matches[int(args.cell[1])]]
        elif args.cell == "D":
            use_device_api = True
            picks = matches[:2]
        elif args.cell == "E":
            providers_in = ["CPUExecutionProvider"]
        elif args.cell == "F1":
            providers_in = None
        elif args.cell == "F2":
            providers_in = []
        elif args.cell == "F3":
            providers_in = [ep]
        r["providers_arg"] = providers_in
        r["device_api_devices"] = len(picks)

        if use_device_api:
            so.add_provider_for_devices(picks, {})

        t0 = time.perf_counter()
        if providers_in is None:
            sess = ort.InferenceSession(model, so) if use_device_api else \
                ort.InferenceSession(model)
        else:
            sess = ort.InferenceSession(model, so, providers=providers_in)
        r["session_create_seconds"] = round(time.perf_counter() - t0, 4)
        got = list(sess.get_providers())
        r["session_get_providers"] = got

        rng = np.random.default_rng(1100)
        xin = rng.standard_normal((4, 3)).astype(np.float32)
        out = sess.run(None, {"x": xin})[0]
        r["parity_max_abs_diff"] = float(np.max(np.abs(out - (xin + xin))))

        prof = sess.end_profiling()
        events = []
        if prof and os.path.isfile(prof):
            ev = json.load(open(prof, encoding="utf-8"))
            events = ev if isinstance(ev, list) else ev.get("traceEvents", [])
        nodes = [e for e in events if e.get("cat") == "Node"]
        r["node_attribution"] = [
            {"name": n.get("name"), "provider": (n.get("args") or {}).get("provider")}
            for n in nodes]
        ep_nodes = sum(1 for n in r["node_attribution"] if n["provider"] == ep)

        outcome = classify(ep, registered, providers_in or [], "", got, ep_nodes)
        if outcome.startswith("PASS") and r["parity_max_abs_diff"] != 0.0:
            outcome = "EP_IN_SESSION_PARITY_MISMATCH"
        finish_receipt(r, 0, outcome)
        atomic_write_json(args.out, redact_json(r))
        print(f"cell {args.cell}: {outcome}; providers={got}", flush=True)
        sys.stdout.flush(); sys.stderr.flush()
        os._exit(0)

    except Exception as e:
        r["exception"] = "".join(traceback.format_exception_only(type(e), e))[:1500]
        r["traceback_tail"] = traceback.format_exc()[-1200:]
        outcome = classify(ep, registered, [], r["exception"], [], 0)
        finish_receipt(r, 1, outcome)
        atomic_write_json(args.out, redact_json(r))
        print(f"cell {args.cell}: {outcome}: {r['exception'][:180]}", flush=True)
        sys.stdout.flush(); sys.stderr.flush()
        os._exit(1)



if __name__ == "__main__":
    sys.exit(main())
