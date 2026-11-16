"""R1.1 parent: run the independent HIP probe once per candidate DLL.

Each candidate runs in its own python subprocess (HIP runtimes hold global
state; never mix amdhip64 major versions in one process). The parent only
spawns, captures stdout/stderr/exit-code and merges receipts -- it never
imports onnxruntime, so every result here is by construction independent of
ORT / WinML / the AMD EP plugin.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EP_DIR = (r"C:\Program Files\WindowsApps"
          r"\Microsoft.WinML.AMD.GPU.EP.Framework.2_1.8.65.0_x64__8wekyb3d8bbwe"
          r"\ExecutionProvider")

PY = sys.executable


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def candidates() -> list:
    cands = [
        ("ep_amdhip64_7", os.path.join(EP_DIR, "amdhip64_7.dll"),
         "HIP runtime bundled with WinML AMD EP package 1.8.65.0 -- the DLL "
         "the EP actually loaded when round-1 hit HIP 101"),
    ]
    for p in sorted(glob.glob(r"C:\Windows\System32\amdhip64*.dll")):
        tag = "sys_" + os.path.basename(p).replace(".dll", "").replace("amdhip64_", "amdhip")
        note = "driver-shipped HIP runtime in System32 (installed by Adrenalin)"
        cands.append((tag, p, note))
    return cands


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=REPO)
    ap.add_argument("--timeout", type=int, default=120)
    args = ap.parse_args()

    outdir = os.path.join(args.repo, "reports", "r1")
    os.makedirs(outdir, exist_ok=True)
    child = os.path.join(args.repo, "runner", "phases", "r1_hip_probe_child.py")

    manifest = {
        "case_id": "r1_hip_probe",
        "phase": "r1",
        "description": "Independent HIP Runtime probe (pure ctypes; no ORT/WinML imports)",
        "started_utc": utc_now(),
        "python": sys.version,
        "candidates": [],
    }

    for tag, dll, note in candidates():
        out = os.path.join(outdir, f"hip_probe_{tag}.json")
        print(f"=== probing {tag}: {dll}", flush=True)
        try:
            r = subprocess.run(
                [PY, child, "--dll", dll, "--tag", tag, "--out", out],
                capture_output=True, text=True, timeout=args.timeout,
                cwd=args.repo)
            stdout, stderr, rc = r.stdout, r.stderr, r.returncode
        except subprocess.TimeoutExpired as e:
            stdout = (e.stdout or b"").decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
            stderr = f"TIMEOUT after {args.timeout}s"
            rc = -9
        entry = {"tag": tag, "dll": dll, "note": note, "exit_code": rc,
                 "stdout_tail": stdout[-2000:], "stderr_tail": stderr[-1000:],
                 "receipt": os.path.relpath(out, args.repo) if os.path.isfile(out) else None}
        manifest["candidates"].append(entry)
        log = os.path.join(outdir, f"hip_probe_{tag}.console.log")
        with open(log, "w", encoding="utf-8") as f:
            f.write(f"$ python {child} --dll {dll} --tag {tag}\nexit={rc}\n--- stdout ---\n{stdout}\n--- stderr ---\n{stderr}\n")
        print(f"    exit={rc}")

    manifest["finished_utc"] = utc_now()
    with open(os.path.join(outdir, "hip_probe_manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=1)
    print("manifest -> reports/r1/hip_probe_manifest.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
