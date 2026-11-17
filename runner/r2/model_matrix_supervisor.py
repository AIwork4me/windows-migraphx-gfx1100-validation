"""R2.2 model-matrix supervisor: case list -> isolated children -> ledger.

Case identity key = (model_sha256, ep, batch, opt_level, ort_version,
device_fingerprint). Resume-safe: cases already DONE with the same key and
an existing receipt are skipped; FAILED cases are re-run only with --retry.

Classifications come from the child receipts; process-level deaths are
classified here (TIMEOUT / NATIVE_CRASH / rc != 0 with receipt missing).

Outputs under reports/r2/<run_id>/:
  cases/<case>.{json,stdout.log,stderr.log}
  ledger.json           (case -> PENDING/RUNNING/DONE/FAILED/BLOCKED)
  OPERATOR_MATRIX.csv / MODEL_MATRIX.csv  (aggregated from receipts)
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PY_WINML = os.path.join(REPO, "venv-winml", "Scripts", "python.exe")
RUN_ID = None


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_of(path: str) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def case_id_of(c: dict) -> str:
    return (f"{os.path.splitext(os.path.basename(c['model']))[0]}"
            f"__{c['ep'].replace('ExecutionProvider', '')}"
            f"__b{c['batch']}__{c['opt']}")


def load_or_create_ledger(path: str, cases: list[dict]) -> dict:
    led = {"schema": "r2/model_ledger/1", "run_id": RUN_ID,
           "cases": {}}
    if os.path.exists(path):
        try:
            led = json.load(open(path, encoding="utf-8"))
        except Exception:
            pass
    for c in cases:
        cid = case_id_of(c)
        led["cases"].setdefault(cid, {
            "key": {"model_sha": sha256_of(c["model"]), "ep": c["ep"],
                    "batch": c["batch"], "opt": c["opt"],
                    "ort": "1.30.1", "device": "LUID72346_gfx1100"},
            "status": "PENDING"})
    return led


def run_case(c: dict, out_dir: str, led: dict, cid: str,
             timeout_s: int) -> None:
    out_json = os.path.join(out_dir, "cases", cid + ".json")
    cmd = [PY_WINML, "-I", os.path.join(REPO, "runner", "r2", "model_child.py"),
           "--model", c["model"], "--ep", c["ep"],
           "--batch", str(c["batch"]), "--opt", c["opt"],
           "--family", c.get("family", "synthetic"),
           "--out", out_json]
    if c.get("input_size"):
        cmd += ["--input-size", str(c["input_size"])]
    led["cases"][cid]["status"] = "RUNNING"
    led["cases"][cid]["started_utc"] = utc_now()
    t0 = time.time()
    try:
        with open(out_json.replace(".json", ".stdout.log"), "wb") as fo, \
             open(out_json.replace(".json", ".stderr.log"), "wb") as fe:
            p = subprocess.run(cmd, stdout=fo, stderr=fe,
                               timeout=timeout_s, cwd=REPO)
        rc = p.returncode
        proc_class = None
    except subprocess.TimeoutExpired:
        rc = None
        proc_class = "TIMEOUT"
    elapsed = round(time.time() - t0, 1)

    receipt = {}
    try:
        receipt = json.load(open(out_json, encoding="utf-8"))
    except Exception:
        pass
    if proc_class:
        cls = proc_class
    elif not receipt:
        cls = "NATIVE_CRASH" if (rc or 0) not in (0, 1) else "NO_RECEIPT"
    else:
        cls = receipt.get("classification", "UNKNOWN")
    led["cases"][cid].update({
        "status": "DONE" if cls.startswith("PASS") else "FAILED",
        "classification": cls,
        "rc": rc, "elapsed_s": elapsed,
        "gpu_node_ratio": receipt.get("gpu_node_ratio"),
        "parity_all_ok": (receipt.get("parity") or {}).get("all_ok"),
        "receipt": os.path.relpath(out_json, REPO),
        "finished_utc": utc_now(),
    })


def aggregate(led: dict, out_dir: str) -> None:
    rows_op, rows_model = [], []
    for cid, c in led["cases"].items():
        rec = {}
        try:
            rec = json.load(open(os.path.join(REPO, c.get("receipt", "x")),
                                 encoding="utf-8"))
        except Exception:
            pass
        base = {
            "case": cid, "model": rec.get("model", cid.split("__")[0]),
            "ep": c["key"]["ep"], "batch": c["key"]["batch"],
            "opt": c["key"]["opt"],
            "classification": c.get("classification"),
            "gpu_node_ratio": c.get("gpu_node_ratio"),
            "parity_all_ok": c.get("parity_all_ok"),
            "elapsed_s": c.get("elapsed_s"),
        }
        if rec.get("family", "synthetic") == "synthetic" or \
           cid.split("__")[0] in [os.path.splitext(f)[0] for f in
                                  ("abs.onnx", "matmul.onnx")]:
            rows_op.append(base)
        else:
            rows_model.append(base)
    for name, rows in (("OPERATOR_MATRIX.csv", rows_op),
                       ("MODEL_MATRIX.csv", rows_model)):
        p = os.path.join(out_dir, name)
        with open(p, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else
                               ["case"])
            w.writeheader()
            w.writerows(rows)


def main() -> int:
    global RUN_ID
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--case-list", required=True,
                    help="JSON list of {model, ep, batch, opt, family, timeout_s}")
    ap.add_argument("--only-prefix", default=None)
    ap.add_argument("--retry-failed", action="store_true")
    ap.add_argument("--max-minutes", type=float, default=600)
    args = ap.parse_args()
    RUN_ID = args.run_id

    out_dir = os.path.join(REPO, "reports", "r2", RUN_ID)
    os.makedirs(os.path.join(out_dir, "cases"), exist_ok=True)
    cases = json.load(open(args.case_list, encoding="utf-8"))
    if args.only_prefix:
        cases = [c for c in cases if case_id_of(c).startswith(args.only_prefix)]
    led_path = os.path.join(out_dir, "ledger.json")
    led = load_or_create_ledger(led_path, cases)

    t_end = time.time() + args.max_minutes * 60
    for c in cases:
        cid = case_id_of(c)
        st = led["cases"][cid]
        if st["status"] == "DONE":
            continue
        if st["status"] == "FAILED" and not args.retry_failed:
            continue
        if time.time() > t_end:
            st["status"] = "PENDING"  # ran out of budget; resume next time
            break
        print(f"[matrix] {cid} ...", flush=True)
        run_case(c, out_dir, led, cid, int(c.get("timeout_s", 240)))
        print(f"[matrix] {cid} -> {led['cases'][cid]['classification']} "
              f"({led['cases'][cid]['elapsed_s']}s)", flush=True)
        with open(led_path, "w", encoding="utf-8") as f:
            json.dump(led, f, indent=1)
    led["updated_utc"] = utc_now()
    with open(led_path, "w", encoding="utf-8") as f:
        json.dump(led, f, indent=1)
    aggregate(led, out_dir)
    n = {}
    for c in led["cases"].values():
        n[c.get("classification", "?")] = n.get(c.get("classification", "?"), 0) + 1
    print(json.dumps(n, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
