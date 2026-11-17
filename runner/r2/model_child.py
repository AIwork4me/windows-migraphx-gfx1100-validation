"""R2.2 model-validation child: ONE (model, EP, batch, opt-level) per process.

Runs on the real RX 7900 XTX via the fail-closed device resolver, or fails
with an honest classification. GPU PASS requires ALL of:

  1. target EP in session get_providers()
  2. >=1 compute node attributed to the EP in the ORT profile
  3. by-name, per-output parity vs an ORT CPU reference built from the
     SAME onnxruntime build with identical inputs (predeclared tolerance)
  4. receipt records providers, node attribution, DLL, versions, timings

Classification vocabulary (never silently downgraded):
  PASS_GPU_CORRECT_FULL_COVERAGE / PASS_GPU_CORRECT_PARTIAL_COVERAGE /
  FAIL_EP_NOT_ATTACHED / FAIL_NO_GPU_COMPUTE / FAIL_CORRECTNESS /
  FAIL_RUNTIME / FAIL_COMPILATION / SWAP_SUSPECTED_150 / SHAPE_UNSUPPORTED
Process death / timeout are classified by the supervisor.

#150 probe: for same-shape output groups, if by-name parity fails but the
values match after permuting outputs within the group, we record
swap_suspected with the permutation instead of a bare VALUE_MISMATCH.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402
import onnxruntime as ort  # noqa: E402

from common import parity_check, sha256_file  # noqa: E402  (runner/common.py)
from r2.device_resolver import (  # noqa: E402
    GpuUnavailableError, build_candidates, resolve_device,
)
from r2.dxgi_probe import enumerate_dxgi_adapters  # noqa: E402

EP_LIBRARY = os.path.join(
    r"C:\Program Files\WindowsApps",
    "Microsoft.WinML.AMD.GPU.EP.Framework.2_1.8.65.0_x64__8wekyb3d8bbwe",
    "ExecutionProvider", "amdgpu-ep.dll")

OPT_LEVELS = {
    "DISABLE_ALL": ort.GraphOptimizationLevel.ORT_DISABLE_ALL,
    "BASIC": ort.GraphOptimizationLevel.ORT_ENABLE_BASIC,
    "EXTENDED": ort.GraphOptimizationLevel.ORT_ENABLE_EXTENDED,
    "ALL": ort.GraphOptimizationLevel.ORT_ENABLE_ALL,
}

# predeclared tolerances (FP32; declared BEFORE any run, never relaxed after)
TOLERANCES = {
    "synthetic": {"atol": 1e-4, "rtol": 1e-3},
    "real": {"atol": 1e-2, "rtol": 1e-2},
}


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _metadata_of(dev_entry) -> dict:
    md = getattr(dev_entry.device, "metadata", None)
    if isinstance(md, dict):
        return md
    try:
        import ast
        v = ast.literal_eval(str(md))
        return v if isinstance(v, dict) else {}
    except Exception:
        return {}


def build_identity_view(ep_name: str) -> tuple[dict, list]:
    """Register the AMD EP library and build the resolver's identity view.

    The registration name becomes the factory/ep name in get_ep_devices(),
    so we register under the full EP name (R1 evidence).
    """
    ort.register_execution_provider_library(ep_name, EP_LIBRARY)
    dxgi = enumerate_dxgi_adapters()
    ep_devices = []
    raw = ort.get_ep_devices()
    for d in raw:
        ep_devices.append({
            "ep_name": d.ep_name,
            "vendor_id": getattr(d.device, "vendor_id", None),
            "metadata": _metadata_of(d),
        })
    view = {"dxgi_adapters": dxgi.get("adapters") if dxgi.get("validated") else [],
            "dxgi_validated": dxgi.get("validated"),
            "ep_devices": ep_devices}
    return view, raw


def in_process_canary(model_path: str, ep_name: str):
    """Canary for the resolver: prove THIS process can execute on a device.

    Parity is measured against a CPU session of the same canary model with
    identical input -- never a hardcoded expected value (the first version
    assumed the fixture's add constant and false-negatived itself).
    """
    x = np.full((1, 8), 1.5, dtype=np.float32)

    def canary(candidate):
        try:
            devs = [d for d in ort.get_ep_devices()
                    if d.ep_name == ep_name
                    and str(_metadata_of(d).get("LUID", "")) == str(candidate.luid)]
            if not devs:
                return {"passed": False, "reason": "device not found at canary time"}
            so = ort.SessionOptions()
            so.enable_profiling = False
            so.add_provider_for_devices([devs[0]], {})
            sess = ort.InferenceSession(model_path, so)
            if ep_name not in sess.get_providers():
                return {"passed": False,
                        "reason": f"canary session providers={sess.get_providers()}"}
            out = np.asarray(sess.run(None, {"X": x})[0])
            cpu = ort.InferenceSession(model_path, providers=["CPUExecutionProvider"])
            ref = np.asarray(cpu.run(None, {"X": x})[0])
            ok = np.allclose(out, ref, atol=1e-4, rtol=1e-3)
            del sess, cpu
            return {"passed": bool(ok),
                    "parity_max_abs": float(np.abs(out - ref).max())}
        except Exception as e:
            return {"passed": False, "reason": f"{type(e).__name__}: {e}"}
    return canary


def session_inputs(sess, batch: int, model_name: str = "",
                   input_size: int | None = None) -> dict:
    """Deterministic feeds from live session input specs (no onnx dep).

    Dim policy: dim0 -> batch (or the model's fixed dim); OTHER dynamic
    dims (ORT reports them as 0) -> input_size if given, else 64. Feeding
    1x1 feature maps (the first version's behavior for '?','?' HxW models
    like SCRFD) is a degenerate input, not a real inference shape.

    Domain safety: log/sqrt fixtures require positive inputs (same rule as
    round-1 common.make_feed); feeding negatives produces NaN on GPU AND
    CPU, which would fake a correctness failure.
    """
    rng = np.random.default_rng(1100 + batch)
    domain_safe = any(k in model_name.lower() for k in ("log", "sqrt"))
    default_dyn = input_size if input_size else 64
    feeds = {}
    for idx, i in enumerate(sess.get_inputs()):
        shape = []
        for di, d in enumerate(i.shape):
            if not isinstance(d, int) or d == 0:
                shape.append(batch if di == 0 else default_dyn)
            else:
                shape.append(d)
        if i.type == "tensor(float)":
            arr = rng.standard_normal(shape).astype(np.float32) * 0.5
            if domain_safe:
                arr = np.abs(arr) + 0.5
            feeds[i.name] = arr
        elif i.type == "tensor(float16)":
            feeds[i.name] = (rng.standard_normal(shape) * 0.5).astype(np.float16)
        elif i.type in ("tensor(int64)",):
            feeds[i.name] = rng.integers(0, 4, size=shape).astype(np.int64)
        elif i.type in ("tensor(int32)",):
            feeds[i.name] = rng.integers(0, 4, size=shape).astype(np.int32)
        elif i.type in ("tensor(uint8)",):
            feeds[i.name] = rng.integers(0, 255, size=shape).astype(np.uint8)
        elif i.type in ("tensor(double)",):
            feeds[i.name] = (rng.standard_normal(shape) * 0.5).astype(np.float64)
        else:
            return {"__unsupported_input__": (i.name, i.type)}
    return feeds


def run_case(args) -> dict:
    t0 = time.time()
    receipt = {
        "schema_version": "r2/model_case/1",
        "case_id": f"r2_{os.path.basename(args.model)}_{args.ep}_b{args.batch}_{args.opt}",
        "phase": "r2.2",
        "model": os.path.basename(args.model),
        "model_sha256": sha256_file(args.model),
        "ep": args.ep,
        "batch": args.batch,
        "opt_level": args.opt,
        "tolerance": TOLERANCES["synthetic" if args.family == "synthetic" else "real"],
        "ort_version": ort.__version__,
        "ep_library": EP_LIBRARY,
        "started_utc": utc_now(),
        "real_hardware": True,
    }

    # ---- device resolution (fail-closed) ------------------------------
    view, raw_devices = build_identity_view(args.ep)
    receipt["dxgi_validated"] = view["dxgi_validated"]
    canary = in_process_canary(
        os.path.join(REPO, "fixtures", "identity_add.onnx"), args.ep)
    try:
        res = resolve_device(view, args.ep, canary)
        receipt["device_resolution"] = {
            "luid": res.candidate.luid,
            "dxgi_adapter_number": res.candidate.dxgi_adapter_number,
            "discrete": res.candidate.discrete,
            "basis": res.basis,
            "canary": res.canary,
        }
    except GpuUnavailableError as e:
        receipt["classification"] = "FAIL_DEVICE_RESOLUTION"
        receipt["error"] = str(e)
        return receipt

    # ---- GPU session ---------------------------------------------------
    try:
        devs = [d for d in ort.get_ep_devices()
                if d.ep_name == args.ep
                and str(_metadata_of(d).get("LUID", "")) == str(res.candidate.luid)]
        so = ort.SessionOptions()
        so.enable_profiling = True
        so.profile_file_prefix = args.out.replace(".json", "_profile")
        so.graph_optimization_level = OPT_LEVELS[args.opt]
        so.add_provider_for_devices([devs[0]], {})
        t_sess = time.time()
        sess = ort.InferenceSession(args.model, so)
        receipt["session_create_s"] = round(time.time() - t_sess, 3)
    except Exception as e:
        receipt["classification"] = "FAIL_RUNTIME"
        receipt["error"] = f"session create: {type(e).__name__}: {e}"
        return receipt

    providers = list(sess.get_providers())
    receipt["session_providers"] = providers
    if args.ep not in providers:
        receipt["classification"] = "FAIL_EP_NOT_ATTACHED"
        return receipt

    feeds = session_inputs(sess, args.batch, model_name=args.model,
                           input_size=args.input_size)
    if "__unsupported_input__" in feeds:
        receipt["classification"] = "SHAPE_UNSUPPORTED"
        receipt["error"] = f"unsupported input {feeds['__unsupported_input__']}"
        return receipt
    receipt["input_names"] = list(feeds)
    receipt["input_shapes"] = {k: list(v.shape) for k, v in feeds.items()}

    out_names = [o.name for o in sess.get_outputs()]
    receipt["output_names"] = out_names

    # ---- GPU run --------------------------------------------------------
    try:
        t_run = time.time()
        outs_list = sess.run(None, feeds)
        receipt["run_s"] = round(time.time() - t_run, 3)
    except Exception as e:
        receipt["classification"] = ("FAIL_RUNTIME" if "Supported" not in str(e)
                                     else "FAIL_UNSUPPORTED_OP")
        receipt["error"] = f"run: {type(e).__name__}: {e}"
        return receipt
    gpu_out = {n: np.asarray(v) for n, v in zip(out_names, outs_list)}
    profile_path = sess.end_profiling()

    # ---- CPU reference (same build, same inputs, same opt level) -------
    try:
        so_cpu = ort.SessionOptions()
        so_cpu.graph_optimization_level = OPT_LEVELS[args.opt]
        cpu_sess = ort.InferenceSession(args.model, so_cpu,
                                        providers=["CPUExecutionProvider"])
        cpu_out_list = cpu_sess.run(None, feeds)
        cpu_out = {n: np.asarray(v) for n, v in zip(out_names, cpu_out_list)}
    except Exception as e:
        receipt["classification"] = "FAIL_CPU_REFERENCE"
        receipt["error"] = f"cpu reference: {type(e).__name__}: {e}"
        return receipt

    tol = TOLERANCES["synthetic" if args.family == "synthetic" else "real"]
    par = parity_check(cpu_out, gpu_out, tol["atol"], tol["rtol"])
    receipt["parity"] = par

    # ---- #150 swap suspicion -------------------------------------------
    if not par["all_ok"]:
        bad = [k for k, d in par["per_output"].items() if d.get("status") != "OK"]
        same_shape = {}
        for k in bad:
            key = tuple(cpu_out[k].shape)
            same_shape.setdefault(key, []).append(k)
        for shape, names in same_shape.items():
            if len(names) >= 2:
                import itertools
                for perm in itertools.permutations(names):
                    if perm == tuple(names):
                        continue
                    ok = all(
                        np.allclose(cpu_out[n], gpu_out[p], atol=tol["atol"],
                                    rtol=tol["rtol"])
                        for n, p in zip(names, perm))
                    if ok:
                        receipt["swap_suspected_150"] = {
                            "outputs": names, "permutation": list(perm)}
                        break
        # NaN/Inf scan on failing outputs
        for k in bad:
            if k in gpu_out:
                a = gpu_out[k]
                receipt.setdefault("nonfinite_scan", {})[k] = {
                    "nan": int(np.isnan(a).sum()) if a.dtype.kind == "f" else 0,
                    "inf": int(np.isinf(a).sum()) if a.dtype.kind == "f" else 0,
                }

    # ---- profile attribution -------------------------------------------
    with open(profile_path, encoding="utf-8", errors="replace") as f:
        trace = json.load(f)
    events = trace if isinstance(trace, list) else trace.get("traceEvents", [])
    nodes = [e for e in events if e.get("cat") == "Node"]
    by_prov: dict[str, int] = {}
    ep_ops: dict[str, int] = {}
    for e in nodes:
        p = (e.get("args") or {}).get("provider", "cpu")
        by_prov[p] = by_prov.get(p, 0) + 1
        if p == args.ep:
            op = (e.get("args") or {}).get("op_name") or e.get("name", "?")
            ep_ops[op] = ep_ops.get(op, 0) + 1
    receipt["profile"] = {
        "file": os.path.basename(profile_path),
        "nodes_by_provider": by_prov,
        "ep_node_ops": ep_ops,
        "total_node_events": len(nodes),
    }

    # ---- verdict --------------------------------------------------------
    ep_nodes = by_prov.get(args.ep, 0)
    total = len(nodes)
    if par.get("swap_suspected_150") or not par["all_ok"]:
        receipt["classification"] = ("SWAP_SUSPECTED_150"
                                     if receipt.get("swap_suspected_150")
                                     else "FAIL_CORRECTNESS")
    elif ep_nodes == 0:
        receipt["classification"] = "FAIL_NO_GPU_COMPUTE"
    elif total and ep_nodes == total:
        receipt["classification"] = "PASS_GPU_CORRECT_FULL_COVERAGE"
    else:
        receipt["classification"] = "PASS_GPU_CORRECT_PARTIAL_COVERAGE"
    receipt["gpu_node_ratio"] = round(ep_nodes / total, 4) if total else 0.0
    receipt["finished_utc"] = utc_now()
    receipt["wall_s"] = round(time.time() - t0, 3)
    return receipt


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--ep", required=True,
                    choices=["AMDGPUExecutionProvider", "MIGraphXExecutionProvider"])
    ap.add_argument("--batch", type=int, default=1)
    ap.add_argument("--opt", default="ALL", choices=list(OPT_LEVELS))
    ap.add_argument("--family", default="synthetic",
                    choices=["synthetic", "real"])
    ap.add_argument("--input-size", type=int, default=None,
                    help="default spatial size for non-batch dynamic dims")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    try:
        receipt = run_case(args)
    except Exception:
        receipt = {"classification": "FAIL_RUNTIME",
                   "error": traceback.format_exc(limit=8),
                   "started_utc": utc_now()}
    receipt["finished_utc"] = receipt.get("finished_utc") or utc_now()
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(receipt, f, indent=1, default=str)
    print(receipt.get("classification"), "|",
          receipt.get("gpu_node_ratio", "-"), "|",
          receipt.get("parity", {}).get("all_ok", "-"), flush=True)
    # ORT/WinML leaves non-daemon threads; hard-exit so the supervisor's
    # subprocess.run never hangs on a dead case.
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0 if str(receipt.get("classification", "")).startswith("PASS") else 1)


if __name__ == "__main__":
    main()
