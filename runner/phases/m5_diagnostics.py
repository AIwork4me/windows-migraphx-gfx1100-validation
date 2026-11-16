"""M5: regression diagnostics.

- #151 probe: classic providers=[EP] without plugin registration -> what
  actually happens (exception vs silent CPU fallback); contrast with the
  device plugin API route. Verbatim errors recorded.
- #150 mini reproducer: synthetic only (nine_out/two_out_same_shape already in
  M1; here we dump a standalone open-source reproducer file set).
- WindowsML#15-informed guard: 7900XTX native crash detector hook = supervisor
  circuit breaker (documented here, exercised by M3 soak).
- support matrix CSV generation from ledger.
"""
from __future__ import annotations
import argparse, csv, os, sys, json, traceback
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
import common as C
import ledger as L
from ep_runner import register_ep, find_winml_ep_library


def probe_151(repo: str, ep: str, led: str):
    """#151 probe via file handshake (child writes JSON; no pipes, hard exits)."""
    import subprocess, sys as _sys
    out_path = os.path.join(repo, "reports", "m5", f"probe151_{ep}.json")
    if os.path.exists(out_path):
        os.remove(out_path)
    child = os.path.join(repo, "runner", "phases", "probe151_child.py")
    p = subprocess.Popen([_sys.executable, "-u", child, repo, ep],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         cwd=repo)
    out = {"ep": ep, "probe": "providers=[EP] classic string, unregistered",
           "timeout_s": 90}
    try:
        p.wait(timeout=90)
        with open(out_path, encoding="utf-8") as f:
            childres = json.load(f)
        out.update(childres)
        if childres.get("stage") == "exception":
            out["verdict"] = "EXCEPTION_ON_SESSION_CREATE"
        else:
            gpu = (childres.get("placement") or {}).get("gpu_nodes", 0)
            if ep in childres.get("get_providers", []) and gpu >= 1:
                out["verdict"] = "GPU_EXECUTED"
            elif ep in childres.get("get_providers", []):
                out["verdict"] = "EP_LISTED_NO_GPU_NODES"
            else:
                out["verdict"] = "SILENT_CPU_FALLBACK"
                out["note"] = ("classic providers=[...] silently drops the plugin EP "
                               "(issue #151 behavior); no GPU execution claimed")
    except subprocess.TimeoutExpired:
        p.kill()
        out["verdict"] = "HANG_TIMEOUT_90S"
        out["note"] = "session creation with unregistered plugin EP did not return in 90s"
    except FileNotFoundError:
        out["verdict"] = "CHILD_PRODUCED_NO_FILE"
        out["rc"] = p.poll()
    L.upsert_case(led, {"id": f"m5/probe151/{ep}", "phase": "m5",
                        "status": "INCONCLUSIVE",  # informational probe
                        "detail": {k: out.get(k) for k in ("verdict", "exception", "get_providers", "note")}})
    return out


def support_matrix(repo: str, out_csv: str):
    led = L.load_ledger(os.path.join(repo, "reports", "ledger.json"))
    rows = []
    for c in led.get("cases", []):
        if c.get("phase") in ("m0", "m1", "m2", "m3"):
            rows.append({
                "case": c.get("id"), "phase": c.get("phase"),
                "ep": c.get("ep", ""), "model": c.get("model", ""),
                "batch": c.get("batch", ""), "status": c.get("status", ""),
                "gpu_nodes": (c.get("placement") or {}).get("gpu_nodes", ""),
                "total_nodes": (c.get("placement") or {}).get("total_node_events", ""),
                "parity_ok": (c.get("parity_summary") or c.get("parity") or {}).get(
                    "all_ok", c.get("parity", {}).get("all_ok", "") if isinstance(c.get("parity"), dict) else ""),
                "note": c.get("reason", ""),
            })
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["case", "phase", "ep", "model", "batch",
                                          "status", "gpu_nodes", "total_nodes",
                                          "parity_ok", "note"])
        w.writeheader()
        w.writerows(rows)
    return len(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=C.REPO)
    args = ap.parse_args()
    repo = os.path.abspath(args.repo)
    led = os.path.join(repo, "reports", "ledger.json")
    receipt = C.new_receipt("m5", "m5_diagnostics_run")
    receipt["probe_151"] = []
    for ep in ("AMDGPUExecutionProvider", "MIGraphXExecutionProvider"):
        receipt["probe_151"].append(probe_151(repo, ep, led))
        print("probe151", ep, receipt["probe_151"][-1].get("verdict"), flush=True)
    # standalone #150 reproducer dump (synthetic, open-source)
    repro_dir = os.path.join(repo, "reports", "m5", "repro_150")
    os.makedirs(repro_dir, exist_ok=True)
    man = C.write_fixtures()
    for name in ("nine_out.onnx", "two_out_same_shape.onnx"):
        src = os.path.join(C.FIXTURES, name)
        if os.path.exists(src):
            import shutil
            shutil.copy(src, repro_dir)
    with open(os.path.join(repro_dir, "RUN.md"), "w", encoding="utf-8") as f:
        f.write("""# Issue #150 reproducer (synthetic)
Run: python runner/phases/m1_multi_output.py --repo .
nine_out.onnx: 9 outputs, offsets +1..+9. two_out_same_shape.onnx: A=X+1,B=X+10.
A mismatch by name (A matching B's values) indicates output misrouting.
Expected/actual JSON under reports/m1/mismatch/ when a failure occurs.
""")
    n = support_matrix(repo, os.path.join(repo, "reports", "support_matrix.csv"))
    receipt["support_matrix_rows"] = n
    C.save_receipt("m5", receipt)
    print("M5_DIAGNOSTICS_DONE")


if __name__ == "__main__":
    rc = main()
    sys.stdout.flush(); sys.stderr.flush()
    os._exit(rc if isinstance(rc, int) else 0)  # hard exit: skip interpreter shutdown (WinML/ORT leave non-daemon threads)
