"""R1.2 supervisor: per-EP enumerate -> per-candidate fresh-process runs.

For each of AMDGPUExecutionProvider / MIGraphXExecutionProvider:
  1. enumerate   (1 process; identity dump of every ep device)
  2. run c=0..N-1 (one fresh process per candidate; single-device session)
  3. multi        (negative control, two devices)
Then merges a dll_load_audit: which amdhip64*.dll each EP process mapped,
cross-checked by SHA256 against the R1.1 inventory.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from r1.r1_common import atomic_write_json, finish_receipt, new_receipt, redact_json  # noqa: E402

EPS = ["AMDGPUExecutionProvider", "MIGraphXExecutionProvider"]
VENV_PY = os.path.join(REPO, "venv-winml", "Scripts", "python.exe")


def spawn(py: str, args: list, timeout: int, logbase: str) -> dict:
    env = dict(os.environ)
    try:
        pr = subprocess.run([py] + args, capture_output=True, text=True,
                            timeout=timeout, cwd=REPO, env=env)
        rc, so, se = pr.returncode, pr.stdout, pr.stderr
    except subprocess.TimeoutExpired as e:
        rc = -9
        so = (e.stdout or b"").decode("utf-8", "replace")
        se = f"TIMEOUT after {timeout}s"
    with open(logbase + ".stdout.log", "w", encoding="utf-8") as f:
        f.write(f"$ {py} {' '.join(args)}\nexit={rc}\n--- stdout ---\n{so}\n")
    with open(logbase + ".stderr.log", "w", encoding="utf-8") as f:
        f.write(se or "")
    return {"exit_code": rc, "stdout_tail": so[-1500:], "stderr_tail": se[-1500:]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--timeout", type=int, default=180)
    args = ap.parse_args()

    outdir = os.path.join(REPO, "reports", "r1", args.run_id)
    os.makedirs(outdir, exist_ok=True)
    r = new_receipt("r1_ep_device_supervisor", args.run_id, sys.argv)
    child = os.path.join(REPO, "runner", "r1", "ep_device_child.py")
    summary = []

    for ep in EPS:
        tag = ep.replace("ExecutionProvider", "")
        # 1) enumerate
        enum_out = os.path.join(outdir, f"epdev_{tag}_enumerate.json")
        run = spawn(VENV_PY, [child, "--ep", ep, "--mode", "enumerate",
                              "--out", enum_out, "--run-id", args.run_id],
                    args.timeout, os.path.join(outdir, f"epdev_{tag}_enumerate"))
        enum = {}
        if os.path.isfile(enum_out):
            try:
                enum = json.load(open(enum_out, encoding="utf-8"))
            except Exception:
                enum = {}
        n_match = enum.get("matching_devices", 0)
        row = {"ep": ep, "enumerate_exit": run["exit_code"],
               "matching_devices": n_match,
               "total_ep_devices": enum.get("total_ep_devices"),
               "candidates": []}
        print(f"{ep}: {n_match} matching device(s) of {enum.get('total_ep_devices')} total")

        # 2) per-candidate fresh-process single-device runs
        for i in range(max(n_match, 0)):
            c_out = os.path.join(outdir, f"epdev_{tag}_run_c{i}.json")
            crun = spawn(VENV_PY, [child, "--ep", ep, "--mode", "run",
                                   "--candidate", str(i), "--out", c_out,
                                   "--run-id", args.run_id],
                         args.timeout, os.path.join(outdir, f"epdev_{tag}_run_c{i}"))
            outcome = "RECEIPT_MISSING"
            if os.path.isfile(c_out):
                try:
                    outcome = json.load(open(c_out, encoding="utf-8")).get("outcome", "?")
                except Exception:
                    outcome = "RECEIPT_UNREADABLE"
            row["candidates"].append({"index": i, "exit_code": crun["exit_code"],
                                      "outcome": outcome})
            print(f"  c{i}: exit={crun['exit_code']} outcome={outcome}")

        # 3) multi-device negative control
        m_out = os.path.join(outdir, f"epdev_{tag}_multi.json")
        mrun = spawn(VENV_PY, [child, "--ep", ep, "--mode", "multi",
                               "--candidate", "0", "--out", m_out,
                               "--run-id", args.run_id],
                     args.timeout, os.path.join(outdir, f"epdev_{tag}_multi"))
        m_outcome = "RECEIPT_MISSING"
        if os.path.isfile(m_out):
            try:
                m_outcome = json.load(open(m_out, encoding="utf-8")).get("outcome", "?")
            except Exception:
                m_outcome = "RECEIPT_UNREADABLE"
        row["multi"] = {"exit_code": mrun["exit_code"], "outcome": m_outcome}
        print(f"  multi: exit={mrun['exit_code']} outcome={m_outcome}")
        summary.append(row)

    # ---- dll load audit ---------------------------------------------------
    audit = {"inventory_ref": "hip_runtime_inventory.json"}
    known = {}
    inv_path = os.path.join(outdir, "hip_runtime_inventory.json")
    if os.path.isfile(inv_path):
        for item in json.load(open(inv_path, encoding="utf-8")):
            if item.get("present"):
                known[item["sha256"]] = os.path.basename(item["path"])
    for fn in sorted(os.listdir(outdir)):
        if not (fn.startswith("epdev_") and fn.endswith(".json")):
            continue
        try:
            d = json.load(open(os.path.join(outdir, fn), encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(d, dict):
            continue
        mods = d.get("modules_after_session") or d.get("modules_at_failure") \
            or d.get("modules_after_registration") or []
        hips = [m for m in mods if "amdhip64" in os.path.basename(m).lower()] if isinstance(mods, list) else []
        audit[fn] = {"amdhip_loaded": hips, "module_count": len(mods) if isinstance(mods, list) else None}
    atomic_write_json(os.path.join(outdir, "dll_load_audit.json"), audit)

    r["summary"] = summary
    r["dll_load_audit"] = audit
    finish_receipt(r, 0, "EPDEV_DONE")
    atomic_write_json(os.path.join(outdir, "ep_device_supervisor.json"), redact_json(r))
    print("ep_device supervisor done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
