"""R1.1 supervisor: HIP runtime inventory + official probe matrix.

Inventory (read-only): every discoverable official amdhip64*.dll with path,
SHA256, PE machine, file version and Authenticode signature status.

Matrix (one fresh subprocess per cell, 60s timeout each):
  M1 ep_hip7_default      EP-bundled amdhip64_7.dll, current environment
  M2 sys_hip6_default     System32 driver-shipped amdhip64_6.dll, current env
  M3 ep_hip7_clearenv     EP-bundled amdhip64_7.dll, device-visibility env
                          vars removed for the child (none are set today; the
                          cell proves the absence, not a difference)
  M4 ep_hip7_hipinit      EP-bundled amdhip64_7.dll, hipInit(0) before queries

Never mixes HIP 6 and HIP 7 in one process. No system PATH/registry changes.
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from r1.r1_common import (  # noqa: E402
    atomic_write_json, finish_receipt, new_receipt, pe_machine, sha256_of,
    visibility_env_snapshot,
)

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EP_DIR = (r"C:\Program Files\WindowsApps"
          r"\Microsoft.WinML.AMD.GPU.EP.Framework.2_1.8.65.0_x64__8wekyb3d8bbwe"
          r"\ExecutionProvider")


def signature_of(path: str) -> dict:
    """Authenticode status via PowerShell (read-only)."""
    ps = ("$s = Get-AuthenticodeSignature -LiteralPath '%s'; "
          "$s.Status.ToString(); $s.SignerCertificate.Subject" % path.replace("'", "''"))
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                             capture_output=True, text=True, timeout=60)
        lines = [l.strip() for l in out.stdout.splitlines() if l.strip()]
        return {"status": lines[0] if lines else "UNKNOWN",
                "signer": lines[1] if len(lines) > 1 else ""}
    except Exception as e:
        return {"status": "QUERY_FAILED", "error": repr(e)}


def inventory(outdir: str) -> list:
    paths = [os.path.join(EP_DIR, "amdhip64_7.dll")]
    paths += sorted(glob.glob(r"C:\Windows\System32\amdhip64*.dll"))
    inv = []
    for p in paths:
        if not os.path.isfile(p):
            inv.append({"path": p, "present": False})
            continue
        inv.append({
            "path": p, "present": True,
            "sha256": sha256_of(p), "size": os.path.getsize(p),
            "pe_machine": pe_machine(p),
            "signature": signature_of(p),
        })
    atomic_write_json(os.path.join(outdir, "hip_runtime_inventory.json"), inv)
    return inv


def matrix(outdir: str, run_id: str, timeout: int) -> list:
    child = os.path.join(REPO, "runner", "r1", "hip_probe_child.py")
    cells = [
        ("M1_ep_hip7_default", os.path.join(EP_DIR, "amdhip64_7.dll"), False, {}),
        ("M2_sys_hip6_default", r"C:\Windows\System32\amdhip64_6.dll", False, {}),
        ("M3_ep_hip7_clearenv", os.path.join(EP_DIR, "amdhip64_7.dll"), False,
         {k: None for k in ("HIP_VISIBLE_DEVICES", "ROCR_VISIBLE_DEVICES",
                            "CUDA_VISIBLE_DEVICES", "GPU_DEVICE_ORDINAL",
                            "HSA_OVERRIDE_GFX_VERSION", "HIP_HIDDEN_DEVICES")}),
        ("M4_ep_hip7_hipinit", os.path.join(EP_DIR, "amdhip64_7.dll"), True, {}),
    ]
    rows = []
    for tag, dll, hipinit, envpatch in cells:
        out = os.path.join(outdir, f"hip_probe_{tag}.json")
        env = dict(os.environ)
        env["R1_SOURCE_HEAD"] = env.get("R1_SOURCE_HEAD", "<unset>")
        for k, v in envpatch.items():
            if v is None:
                env.pop(k, None)
        cmd = [sys.executable, child, "--dll", dll, "--tag", tag,
               "--out", out, "--run-id", run_id]
        if hipinit:
            cmd.append("--hipinit")
        try:
            pr = subprocess.run(cmd, capture_output=True, text=True,
                                timeout=timeout, cwd=REPO, env=env)
            rc, so, se = pr.returncode, pr.stdout, pr.stderr
        except subprocess.TimeoutExpired as e:
            rc = -9
            so = (e.stdout or b"").decode("utf-8", "replace")
            se = f"TIMEOUT after {timeout}s"
        with open(os.path.join(outdir, f"hip_probe_{tag}.stdout.log"), "w",
                  encoding="utf-8") as f:
            f.write(so or "")
        with open(os.path.join(outdir, f"hip_probe_{tag}.stderr.log"), "w",
                  encoding="utf-8") as f:
            f.write(se or "")
        verdict = "RECEIPT_MISSING"
        if os.path.isfile(out):
            try:
                verdict = json.load(open(out, encoding="utf-8")).get("outcome", "NO_OUTCOME")
            except Exception:
                verdict = "RECEIPT_UNREADABLE"
        rows.append({"cell": tag, "dll": dll, "exit_code": rc,
                     "verdict": verdict,
                     "receipt": os.path.relpath(out, REPO).replace("\\", "/")})
        print(f"{tag}: exit={rc} verdict={verdict}", flush=True)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=REPO)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--timeout", type=int, default=60)
    args = ap.parse_args()

    outdir = os.path.join(args.repo, "reports", "r1", args.run_id)
    os.makedirs(outdir, exist_ok=True)
    r = new_receipt("r1_hip_probe_supervisor", args.run_id, sys.argv,
                    {"visibility_env_parent": visibility_env_snapshot(),
                     "ep_dir": EP_DIR})

    r["inventory"] = inventory(outdir)
    r["matrix"] = matrix(outdir, args.run_id, args.timeout)

    with open(os.path.join(outdir, "hip_probe_matrix.csv"), "w", newline="",
              encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["cell", "dll", "exit_code", "verdict", "receipt"])
        w.writeheader()
        w.writerows(r["matrix"])

    finish_receipt(r, 0, "SUPERVISOR_DONE")
    atomic_write_json(os.path.join(outdir, "hip_probe_supervisor.json"), r)
    print("done ->", outdir, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
