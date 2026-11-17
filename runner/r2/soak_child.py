"""R2.3 soak child: one (model, EP) looped under time budget.

Varying inputs each iteration (seeded by iteration), session lifecycle
(recreate every --recreate-every iterations), heartbeat every 30s, hard
self-timeout via budget check, error classification. Working-set sampled
per heartbeat for slope analysis. Stops at budget, on repeated errors, or
on error budget exhaustion -- never silently continues after failures.
"""

from __future__ import annotations

import argparse
import json
import os
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
    OPT_LEVELS, build_identity_view, in_process_canary, session_inputs,
    _metadata_of,
)
from r2.device_resolver import GpuUnavailableError, resolve_device  # noqa: E402


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def working_set_mb() -> float:
    try:
        import ctypes
        psapi = ctypes.WinDLL("psapi")
        import ctypes.wintypes as wt
        pmc = (ctypes.c_byte * 72)()
        ctypes.cast(pmc, ctypes.POINTER(ctypes.c_ulong))[0] = 72
        k = ctypes.WinDLL("kernel32")
        h = k.GetCurrentProcess()
        psapi.GetProcessMemoryInfo(h, pmc, 72)
        ws = int.from_bytes(bytes(pmc)[16:24], "little")  # WorkingSetSize
        return round(ws / 1048576, 1)
    except Exception:
        return -1.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--ep", required=True)
    ap.add_argument("--minutes", type=float, default=15)
    ap.add_argument("--recreate-every", type=int, default=2000)
    ap.add_argument("--input-size", type=int, default=None)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    receipt = {
        "schema_version": "r2/soak/1",
        "case_id": f"r2_soak_{os.path.basename(args.model)}_{args.ep}",
        "model": os.path.basename(args.model), "model_sha256": sha256_file(args.model),
        "ep": args.ep, "minutes_budget": args.minutes,
        "started_utc": utc_now(), "real_hardware": True,
        "heartbeats": [], "errors": [], "iterations": 0,
        "session_recreates": 0,
    }
    t_end = time.time() + args.minutes * 60
    t_last_hb = 0.0
    err_budget = 3
    classification = "PASS_SOAK_COMPLETED"
    try:
        view, _ = build_identity_view(args.ep)
        canary = in_process_canary(os.path.join(REPO, "fixtures", "identity_add.onnx"),
                                   args.ep)
        res = resolve_device(view, args.ep, canary)
        devs = [d for d in ort.get_ep_devices()
                if d.ep_name == args.ep
                and str(_metadata_of(d).get("LUID", "")) == str(res.candidate.luid)]
        receipt["device_luid"] = res.candidate.luid

        def make_sess():
            so = ort.SessionOptions()
            so.graph_optimization_level = OPT_LEVELS["ALL"]
            so.add_provider_for_devices([devs[0]], {})
            return ort.InferenceSession(args.model, so)

        sess = make_sess()
        it = 0
        ws_series = []
        while time.time() < t_end:
            feeds = session_inputs(sess, 1, model_name=args.model,
                                   input_size=args.input_size)
            if "__unsupported_input__" in feeds:
                classification = "SKIPPED_INPUT_TYPE"
                break
            try:
                sess.run(None, feeds)
                it += 1
            except Exception as e:
                receipt["errors"].append({"iter": it, "t": utc_now(),
                                          "err": f"{type(e).__name__}: {e}"[:300]})
                err_budget -= 1
                if err_budget <= 0:
                    classification = "FAIL_SOAK_ERRORS"
                    break
                try:
                    sess = make_sess()
                    receipt["session_recreates"] += 1
                except Exception:
                    classification = "FAIL_SOAK_ERRORS"
                    break
            if it % args.recreate_every == 0:
                sess = make_sess()
                receipt["session_recreates"] += 1
            now = time.time()
            if now - t_last_hb >= 30:
                ws = working_set_mb()
                ws_series.append(ws)
                receipt["heartbeats"].append({"t": utc_now(), "iter": it,
                                              "ws_mb": ws})
                t_last_hb = now
                with open(args.out, "w", encoding="utf-8") as f:
                    json.dump(receipt, f, indent=1, default=str)  # checkpoint
        receipt["iterations"] = it
        if len(ws_series) >= 2:
            import statistics
            k = statistics.linear_regression(range(len(ws_series)), ws_series).slope \
                if hasattr(statistics, "linear_regression") else None
            receipt["working_set_slope_mb_per_hb"] = round(k, 3) if k is not None \
                else None
            receipt["working_set_first_last_mb"] = [ws_series[0], ws_series[-1]]
    except GpuUnavailableError as e:
        classification = "FAIL_DEVICE_RESOLUTION"
        receipt["error"] = str(e)
    except Exception:
        classification = "FAIL_RUNTIME"
        receipt["error"] = traceback.format_exc(limit=8)

    receipt["classification"] = classification
    receipt["finished_utc"] = utc_now()
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(receipt, f, indent=1, default=str)
    print(classification, "| iters:", receipt["iterations"],
          "| errors:", len(receipt["errors"]), flush=True)
    sys.stdout.flush(); sys.stderr.flush()
    os._exit(0 if classification.startswith("PASS") or
             classification.startswith("SKIPPED") else 1)


if __name__ == "__main__":
    main()
