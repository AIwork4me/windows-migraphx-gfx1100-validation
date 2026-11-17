#!/usr/bin/env python3
"""R2.1-D SSD partition forensics child: one (model, EP, opt) case per process.

Reproduces the R2 ssd_mobilenet_v1_10 whole-graph-CPU observation with
profiler attribution, saves the optimized model for graph inspection, and
supports minimal fixture models built by ssd_build_fixtures.py to isolate
candidate partition barriers (opset-10 Loop control flow vs dynamic input
dims vs plain conv chain).

Writes one receipt JSON. Exit 0 = case executed (classification states the
outcome); exit 2 = environment/infra failure.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "runner"))

import onnxruntime as ort  # noqa: E402

from common import sha256_file  # noqa: E402
from r2.model_child import (  # noqa: E402
    OPT_LEVELS, build_identity_view, in_process_canary, _metadata_of,
)
from r2.device_resolver import resolve_device  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--ep", required=True,
                    help="EP name, or DML_CONTROL to use built-in DML")
    ap.add_argument("--opt", default="ALL", choices=list(OPT_LEVELS))
    ap.add_argument("--out", required=True)
    ap.add_argument("--save-optimized", default=None)
    args = ap.parse_args()

    receipt = {
        "schema": "r21/ssd_partition/1",
        "model": os.path.basename(args.model),
        "model_sha256": sha256_file(args.model),
        "ep": args.ep, "opt": args.opt,
        "ort": ort.__version__,
        "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    try:
        so = ort.SessionOptions()
        so.graph_optimization_level = OPT_LEVELS[args.opt]
        so.enable_profiling = True
        if args.save_optimized:
            so.optimized_model_filepath = args.save_optimized

        t0 = time.time()
        if args.ep == "DML_CONTROL":
            sess = ort.InferenceSession(args.model, so,
                                        providers=["DmlExecutionProvider",
                                                   "CPUExecutionProvider"])
        else:
            view, _ = build_identity_view(args.ep)
            canary = in_process_canary(
                os.path.join(REPO, "fixtures", "identity_add.onnx"), args.ep)
            res = resolve_device(view, args.ep, canary)
            receipt["device_luid"] = str(res.candidate.luid)
            devs = [d for d in ort.get_ep_devices()
                    if d.ep_name == args.ep
                    and str(_metadata_of(d).get("LUID", "")) == str(res.candidate.luid)]
            so.add_provider_for_devices([devs[0]], {})
            sess = ort.InferenceSession(args.model, so)
        receipt["session_create_s"] = round(time.time() - t0, 2)
        receipt["session_providers"] = sess.get_providers()

        # deterministic input from live input specs (R2 rule: other dynamic
        # dims -> explicit size; SSD input is NHWC 1x300x300x3)
        rng = np.random.default_rng(2026)
        feeds = {}
        for i in sess.get_inputs():
            shape = [d if isinstance(d, int) and d > 0 else 0 for d in i.shape]
            if "ssd" in args.model:
                shape = [1, 300, 300, 3]
            for j, d in enumerate(shape):
                if d == 0:
                    shape[j] = 8
            feeds[i.name] = rng.standard_normal(shape).astype(np.float32) * 0.1
        sess.run(None, feeds)
        profile_path = sess.end_profiling()
        receipt["profile_file"] = os.path.basename(profile_path)

        with open(profile_path, encoding="utf-8", errors="replace") as f:
            trace = json.load(f)
        events = trace if isinstance(trace, list) else trace.get("traceEvents", [])
        nodes = [e for e in events if e.get("cat") == "Node"]
        by_prov, ops = {}, {}
        for e in nodes:
            p = (e.get("args") or {}).get("provider", "cpu")
            by_prov[p] = by_prov.get(p, 0) + 1
            op = (e.get("args") or {}).get("op_name") or e.get("name", "?")
            ops[f"{p}:{op}"] = ops.get(f"{p}:{op}", 0) + 1
        receipt["nodes_by_provider"] = by_prov
        receipt["total_node_events"] = len(nodes)
        receipt["cpu_op_distribution"] = dict(sorted(
            {k.split(":", 1)[1]: v for k, v in ops.items()
             if k.startswith("cpu:")}.items(), key=lambda kv: -kv[1])[:25])

        ep_nodes = by_prov.get(args.ep, 0)
        if args.ep == "DML_CONTROL":
            ep_nodes = by_prov.get("DmlExecutionProvider", 0)
        if ep_nodes == 0:
            receipt["classification"] = "FAIL_NO_GPU_COMPUTE"
        elif by_prov.get("CPUExecutionProvider", 0) > 0:
            receipt["classification"] = "PARTIAL_GPU_PARTITION"
        else:
            receipt["classification"] = "PASS_GPU_CORRECT_FULL_COVERAGE"
    except Exception:  # noqa: BLE001
        receipt["classification"] = "ERROR_RUNTIME"
        receipt["error"] = traceback.format_exc(limit=8)

    receipt["finished_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with open(args.out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(receipt, f, indent=1, default=str)
    print(args.ep, args.opt, "->", receipt["classification"],
          receipt.get("nodes_by_provider"), flush=True)
    rc = 0 if receipt["classification"] not in ("ERROR_RUNTIME",) else 2
    sys.stdout.flush(); sys.stderr.flush()
    os._exit(rc)


if __name__ == "__main__":
    main()
