#!/usr/bin/env python3
"""R2.1-D driver: SSD partition case matrix, one child process per case.

Serial GPU usage. Cases:
  - ssd_mobilenet_v1_10 x {AMDGPU, MIGraphX} x ALL  (R2 reproduction)
  - ssd x AMDGPU x {DISABLE_ALL, BASIC, EXTENDED}   (optimization controls)
  - ssd x DML_CONTROL x ALL                          (same-build control)
  - 4 minimal fixtures x {AMDGPU, MIGraphX} x ALL    (barrier isolation)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PY = os.path.join(REPO, "venv-winml", "Scripts", "python.exe")
CHILD = os.path.join(REPO, "runner", "r2_1", "ssd_partition_child.py")
SSD = os.path.join(REPO, "artifacts", "models", "ssd_mobilenet_v1_10.onnx")
FIXDIR = os.path.join(REPO, "artifacts", "models", "ssd_partition_fixtures")

CASES = []
for ep in ("AMDGPUExecutionProvider", "MIGraphXExecutionProvider"):
    CASES.append((SSD, ep, "ALL"))
for opt in ("DISABLE_ALL", "BASIC", "EXTENDED"):
    CASES.append((SSD, "AMDGPUExecutionProvider", opt))
CASES.append((SSD, "DML_CONTROL", "ALL"))
for fx in ("static_conv3.onnx", "dynamic_conv3.onnx",
           "static_loop.onnx", "static_conv_loop.onnx"):
    for ep in ("AMDGPUExecutionProvider", "MIGraphXExecutionProvider"):
        CASES.append((os.path.join(FIXDIR, fx), ep, "ALL"))


def main():
    out_dir = sys.argv[1]
    os.makedirs(out_dir, exist_ok=True)
    results = []
    for model, ep, opt in CASES:
        tag = f"{os.path.splitext(os.path.basename(model))[0]}__{'DMLC' if ep=='DML_CONTROL' else ('AMD' if ep.startswith('AMDGPU') else 'MGX')}__{opt}"
        rc_path = os.path.join(out_dir, f"{tag}.json")
        opt_save = os.path.join(out_dir, f"{tag}.optimized.onnx")
        cmd = [PY, "-I", CHILD, "--model", model, "--ep", ep, "--opt", opt,
               "--out", rc_path, "--save-optimized", opt_save]
        print(f"RUN {tag} ...", flush=True)
        r = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, timeout=420)
        try:
            rec = json.load(open(rc_path, encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            rec = {"classification": "RECEIPT_MISSING", "rc": r.returncode,
                   "stdout_tail": r.stdout[-400:], "stderr_tail": r.stderr[-800:]}
        rec["child_rc"] = r.returncode
        rec["stdout_tail"] = r.stdout[-1500:]
        rec["stderr_tail"] = r.stderr[-1500:]
        results.append(rec)
        print(f"  -> {rec.get('classification')} {rec.get('nodes_by_provider')}", flush=True)
    with open(os.path.join(out_dir, "ssd_partition_matrix.json"), "w",
              encoding="utf-8", newline="\n") as f:
        json.dump({"cases": results}, f, indent=1, default=str)
    print("DONE", len(results), "cases", flush=True)


if __name__ == "__main__":
    main()
