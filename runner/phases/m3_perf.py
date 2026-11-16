"""M3: performance benchmark on PASS_CORRECTNESS models (CPU vs EP, fair setup).

20 warmup + 100 timed, 3 rounds; cold-init vs steady state separated;
per-session sync timing (session.run is synchronous in ORT python).
"""
from __future__ import annotations
import argparse, os, sys, json, statistics, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
import common as C
import ledger as L
from ep_runner import register_ep, find_winml_ep_library

WARMUP, TIMED, ROUNDS = 20, 100, 3
BENCH = [("identity_add", 1), ("matmul", 1), ("matmul", 4), ("conv_relu", 1), ("softmax", 1)]
EPS = ["AMDGPUExecutionProvider", "MIGraphXExecutionProvider", "DmlExecutionProvider"]


def bench_one(repo, ep, model, b, led):
    import onnxruntime as ort
    man = C.write_fixtures()
    meta = man[model]
    path = os.path.join(C.FIXTURES, model + ".onnx")
    feeds = C.make_feed(model, meta, b)
    res = {"ep": ep, "model": model, "batch": b, "warmup": WARMUP, "timed": TIMED}
    try:
        # CPU
        so = ort.SessionOptions()
        sess = ort.InferenceSession(path, sess_options=so, providers=["CPUExecutionProvider"])
        for _ in range(WARMUP):
            sess.run(None, feeds)
        t = []
        for _ in range(TIMED):
            t0 = time.perf_counter(); sess.run(None, feeds); t.append((time.perf_counter() - t0) * 1e3)
        res["cpu_ms"] = {"p50": round(statistics.median(t), 3), "mean": round(statistics.mean(t), 3),
                         "p95": round(sorted(t)[int(len(t) * .95) - 1], 3), "n": len(t)}
        # EP (skip for the CPU baseline row)
        if ep == "CPUExecutionProvider":
            res["status"] = "PASS"
            L.upsert_case(led, {"id": f"m3/{ep}/{model}/b{b}", "phase": "m3", **res})
            return res
        t0 = time.perf_counter()
        so = ort.SessionOptions(); so.log_severity_level = 3
        so.add_provider_for_devices(["GPU"], ep)
        sess = ort.InferenceSession(path, sess_options=so, providers=["CPUExecutionProvider"])
        res["session_create_ms"] = round((time.perf_counter() - t0) * 1e3, 1)
        first0 = time.perf_counter()
        sess.run(None, feeds)
        res["first_run_ms"] = round((time.perf_counter() - first0) * 1e3, 1)
        for _ in range(WARMUP):
            sess.run(None, feeds)
        all_rounds = []
        for _ in range(ROUNDS):
            t = []
            for _ in range(TIMED):
                t0 = time.perf_counter(); sess.run(None, feeds); t.append((time.perf_counter() - t0) * 1e3)
            all_rounds.append(t)
        flat = [x for r in all_rounds for x in r]
        res["ep_ms"] = {"p50": round(statistics.median(flat), 3), "mean": round(statistics.mean(flat), 3),
                        "p95": round(sorted(flat)[int(len(flat) * .95) - 1], 3),
                        "n": len(flat), "rounds": ROUNDS}
        res["get_providers"] = list(sess.get_providers())
        res["speedup_p50"] = round(res["cpu_ms"]["p50"] / res["ep_ms"]["p50"], 2) if res["ep_ms"]["p50"] else None
        res["status"] = "PASS" if ep in res["get_providers"] else "FAIL_EP_NOT_IN_PROVIDERS"
    except Exception as e:
        res["status"] = "FAIL"; res["exception"] = repr(e)[:400]
    L.upsert_case(led, {"id": f"m3/{ep}/{model}/b{b}", "phase": "m3", **res})
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=C.REPO)
    args = ap.parse_args()
    repo = os.path.abspath(args.repo)
    led = os.path.join(repo, "reports", "ledger.json")
    receipt = C.new_receipt("m3", "m3_perf_run")
    receipt["bench"] = []
    # CPU-only baseline entry
    for ep in ["CPUExecutionProvider"] + EPS:
        if ep != "CPUExecutionProvider":
            register_ep(ep, [find_winml_ep_library(ep)])
        for model, b in BENCH:
            r = bench_one(repo, ep, model, b, led)
            receipt["bench"].append(r)
            print(r["id"] if "id" in r else f"{ep}/{model}/b{b}", r.get("status"),
                  "cpu_p50", (r.get("cpu_ms") or {}).get("p50"), "ep_p50", (r.get("ep_ms") or {}).get("p50"), flush=True)
    C.save_receipt("m3", receipt)
    print("M3_PERF_DONE")


if __name__ == "__main__":
    rc = main()
    sys.stdout.flush(); sys.stderr.flush()
    os._exit(rc if isinstance(rc, int) else 0)  # hard exit: skip interpreter shutdown (WinML/ORT leave non-daemon threads)
