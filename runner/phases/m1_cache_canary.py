"""M1: MIGraphX/AMDGPU EP cache canary (upstream issue #117 crash guard).

Runs a tiny graph twice with cache options enabled inside THIS dedicated
subprocess (supervisor isolates us). If we die, the supervisor records
CRASH_NATIVE and disables the option set.
"""
from __future__ import annotations
import argparse, os, sys, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
import common as C
import ledger as L
import onnxruntime as ort
from ep_runner import register_ep, find_winml_ep_library

CACHE_OPT_SETS = [
    {"label": "migraphx_cache", "opts": {"migraphx_cache_dir": r"C:\AIwork4me\migraphx_cache"}},
    {"label": "cache_enabled_generic", "opts": {"cache_enabled": "1"}},
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=C.REPO)
    args = ap.parse_args()
    repo = os.path.abspath(args.repo)
    led = os.path.join(repo, "reports", "ledger.json")
    man = C.write_fixtures()
    receipt = C.new_receipt("m1", "m1_cache_canary_run")
    receipt["sets"] = []
    for ep in ("AMDGPUExecutionProvider", "MIGraphXExecutionProvider"):
        reg = register_ep(ep, [find_winml_ep_library(ep)])
        if not reg.get("ok"):
            L.upsert_case(led, {"id": f"m1-cache/{ep}", "phase": "m1",
                                "status": "BLOCKED_DEPENDENCY", "reason": f"not registered: {reg}"})
            continue
        for cs in CACHE_OPT_SETS:
            cid = f"m1-cache/{ep}/{cs['label']}"
            rec = {"id": cid, "ep": ep, "options": cs["opts"]}
            try:
                feeds = C.make_feed("identity_add", man["identity_add"], 1)
                so = ort.SessionOptions()
                so.log_severity_level = 3
                from ep_runner import add_ep_for_devices as _add; _n = _add(so, ort, ep, cs["opts"])
                sess = ort.InferenceSession(os.path.join(C.FIXTURES, "identity_add.onnx"),
                                            sess_options=so, providers=["CPUExecutionProvider"])
                outs = sess.run(None, feeds)
                provs = list(sess.get_providers())
                rec.update({"run1": "ok", "get_providers": provs})
                # second run, same options (cache reuse path)
                so2 = ort.SessionOptions()
                so2.log_severity_level = 3
                _add(so2, ort, ep, cs["opts"])
                sess2 = ort.InferenceSession(os.path.join(C.FIXTURES, "identity_add.onnx"),
                                             sess_options=so2, providers=["CPUExecutionProvider"])
                outs2 = sess2.run(None, feeds)
                par = C.parity_check(
                    {o.name: a for o, a in zip(sess.get_outputs(), outs)},
                    {o.name: a for o, a in zip(sess2.get_outputs(), outs2)}, **C.TOL_FP32)
                rec.update({"run2": "ok", "parity": par["all_ok"]})
                rec["status"] = "PASS" if par["all_ok"] and ep in provs else "FAIL"
            except Exception as e:
                rec.update({"status": "FAIL", "exception": repr(e)[:500]})
            L.upsert_case(led, {"id": cid, "phase": "m1", **{k: rec.get(k) for k in ("status", "options", "exception")}})
            receipt["sets"].append(rec)
            print(cid, rec.get("status"), flush=True)
    C.save_receipt("m1", receipt)
    print("M1_CACHE_CANARY_DONE")


if __name__ == "__main__":
    rc = main()
    sys.stdout.flush(); sys.stderr.flush()
    os._exit(rc if isinstance(rc, int) else 0)  # hard exit: skip interpreter shutdown (WinML/ORT leave non-daemon threads)
