#!/usr/bin/env python3
"""R2.1.1-P2 child: ONE (fixture, EP, opt-level) case per process.

Contract-valid control-flow fixtures on the real RX 7900 XTX through the
R2 fail-closed resolver (canary + add_provider_for_devices). GPU PASS
requires: target EP in session providers AND nodes_by_provider[ep] > 0
AND by-name per-output parity vs a CPU session of the SAME ORT build with
identical inputs (int64 outputs compared exactly). Fallback stdout/stderr
markers are captured verbatim for the SSD causality table.

Exit 0 = case executed (classification states the outcome); 2 = infra
failure (resolver/registration), reported separately from EP behavior.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "runner"))

import numpy as np  # noqa: E402
import onnxruntime as ort  # noqa: E402

from common import sha256_file  # noqa: E402
from r2.model_child import (  # noqa: E402
    OPT_LEVELS, build_identity_view, in_process_canary, _metadata_of,
)
from r2.device_resolver import resolve_device  # noqa: E402
from r2_1_1.cpu_oracle_r211 import inputs_for  # noqa: E402

TOL = {"atol": 1e-4, "rtol": 1e-3}  # predeclared, synthetic-fixture class


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture", required=True)
    ap.add_argument("--ep", required=True,
                    choices=["AMDGPUExecutionProvider",
                             "MIGraphXExecutionProvider"])
    ap.add_argument("--opt", default="ALL", choices=list(OPT_LEVELS))
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    model = os.path.join(REPO, "artifacts", "models", "r2_1_1_fixtures",
                         args.fixture)
    receipt = {
        "schema": "r2_1_1/gpu_matrix/1",
        "fixture": args.fixture,
        "model_sha256": sha256_file(model),
        "ep": args.ep, "opt": args.opt, "ort": ort.__version__,
        "tolerance": TOL,
        "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    try:
        view, _raw = build_identity_view(args.ep)
        canary = in_process_canary(
            os.path.join(REPO, "fixtures", "identity_add.onnx"), args.ep)
        res = resolve_device(view, args.ep, canary)
        receipt["device_luid"] = str(res.candidate.luid)
        receipt["device_basis"] = res.basis
        devs = [d for d in ort.get_ep_devices()
                if d.ep_name == args.ep
                and str(_metadata_of(d).get("LUID", "")) == str(res.candidate.luid)]

        so = ort.SessionOptions()
        so.graph_optimization_level = OPT_LEVELS[args.opt]
        so.enable_profiling = True
        so.add_provider_for_devices([devs[0]], {})
        t0 = time.time()
        sess = ort.InferenceSession(model, so)
        receipt["session_create_s"] = round(time.time() - t0, 2)
        receipt["session_providers"] = sess.get_providers()

        # CPU reference, SAME ORT build, identical inputs, by output name
        so_cpu = ort.SessionOptions()
        so_cpu.graph_optimization_level = OPT_LEVELS[args.opt]
        cpu = ort.InferenceSession(model, so_cpu,
                                   providers=["CPUExecutionProvider"])
        out_names = [o.name for o in sess.get_outputs()]

        per_input, parity_ok = [], True
        for k, feeds in enumerate(inputs_for(args.fixture)):
            got = dict(zip(out_names, sess.run(None, feeds)))
            ref = dict(zip(out_names, cpu.run(None, feeds)))
            entry = {"input_index": k, "outputs": {}}
            for oname in out_names:
                g, r = np.asarray(got[oname]), np.asarray(ref[oname])
                if g.dtype.kind == "i":
                    match = (g.shape == r.shape and np.array_equal(g, r))
                    max_abs = 0.0 if match else -1.0
                elif g.shape != r.shape:
                    match, max_abs = False, -1.0
                else:
                    max_abs = float(np.max(np.abs(
                        g.astype(np.float64) - r.astype(np.float64))))
                    match = max_abs <= (TOL["atol"] + TOL["rtol"] * float(
                        np.max(np.abs(r.astype(np.float64)))))
                entry["outputs"][oname] = {"match": bool(match),
                                           "max_abs": max_abs,
                                           "shape": list(g.shape)}
                parity_ok = parity_ok and match
            per_input.append(entry)
        receipt["parity"] = per_input

        profile_path = sess.end_profiling()
        with open(profile_path, encoding="utf-8", errors="replace") as f:
            trace = json.load(f)
        events = trace if isinstance(trace, list) else trace.get("traceEvents", [])
        by_prov = {}
        for e in events:
            if e.get("cat") == "Node":
                p = (e.get("args") or {}).get("provider", "cpu")
                by_prov[p] = by_prov.get(p, 0) + 1
        receipt["nodes_by_provider"] = by_prov
        receipt["total_node_events"] = sum(by_prov.values())

        ep_nodes = by_prov.get(args.ep, 0)
        if args.ep in sess.get_providers() and ep_nodes > 0 and parity_ok:
            if by_prov.get("CPUExecutionProvider", 0) > 0:
                receipt["classification"] = "PASS_GPU_EXECUTED_PARTIAL_PARTITION"
            else:
                receipt["classification"] = "PASS_GPU_EXECUTED"
        elif ep_nodes == 0:
            receipt["classification"] = "FAIL_NO_GPU_COMPUTE"
        elif not parity_ok:
            receipt["classification"] = "FAIL_CORRECTNESS"
        else:
            receipt["classification"] = "FAIL_EP_NOT_ATTACHED"
    except Exception:  # noqa: BLE001
        receipt["classification"] = "ERROR_RUNTIME"
        receipt["error"] = traceback.format_exc(limit=10)

    receipt["finished_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with open(args.out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(receipt, f, indent=1, default=str)
    print(f"r2_1_1 child: {args.fixture} {args.ep} {args.opt} -> "
          f"{receipt['classification']} nbp={receipt.get('nodes_by_provider')}",
          flush=True)
    rc = 0 if receipt["classification"] not in ("ERROR_RUNTIME",) else 2
    sys.stdout.flush(); sys.stderr.flush()
    os._exit(rc)


if __name__ == "__main__":
    main()
