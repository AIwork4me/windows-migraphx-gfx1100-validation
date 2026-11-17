"""R2.0 environment fingerprint (read-only), extending R1's with the
R2-relevant identity anchors: EP DLL + HIP DLL absolute paths and SHA256,
ORT builds in both venvs, git state of the R2 branch."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EP_DIR = os.path.join(
    r"C:\Program Files\WindowsApps",
    "Microsoft.WinML.AMD.GPU.EP.Framework.2_1.8.65.0_x64__8wekyb3d8bbwe",
    "ExecutionProvider")


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    def sh(cmd, network=False):
        env = dict(os.environ)
        if network and "http_proxy" not in {k.lower() for k in env}:
            env["http_proxy"] = env["https_proxy"] = "http://127.0.0.1:7897"
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=90,
                           cwd=REPO, env=env, encoding="utf-8",
                           errors="replace")
        return (r.stdout or "").strip()

    def shn(cmd):
        return sh(cmd, network=True)

    dlls = {}
    for name in os.listdir(EP_DIR) if os.path.isdir(EP_DIR) else []:
        if name.lower() in ("amdgpu-ep.dll", "amdhip64_7.dll", "migraphx.dll",
                            "miopen.dll"):
            p = os.path.join(EP_DIR, name)
            dlls[name] = {"path": p, "sha256": sha256_of(p),
                          "size": os.path.getsize(p)}
    sys_hip = r"C:\Windows\System32\amdhip64_6.dll"
    if os.path.isfile(sys_hip):
        dlls["amdhip64_6.dll (System32)"] = {"path": sys_hip,
                                             "sha256": sha256_of(sys_hip),
                                             "size": os.path.getsize(sys_hip)}

    fp = {
        "schema": "r2/env_fingerprint/1",
        "generated_utc": utc_now(),
        "os": sh(["powershell", "-NoProfile", "-Command",
                  "(Get-CimInstance Win32_OperatingSystem).Caption + ' ' + "
                  "(Get-CimInstance Win32_OperatingSystem).Version"]),
        "git": {
            "branch": sh(["git", "rev-parse", "--abbrev-ref", "HEAD"]),
            "head": sh(["git", "rev-parse", "HEAD"]),
            "remote_main": shn(["git", "ls-remote", "origin", "refs/heads/main"]).split()[0]
            if shn(["git", "ls-remote", "origin", "refs/heads/main"]) else None,
            "dirty_files": len(sh(["git", "status", "--porcelain"]).splitlines()),
        },
        "ort": {
            "venv_winml": sh([os.path.join(REPO, "venv-winml", "Scripts",
                                           "python.exe"), "-c",
                              "import onnxruntime as o; print(o.__version__)"]),
            "venv_cpu": sh([os.path.join(REPO, "venv-cpu", "Scripts",
                                         "python.exe"), "-c",
                            "import onnxruntime as o; print(o.__version__)"]),
        },
        "amd_ep_package": {
            "name": "Microsoft.WinML.AMD.GPU.EP.Framework.2 1.8.65.0 (MSIX)",
            "dir": EP_DIR,
            "dlls": dlls,
        },
        "driver_note": "AMD PnP driver 32.0.21030.2001 (unchanged since R1); "
                       "Adrenalin mapping COMPATIBILITY_UNVERIFIED",
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(fp, f, indent=1)
    print(f"[env] git {fp['git']['head'][:12]} ort_winml={fp['ort']['venv_winml']} "
          f"ep_dlls={len(dlls)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
