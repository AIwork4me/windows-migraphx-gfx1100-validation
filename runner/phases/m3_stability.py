"""M3: stability soak (bounded duration): repeated inference, session churn,
batch sweep, working-set tracking via ctypes. Native crash -> supervisor marks
CRASH_NATIVE and applies circuit breaker.
"""
from __future__ import annotations
import argparse, ctypes, os, sys, time, datetime
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
import common as C
import ledger as L
from ep_runner import register_ep, find_winml_ep_library


class PSAPI(ctypes.Structure):
    _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
                ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]


def working_set_mb() -> float:
    pm = PSAPI(); pm.cb = ctypes.sizeof(PSAPI)
    k32 = ctypes.windll.kernel32
    k32.GetCurrentProcess.restype = ctypes.c_void_p
    h = k32.GetCurrentProcess()
    psapi = ctypes.windll.psapi
    psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
    psapi.GetProcessMemoryInfo.restype = ctypes.c_int
    if psapi.GetProcessMemoryInfo(h, ctypes.byref(pm), pm.cb):
        return round(pm.WorkingSetSize / (1 << 20), 1)
    return -1.0  # UNKNOWN if the counter is unavailable


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=C.REPO)
    ap.add_argument("--minutes", type=float, default=30)
    args = ap.parse_args()
    repo = os.path.abspath(args.repo)
    led = os.path.join(repo, "reports", "ledger.json")
    import onnxruntime as ort
    man = C.write_fixtures()
    receipt = C.new_receipt("m3", "m3_stability_run")
    deadline = datetime.datetime.now() + datetime.timedelta(minutes=args.minutes)
    stats = {"inferences": 0, "sessions_created": 0, "errors": 0, "ws_peak_mb": 0.0,
             "duration_min": args.minutes}
    # pick first EP that passes a canary session (AMD -> MIGraphX -> DML control)
    ep = None
    for cand in ("AMDGPUExecutionProvider", "MIGraphXExecutionProvider", "DmlExecutionProvider"):
        if not register_ep(cand, [find_winml_ep_library(cand)]).get("ok"):
            continue
        try:
            so_t = ort.SessionOptions(); so_t.log_severity_level = 3
            from ep_runner import add_ep_for_devices
            if add_ep_for_devices(so_t, ort, cand) == 0:
                continue
            feeds_t = C.make_feed("identity_add", man["identity_add"], 1)
            s_t = ort.InferenceSession(os.path.join(C.FIXTURES, "identity_add.onnx"),
                                       sess_options=so_t)
            s_t.run(None, feeds_t)
            ep = cand
            break
        except Exception:
            continue
    if not ep:
        L.upsert_case(led, {"id": "m3/stability", "phase": "m3",
                            "status": "BLOCKED_DEPENDENCY", "reason": "no EP canary-passed"})
        C.save_receipt("m3", receipt); return 0
    receipt["ep"] = ep
    t_start = time.time()
    i = 0
    while datetime.datetime.now() < deadline:
        i += 1
        try:
            # session churn every 5th iteration
            if i % 5 == 1 or "sess" not in dir():
                so = ort.SessionOptions(); so.log_severity_level = 3
                sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
                from ep_runner import add_ep_for_devices
                _n = add_ep_for_devices(so, ort, ep)
                if _n == 0:
                    raise RuntimeError(f"EP {ep} not registered")
                model = "conv_relu" if i % 10 == 1 else "matmul"
                b = (i // 10) % 4 + 1
                feeds = C.make_feed(model, man[model], b)
                sess = ort.InferenceSession(os.path.join(C.FIXTURES, model + ".onnx"),
                                            sess_options=so)
                stats["sessions_created"] += 1
            sess.run(None, feeds)
            stats["inferences"] += 1
        except Exception as e:
            stats["errors"] += 1
            if stats["errors"] <= 3:
                print("ERR:", repr(e)[:200], flush=True)
        ws = working_set_mb()
        if ws > stats["ws_peak_mb"]:
            stats["ws_peak_mb"] = ws
        if i % 500 == 0:
            print(f"iter {i} ws={ws}MB inf={stats['inferences']}", flush=True)
    stats["actual_minutes"] = round((time.time() - t_start) / 60, 1)
    receipt["stats"] = stats
    status = "PASS" if stats["errors"] == 0 else ("PASS_WITH_ERRORS" if stats["errors"] < 5 else "FAIL")
    L.upsert_case(led, {"id": f"m3/stability/{ep}", "phase": "m3", "status": status, **stats})
    C.save_receipt("m3", receipt)
    print("M3_STABILITY_DONE")


if __name__ == "__main__":
    rc = main()
    sys.stdout.flush(); sys.stderr.flush()
    os._exit(rc if isinstance(rc, int) else 0)  # hard exit: skip interpreter shutdown (WinML/ORT leave non-daemon threads)
