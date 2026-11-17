"""R1.2 child: EP device identity audit + per-candidate single-device runs.

Modes (one fresh process each, venv-winml):
  enumerate  register the EP library, dump EVERY ort.get_ep_devices() entry
             with full identity fields (no "just take the first"), then exit
             BEFORE creating any session.
  run        register, pick candidate N (0-based among devices whose ep_name
             matches), create session via SessionOptions.add_provider_for_
             devices([that_one_device], {}), run a deterministic tiny Add
             graph, capture get_providers / profile node attribution /
             parity / exact exception, then audit which amdhip64*.dll the
             process actually mapped.
  multi      negative control: add TWO distinct matching devices; source says
             the EP factory rejects >1 device (ORT_INVALID_ARGUMENT).

Never silently falls back: if get_providers() lacks the target EP the run is
classified, not counted as GPU success.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import traceback

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from r1.r1_common import (  # noqa: E402
    atomic_write_json, finish_receipt, module_snapshot, new_receipt,
    redact_json, sha256_of,
)

SAFE_HW_ACCESSORS = ["vendor", "type", "id", "vendor_id", "device_id", "arch",
                     "metadata", "description", "GetVendor", "GetType", "GetId",
                     "GetVendorId", "GetDeviceId"]


def dump_hw_device(dev) -> dict:
    """Best-effort identity dump; anything not exposed is NOT_EXPOSED."""
    out = {"repr": repr(dev)}

    def coerce(v):
        if isinstance(v, (str, int, float, bool)) or v is None:
            return v
        return str(v)

    for a in SAFE_HW_ACCESSORS:
        try:
            v = getattr(dev, a)
            out[a] = coerce(v() if callable(v) else v)
        except AttributeError:
            pass  # attribute not exposed by this build; _dir_public records the rest
        except Exception as e:
            out[a] = f"<error {e!r}>"
    try:
        out["_dir_public"] = [d for d in dir(dev) if not d.startswith("_")]
    except Exception:
        pass
    return out


def tiny_add_model() -> str:
    import numpy as np  # noqa: F401
    from onnx import TensorProto, helper
    x = helper.make_tensor_value_info("x", TensorProto.FLOAT, ["b", 3])
    y = helper.make_tensor_value_info("y", TensorProto.FLOAT, ["b", 3])
    node = helper.make_node("Add", ["x", "x"], ["y"])
    m = helper.make_model(helper.make_graph([node], "r1_add", [x], [y]),
                          opset_imports=[helper.make_opsetid("", 17)])
    m.ir_version = 9
    p = os.path.join(tempfile.gettempdir(), "r1_add.onnx")
    from onnx import save as onnx_save
    onnx_save(m, p)
    return p


def register_and_devices(ort, ep_name: str):
    sys.path.insert(0, REPO)
    from runner.ep_runner import register_ep, find_winml_ep_library
    lib = find_winml_ep_library(ep_name)
    if not lib:
        raise RuntimeError(f"EP library for {ep_name!r} not found")
    reg = register_ep(ep_name, [lib])
    devs = ort.get_ep_devices()
    return lib, reg, devs


def classify(exc_text: str, providers: list, ep: str) -> str:
    if "HIP failure 101" in exc_text or "invalid device ordinal" in exc_text:
        return "HIP101"
    if ep not in providers:
        return "EP_MISSING_FROM_SESSION"
    return "OTHER_ERROR"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ep", required=True)
    ap.add_argument("--mode", choices=["enumerate", "run", "multi"], required=True)
    ap.add_argument("--candidate", type=int, default=0)
    ap.add_argument("--out", required=True)
    ap.add_argument("--run-id", required=True)
    args = ap.parse_args()

    r = new_receipt(f"r1_epdev_{args.ep}_{args.mode}"
                    + (f"_c{args.candidate}" if args.mode == "run" else ""),
                    args.run_id, sys.argv, {"ep": args.ep, "mode": args.mode,
                                            "candidate": args.candidate})
    try:
        import onnxruntime as ort
        r["ort_version"] = ort.__version__
        lib, reg, devs = register_and_devices(ort, args.ep)
        r["ep_library"] = lib
        r["registration"] = {k: redact_json(v) if isinstance(v, str) else v
                             for k, v in reg.items()} if isinstance(reg, dict) else str(reg)

        matches = [d for d in devs if d.ep_name == args.ep]
        r["total_ep_devices"] = len(devs)
        r["matching_devices"] = len(matches)
        r["all_ep_names"] = sorted({d.ep_name for d in devs})
        r["devices"] = [{
            "index": i,
            "ep_name": d.ep_name,
            "ep_vendor": d.ep_vendor,
            "ep_metadata": str(d.ep_metadata),
            "ep_options": str(d.ep_options),
            "device": dump_hw_device(d.device),
        } for i, d in enumerate(devs)]

        if args.mode == "enumerate":
            r["modules_after_registration"] = module_snapshot()
            finish_receipt(r, 0, "ENUMERATED")
            atomic_write_json(args.out, redact_json(r))
            print(f"enumerated: {len(devs)} ep devices, {len(matches)} match {args.ep}")
            return 0

        # run / multi
        import numpy as np
        model = tiny_add_model()
        so = ort.SessionOptions()
        so.enable_profiling = True
        if args.mode == "multi":
            if len(matches) < 2:
                r["outcome_detail"] = "FEWER_THAN_TWO_CANDIDATES"
                finish_receipt(r, 0, "MULTI_NOT_APPLICABLE")
                atomic_write_json(args.out, redact_json(r))
                return 0
            picks = [matches[args.candidate], matches[(args.candidate + 1) % len(matches)]]
            r["picked_indices"] = [args.candidate, (args.candidate + 1) % len(matches)]
        else:
            if args.candidate >= len(matches):
                r["outcome_detail"] = f"candidate index {args.candidate} out of range ({len(matches)})"
                finish_receipt(r, 0, "CANDIDATE_OUT_OF_RANGE")
                atomic_write_json(args.out, redact_json(r))
                return 0
            picks = [matches[args.candidate]]
            r["picked_indices"] = [args.candidate]
        # identity of the picked device(s): DxgiAdapterNumber etc.
        r["picked_devices"] = [{
            "ep_name": p.ep_name,
            "ep_options": str(p.ep_options),
            "hw_metadata": str(getattr(p.device, "metadata", "<NOT_EXPOSED>")),
        } for p in picks]

        t0 = __import__("time").perf_counter()
        so.add_provider_for_devices(picks, {})
        sess = ort.InferenceSession(model, so)
        dt = __import__("time").perf_counter() - t0
        r["session_create_seconds"] = round(dt, 4)
        providers = sess.get_providers()
        r["session_get_providers"] = list(providers)

        rng = np.random.default_rng(1100)
        xin = rng.standard_normal((4, 3)).astype(np.float32)
        out = sess.run(None, {"x": xin})[0]
        r["parity_max_abs_diff"] = float(np.max(np.abs(out - (xin + xin))))
        prof = sess.end_profiling()
        ev = json.load(open(prof, encoding="utf-8"))
        events = ev if isinstance(ev, list) else ev.get("traceEvents", [])
        nodes = [e for e in events if e.get("cat") == "Node"]
        # full attribution: which provider executed each node (mission rule: a
        # GPU PASS needs at least one compute node actually fired by the EP)
        node_attribution = [{
            "name": n.get("name"), "dur_us": n.get("dur"),
            "op_name": (n.get("args") or {}).get("op_name"),
            "provider": (n.get("args") or {}).get("provider"),
            "activity": (n.get("args") or {}).get("op_count"),
        } for n in nodes]
        # keep the raw profile beside the receipt for independent review
        import shutil
        prof_dest = os.path.join(os.path.dirname(args.out),
                                 os.path.basename(args.out).replace(".json", "_profile.json"))
        try:
            shutil.copyfile(prof, prof_dest)
        except OSError:
            prof_dest = None
        r["profile"] = {"events": len(events), "node_events": len(nodes),
                        "node_attribution": node_attribution,
                        "profile_saved": os.path.basename(prof_dest) if prof_dest else None}
        r["modules_after_session"] = module_snapshot()
        target_in_session = args.ep in providers
        ep_executed_nodes = [n for n in node_attribution if n.get("provider") == args.ep]
        r["ep_executed_node_count"] = len(ep_executed_nodes)
        if target_in_session and ep_executed_nodes and r["parity_max_abs_diff"] == 0.0:
            outcome = "PASS_AMD_GPU_EXECUTED_AND_PARITY"
        elif target_in_session:
            outcome = "EP_IN_SESSION_NO_GPU_NODES"
        else:
            outcome = "EP_MISSING_FROM_SESSION"
        finish_receipt(r, 0, outcome)
        atomic_write_json(args.out, redact_json(r))
        print(f"{args.ep} c{args.candidate} {args.mode}: {outcome}; providers={providers}")
        return 0

    except Exception as e:
        r["exception"] = "".join(traceback.format_exception_only(type(e), e))[:2000]
        r["traceback_tail"] = traceback.format_exc()[-1500:]
        r["modules_at_failure"] = module_snapshot()
        finish_receipt(r, 1, classify(str(e), [], args.ep))
        atomic_write_json(args.out, redact_json(r))
        print(f"{args.ep} {args.mode} c{args.candidate}: {r['outcome']}: {r['exception'][:200]}")
        # ORT/WinML keep non-daemon threads alive; hard-exit after receipt write
        os._exit(1)


if __name__ == "__main__":
    sys.exit(main())
