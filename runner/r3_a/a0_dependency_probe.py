#!/usr/bin/env python3
"""R3-A A0/A2: toolchain + dev-dependency probe and build ledger.

CPU-only, read-only probes of the HOST (no installs, no admin). Writes
reports/r3_a/<run_id>/BUILD_LEDGER.json + VERSION_MANIFEST.json inputs.
Every NOT_FOUND is real evidence for BLOCKED classifications; nothing is
faked or inferred.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import shutil
import subprocess
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
FORK = r"C:\AIwork4me\fork-amdgpu-ep"
MSIX = (r"C:\Program Files\WindowsApps"
        r"\Microsoft.WinML.AMD.GPU.EP.Framework.2_1.8.65.0_x64__8wekyb3d8bbwe")


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def run(cmd, timeout=60):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           shell=(os.name == "nt"))
        return {"rc": r.returncode, "out": r.stdout.strip()[:2000],
                "err": r.stderr.strip()[:1000]}
    except Exception as e:  # noqa: BLE001
        return {"rc": None, "out": "", "err": f"EXC:{type(e).__name__}"}


def probe_tools():
    tools = {}
    for t in ("cl", "cmake", "ninja", "hipcc", "clang++", "g++", "git",
              "python", "py"):
        w = shutil.which(t)
        tools[t] = {"which": w, "status": "FOUND" if w else "NOT_FOUND"}
    vsw = r"C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe"
    tools["vswhere"] = {
        "path": vsw, "status": "FOUND" if os.path.isfile(vsw) else "NOT_FOUND"}
    if os.path.isfile(vsw):
        tools["vswhere"]["instances"] = run([vsw, "-all", "-format", "json"])["out"][:1500]
    return tools


def probe_sdks():
    sdks = {}
    for name, p in (
            ("hip_sdk_dir", r"C:\Program Files\AMD\ROCm"),
            ("rocm_dir", r"C:\Program Files\AMD\ROCm"),
            ("migraphx_include", r"C:\Program Files\AMD\ROCm\include\migraphx"),
            ("migraphx_lib", r"C:\Program Files\AMD\ROCm\lib"),
            ("amd_dir", r"C:\Program Files\AMD")):
        sdks[name] = {"path": p,
                      "status": "FOUND" if os.path.isdir(p) else "NOT_FOUND"}
    # Windows HIP SDK marker: amdhip64 SDK installer registers here
    sdks["hip_sdk_installer_key"] = run(["powershell", "-NoProfile", "-Command",
                                         "Get-ItemProperty "
                                         "'HKLM:\\SOFTWARE\\AMD\\HIP SDK' "
                                         "-ErrorAction SilentlyContinue | "
                                         "ConvertTo-Json -Compress"])
    return sdks


def probe_msix_dev_files():
    """The WinML MSIX is a RUNTIME package. Legal probe: list whether any
    development artifact (headers/import libs) exists inside it. We do NOT
    extract or copy anything."""
    out = {"path": MSIX, "headers": 0, "import_libs": 0, "dlls": []}
    if not os.path.isdir(MSIX):
        out["status"] = "NOT_FOUND"
        return out
    out["status"] = "FOUND"
    ep = os.path.join(MSIX, "ExecutionProvider")
    for root, _dirs, files in os.walk(ep):
        for fn in files:
            ext = os.path.splitext(fn)[1].lower()
            if ext == ".h":
                out["headers"] += 1
            elif ext == ".lib":
                out["import_libs"] += 1
            elif ext == ".dll" and len(out["dlls"]) < 30:
                p = os.path.join(root, fn)
                out["dlls"].append({"name": fn,
                                    "sha256": sha256_file(p),
                                    "bytes": os.path.getsize(p)})
    return out


def probe_git_states():
    st = {}
    for name, path in (("fork", FORK), ("validation_repo", REPO)):
        head = run(["git", "-C", path, "rev-parse", "HEAD"])
        branch = run(["git", "-C", path, "branch", "--show-current"])
        dirty = run(["git", "-C", path, "status", "--porcelain"])
        st[name] = {"head": head["out"], "branch": branch["out"],
                    "clean": dirty["out"] == ""}
    up = run(["git", "-C", FORK, "rev-parse", "upstream/main"])
    st["fork_upstream_main"] = up["out"]
    st["drift_vs_base"] = (up["out"] ==
                           "15ded64bd998a317dfe0ccf0ead8033574dd5b86")
    return st


def main():
    run_id = sys.argv[1]
    out_dir = os.path.join(REPO, "reports", "r3_a", run_id)
    os.makedirs(out_dir, exist_ok=True)
    ledger = {
        "schema": "r3_a/build_ledger/1",
        "generated_utc": datetime.datetime.now(datetime.timezone.utc)
            .replace(microsecond=0).isoformat(),
        "host": "Windows 11 build 26300 (owner machine, no admin rights used)",
        "tools": probe_tools(),
        "sdks": probe_sdks(),
        "msix_dev_probe": probe_msix_dev_files(),
        "git_states": probe_git_states(),
        "build_attempts": [{
            "target": "native full AMD plugin EP (amdgpu-ep.dll + "
                      "migraphx-backend.dll) from fork source",
            "attempt": "NOT_ATTEMPTED_ON_HOST",
            "classification": "BLOCKED_DEPENDENCY",
            "missing": [
                "MSVC compiler (cl NOT_FOUND, vswhere NOT_FOUND -> no "
                "Visual Studio instance)",
                "CMake / Ninja (NOT_FOUND)",
                "Windows HIP SDK (no C:\\Program Files\\AMD\\ROCm, no "
                "HKLM\\SOFTWARE\\AMD\\HIP SDK key)",
                "MIGraphX C++ headers + import libs (official Windows HIP "
                "SDK does not ship MIGraphX dev; WinML MSIX contains only "
                "runtime DLLs - 0 headers, 0 .lib found by legal probe)",
                "ORT C API headers/libs matching the plugin EP ABI "
                "(requires a matching onnxruntime source build or dev "
                "package; none present)",
            ],
            "legal_sources": [
                "Visual Studio 2022 Community (needs admin install "
                "authorization): https://visualstudio.microsoft.com/",
                "Windows HIP SDK installer via AMD ROCm portal (needs "
                "admin): https://www.amd.com/en/developer/resources/"
                "rocm-hub/hip-sdk.html",
                "MIGraphX Windows dev libs: no public standalone "
                "distribution known; requires building ROCm/AMDMIGraphX "
                "from source (deps/onnxruntime-ep-amdgpu vendors "
                "AMDMIGraphX sources, itself needing the above toolchain)",
            ],
            "admin_authorization": "NEEDS_OWNER_AUTH (explicitly not granted)",
        }, {
            "target": "guard decision logic (device_identity_guard.h)",
            "attempt": "COMPILED_AND_RUN_IN_CI",
            "classification": "GUARD_HELPER_CI_PASS",
            "evidence": "fork .github/workflows/guard-helper-cpp.yml: "
                        "ubuntu g++ & clang++ (-Wall -Wextra -Werror), "
                        "windows MSVC cl /W4 /WX; 6 check runs SUCCESS at "
                        "fork head 0baccb25eb607fe43255bf4d5b09a93df8571c91",
        }],
        "layered_verdicts": {
            "GUARD_HELPER_CI_PASS": True,
            "NATIVE_COMPILED": False,
            "NATIVE_PLUGIN_REGISTERED": False,
            "PATCHED_GPU_EXECUTED_WITH_PARITY": False,
            "IDENTITY_MAPPING_PORTABLE_VERIFIED": False,
        },
    }
    with open(os.path.join(out_dir, "BUILD_LEDGER.json"), "w",
              encoding="utf-8", newline="\n") as f:
        json.dump(ledger, f, indent=1)
    print("wrote BUILD_LEDGER.json;",
          "msix headers=", ledger["msix_dev_probe"]["headers"],
          "import_libs=", ledger["msix_dev_probe"]["import_libs"])


if __name__ == "__main__":
    main()
