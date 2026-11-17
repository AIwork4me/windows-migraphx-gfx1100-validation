"""R2.3 performance child: cold/warm latency + throughput on one (model, EP).

Only run for models whose GPU correctness already PASSED (mission rule:
never report CPU-fallback latency as GPU speed).

Measures, all in ONE fresh process:
  cold: EP registration -> session create -> first run (compile) separately
  warm: warmup W runs, then N timed Session.run (no pre/post), p50/p95/
        mean/std/min/max, throughput, wall total
  env : working set before/after, input/output shapes, node ratio (from a
        final profiled run), CPU comparison with the same session shape
GPU VRAM/utilization: NOT_MEASURED unless a reliable counter is available
(none is exposed to user mode here) -- never fabricated.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
import traceback
from datetime import datetime, timezone

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import onnxruntime as ort  # noqa: E402

from common import sha256_file  # noqa: E402
from r2.model_child import (  # noqa: E402
    EP_LIBRARY, OPT_LEVELS, build_identity_view, in_process_canary,
    session_inputs, _metadata_of,
)
from r2.device_resolver import GpuUnavailableError, resolve_device  # noqa: E402


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def working_set_mb() -> float:
    try:
        import ctypes
        import ctypes.wintypes as wt

        class PMC(ctypes.Structure):
            _fields_ = [("cb", wt.DWORD), ("PageFaultCount", wt.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t)]
        pmc = PMC()
        pmc.cb = ctypes.sizeof(PMC)
        ctypes.WinDLL("kernel32").GetProcessMemoryInfo(
            ctypesGetCurrentProcess(), ctypes.byref(pmc), pmc.cb)
        return round(pmc.WorkingSetSize / 1048576, 1)
    except Exception:
        return -1.0


def ctypesGetCurrentProcess():
    import ctypes
    k = ctypes.WinDLL("kernel32")
    h = k.GetCurrentProcess()
    return ctypes.c_void_p(h)


def timed_runs(sess, feeds, n: int) -> dict:
    lat = []
    for _ in range(n):
        t0 = time.perf_counter()
        sess.run(None, feeds)
        lat.append((time.perf_counter() - t0) * 1000.0)
    lat.sort()
    return {
        "n": n,
        "p50_ms": round(statistics.median(lat), 3),
        "p95_ms": round(lat[max(0, int(n * 0.95) - 1)], 3),
        "mean_ms": round(statistics.fmean(lat), 3),
        "std_ms": round(statistics.pstdev(lat), 3),
        "min_ms": round(lat[0], 3), "max_ms": round(lat[-1], 3),
        "throughput_qps": round(1000.0 / (statistics.fmean(lat) or 1e-9), 2),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--ep", required=True)
    ap.add_argument("--batch", type=int, default=1)
    ap.add_argument("--warmup", type=int, default=10)
    ap.add_argument("--runs", type=int, default=100)
    ap.add_argument("--cpu-runs", type=int, default=30)
    ap.add_argument("--input-size", type=int, default=None)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    receipt = {
        "schema_version": "r2/perf/1",
        "case_id": f"r2_perf_{os.path.basename(args.model)}_{args.ep}_b{args.batch}",
        "model": os.path.basename(args.model),
        "model_sha256": sha256_file(args.model),
        "ep": args.ep, "batch": args.batch,
        "warmup": args.warmup, "runs": args.runs,
        "ort_version": ort.__version__,
        "started_utc": utc_now(), "real_hardware": True,
        "threads": "default",
    }
    try:
        t0 = time.perf_counter()
        view, _ = build_identity_view(args.ep)
        t_reg = time.perf_counter() - t0
        canary = in_process_canary(os.path.join(REPO, "fixtures", "identity_add.onnx"),
                                   args.ep)
        res = resolve_device(view, args.ep, canary)
        devs = [d for d in ort.get_ep_devices()
                if d.ep_name == args.ep
                and str(_metadata_of(d).get("LUID", "")) == str(res.candidate.luid)]

        ws0 = working_set_mb()
        so = ort.SessionOptions()
        so.graph_optimization_level = OPT_LEVELS["ALL"]
        so.add_provider_for_devices([devs[0]], {})
        t0 = time.perf_counter()
        sess = ort.InferenceSession(args.model, so)
        t_sess = time.perf_counter() - t0

        feeds = session_inputs(sess, args.batch, model_name=args.model,
                               input_size=args.input_size)
        if "__unsupported_input__" in feeds:
            receipt["classification"] = "SKIPPED_INPUT_TYPE"
            raise RuntimeError(str(feeds))
        t0 = time.perf_counter()
        sess.run(None, feeds)
        t_first = time.perf_counter() - t0

        for _ in range(args.warmup):
            sess.run(None, feeds)
        gpu = timed_runs(sess, feeds, args.runs)
        ws1 = working_set_mb()

        # CPU comparison, same build/graph/inputs
        cpu = ort.InferenceSession(args.model, providers=["CPUExecutionProvider"])
        for _ in range(min(args.warmup, 5)):
            cpu.run(None, feeds)
        cpu_stats = timed_runs(cpu, feeds, args.cpu_runs)

        # node attribution via a short profiled run
        so2 = ort.SessionOptions()
        so2.enable_profiling = True
        so2.profile_file_prefix = args.out.replace(".json", "_profile")
        so2.graph_optimization_level = OPT_LEVELS["ALL"]
        so2.add_provider_for_devices([devs[0]], {})
        s2 = ort.InferenceSession(args.model, so2)
        s2.run(None, feeds)
        prof = s2.end_profiling()
        with open(prof, encoding="utf-8", errors="replace") as f:
            trace = json.load(f)
        events = trace if isinstance(trace, list) else trace.get("traceEvents", [])
        nodes = [e for e in events if e.get("cat") == "Node"]
        ep_nodes = sum(1 for e in nodes
                       if (e.get("args") or {}).get("provider") == args.ep)

        receipt.update({
            "classification": "PASS_PERF_MEASURED" if ep_nodes else "FAIL_NO_GPU_COMPUTE",
            "cold": {"register_s": round(t_reg, 3),
                     "session_create_s": round(t_sess, 3),
                     "first_run_s": round(t_first, 3)},
            "gpu": gpu, "cpu": cpu_stats,
            "gpu_vs_cpu_speedup_mean": round(cpu_stats["mean_ms"] / gpu["mean_ms"], 2)
            if gpu["mean_ms"] else None,
            "working_set_mb": {"before": ws0, "after": ws1},
            "gpu_vram": "NOT_MEASURED",
            "ep_node_ratio_profiled_run": round(ep_nodes / len(nodes), 4) if nodes else 0,
        })
    except GpuUnavailableError as e:
        receipt["classification"] = "FAIL_DEVICE_RESOLUTION"
        receipt["error"] = str(e)
    except Exception:
        receipt.setdefault("classification", "FAIL_RUNTIME")
        receipt["error"] = traceback.format_exc(limit=8)

    receipt["finished_utc"] = utc_now()
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(receipt, f, indent=1, default=str)
    print(receipt.get("classification"), "| gpu p50:",
          (receipt.get("gpu") or {}).get("p50_ms"), "ms | cpu p50:",
          (receipt.get("cpu") or {}).get("p50_ms"), "ms", flush=True)
    sys.stdout.flush(); sys.stderr.flush()
    os._exit(0 if str(receipt.get("classification", "")).startswith("PASS") else 1)


if __name__ == "__main__":
    main()
