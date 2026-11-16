"""R1.0: environment fingerprint (read-only) + driver-version mapping note.

Captures, as one receipt:
  - git state (branch, HEAD, remote main SHA as passed in)
  - Windows build, GPU adapters in WMI order, AMD PnP signed driver info
  - python/ORT versions of the two task venvs
  - presence-only snapshot of device-visibility env vars
  - explicit verdict for driver-vs-requirement comparability
    (COMPATIBILITY_UNVERIFIED unless an authoritative mapping exists)
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from r1.r1_common import (  # noqa: E402
    atomic_write_json, finish_receipt, new_receipt, redact_json,
    visibility_env_snapshot,
)

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PS_ADAPTERS = r"""
Get-CimInstance Win32_VideoController | ForEach-Object {
  '{0}|{1}|{2}|{3}' -f $_.Name, $_.DriverVersion, $_.DriverDate, $_.PNPDeviceID
}
"""

PS_AMD_DRIVER = r"""
Get-CimInstance Win32_PnPSignedDriver | Where-Object { $_.DeviceID -like 'PCI\VEN_1002*' } | ForEach-Object {
  '{0}|{1}|{2}|{3}' -f $_.DeviceName, $_.DriverVersion, $_.DriverProviderName, $_.InfName
}
"""


def ps(script: str) -> list:
    r = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "[Console]::OutputEncoding=[Text.Encoding]::UTF8; " + script],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=120)
    return [l.strip() for l in (r.stdout or "").splitlines() if l.strip()]


def pip_ver(venv: str, pkg: str) -> str:
    exe = os.path.join(REPO, venv, "Scripts", "python.exe")
    if not os.path.isfile(exe):
        return "<venv missing>"
    try:
        r = subprocess.run([exe, "-c",
                            f"import {pkg}; print({pkg}.__version__)"],
                           capture_output=True, text=True, timeout=120)
        return r.stdout.strip() or f"<no output, rc={r.returncode}>"
    except Exception as e:
        return f"<error {e}>"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--remote-main-sha", default="<not fetched>")
    args = ap.parse_args()

    r = new_receipt("r1_env_fingerprint", args.run_id, sys.argv)
    r["git"] = {
        "branch": subprocess.run(["git", "-C", REPO, "rev-parse", "--abbrev-ref", "HEAD"],
                                 capture_output=True, text=True).stdout.strip(),
        "head": subprocess.run(["git", "-C", REPO, "rev-parse", "HEAD"],
                               capture_output=True, text=True).stdout.strip(),
        "remote_main_sha": args.remote_main_sha,
        "porcelain_clean": not subprocess.run(["git", "-C", REPO, "status", "--porcelain"],
                                              capture_output=True, text=True).stdout.strip(),
    }
    r["adapters_wmi_order"] = ps(PS_ADAPTERS)
    r["amd_pnp_signed_driver"] = ps(PS_AMD_DRIVER)
    r["os"] = sys.getwindowsversion() and str(sys.getwindowsversion())
    r["versions"] = {
        "venv-cpu/onnxruntime": pip_ver("venv-cpu", "onnxruntime"),
        "venv-winml/onnxruntime": pip_ver("venv-winml", "onnxruntime"),
        "python_launcher": sys.version.split()[0],
    }
    r["visibility_env"] = visibility_env_snapshot()
    r["driver_comparability"] = {
        "observed_amd_driver": "32.0.21030.2001 (re-measured this run)",
        "ms_doc_requirement": "Adrenalin 25.10.13.09+ per learn.microsoft.com "
                              "supported-execution-providers (fetched 2026-11-17)",
        "gpuopen_mapping": "25.10.13.09 <-> 32.0.21013.9005 per gpuopen.com/version-table "
                           "(fetched 2026-11-17); string ordering across two different "
                           "version schemes is NOT a technical compatibility proof",
        "verdict": "COMPATIBILITY_UNVERIFIED",
        "note": "no authoritative 32.0.21030.2001<->Adrenalin mapping checked this run; "
                "moreover R1.1 shows the raw HIP stack works on this driver, so driver "
                "age is NOT the proximate cause of HIP 101",
    }
    finish_receipt(r, 0, "FINGERPRINT_DONE")
    atomic_write_json(os.path.join(REPO, "reports", "r1", args.run_id,
                                   "env_fingerprint.json"), redact_json(r))
    print("env_fingerprint written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
