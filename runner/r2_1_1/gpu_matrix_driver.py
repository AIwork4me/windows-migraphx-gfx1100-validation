#!/usr/bin/env python3
"""R2.1.1-P2 driver: fixture x EP x opt matrix, one subprocess per case.

Spawns runner/r2_1_1/gpu_matrix_child.py with the venv-winml interpreter
(the ORT build that carries the WinML AMD plugin EPs). Captures raw
stdout/stderr per case (fallback markers feed the causality table),
enforces a per-case timeout, and writes VALID_FIXTURE_MATRIX.csv +
matrix JSON. Classification of child death/timeout is the driver's job,
never silently retried.
"""
from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PY = os.path.join(REPO, "venv-winml", "Scripts", "python.exe")
FIXTURES = [
    "valid_loop_carried.onnx", "valid_loop_scan.onnx",
    "valid_conv_then_loop.onnx", "valid_nonzero_static.onnx",
    "valid_nonzero_dynamic.onnx", "valid_loop_plus_nonzero.onnx",
]
EPS = ["AMDGPUExecutionProvider", "MIGraphXExecutionProvider"]
OPTS = ["DISABLE_ALL", "ALL"]
CASE_TIMEOUT_S = 300


def main() -> int:
    run_id = sys.argv[1]
    out_dir = os.path.join(REPO, "reports", "r2_1_1", run_id, "gpu_matrix")
    os.makedirs(out_dir, exist_ok=True)
    cases = []
    for fx in FIXTURES:
        for ep in EPS:
            for opt in OPTS:
                stem = f"{fx[:-5]}__{ep}__{opt}"
                rcpt = os.path.join(out_dir, stem + ".json")
                log_out = os.path.join(out_dir, stem + ".stdout.log")
                log_err = os.path.join(out_dir, stem + ".stderr.log")
                t0 = time.time()
                try:
                    with open(log_out, "wb") as fo, open(log_err, "wb") as fe:
                        p = subprocess.run(
                            [PY, "-I", os.path.join(
                                REPO, "runner", "r2_1_1", "gpu_matrix_child.py"),
                             "--fixture", fx, "--ep", ep, "--opt", opt,
                             "--out", rcpt],
                            stdout=fo, stderr=fe, timeout=CASE_TIMEOUT_S,
                            cwd=REPO)
                    rc = p.returncode
                except subprocess.TimeoutExpired:
                    rc = -9
                dt = round(time.time() - t0, 1)
                rec = json.load(open(rcpt, encoding="utf-8")) \
                    if os.path.isfile(rcpt) else {}
                if not rec:
                    rec = {"fixture": fx, "ep": ep, "opt": opt,
                           "classification": "CHILD_DIED_OR_TIMEOUT",
                           "child_rc": rc}
                rec["child_rc"] = rc
                rec["wall_s"] = dt
                with open(rcpt, "w", encoding="utf-8", newline="\n") as f:
                    json.dump(rec, f, indent=1, default=str)
                cases.append(rec)
                print(f"{stem:70s} -> {rec['classification']:36s} "
                      f"rc={rc} {dt}s", flush=True)

    with open(os.path.join(out_dir, "VALID_FIXTURE_MATRIX.csv"), "w",
              encoding="utf-8", newline="\n") as f:
        w = csv.writer(f)
        w.writerow(["fixture", "ep", "opt", "classification", "child_rc",
                    "wall_s", "ep_nodes", "cpu_nodes", "parity_all_ok",
                    "session_providers", "device_luid"])
        for c in cases:
            nbp = c.get("nodes_by_provider", {}) or {}
            par = None
            pv = c.get("parity")
            if isinstance(pv, list) and pv and all(
                    isinstance(i, dict) and isinstance(i.get("outputs"), dict)
                    for i in pv):
                par = all(all(o.get("match") is True
                              for o in i["outputs"].values()) for i in pv)
            w.writerow([c["fixture"], c["ep"], c["opt"], c["classification"],
                        c.get("child_rc"), c.get("wall_s"),
                        nbp.get(c["ep"], ""), nbp.get("CPUExecutionProvider", ""),
                        par, "|".join(c.get("session_providers", [])),
                        c.get("device_luid", "")])
    with open(os.path.join(out_dir, "matrix.json"), "w",
              encoding="utf-8", newline="\n") as f:
        json.dump({"schema": "r2_1_1/gpu_matrix_driver/1",
                   "case_timeout_s": CASE_TIMEOUT_S, "cases": cases},
                  f, indent=1, default=str)
    n_pass = sum(1 for c in cases if str(c["classification"]).startswith("PASS"))
    print(f"matrix done: {n_pass}/{len(cases)} PASS_GPU_*")
    return 0


if __name__ == "__main__":
    main()
