#!/usr/bin/env python3
"""R2.1-0 baseline capture: repos/PR/CI/venv/GPU/toolchain/DISK, redacted.

CPU-only, read-only. Writes reports/r2_1/<run_id>/BASELINE.json.
"""
import ctypes
import ctypes.wintypes as wt
import datetime
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
FORK = r"C:\AIwork4me\fork-amdgpu-ep"


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def run(cmd, cwd=None, timeout=60):
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                           timeout=timeout, shell=(os.name == "nt"))
        return {"rc": r.returncode, "out": r.stdout.strip()[:4000],
                "err": r.stderr.strip()[:2000]}
    except Exception as e:  # noqa: BLE001
        return {"rc": None, "out": "", "err": f"EXC:{type(e).__name__}:{e}"}


def redact(s):
    # redact Windows user profile path forms and obvious tokens
    user = os.environ.get("USERNAME", "")
    if user:
        s = s.replace(user, "<USER>")
        s = re.sub(r"[A-Za-z]:\\+Users\\+\S*?<USER>", "<USERPROFILE>", s)
        s = re.sub(r"/[A-Za-z]/+Users/+\S*?<USER>", "<USERPROFILE>", s)
    s = re.sub(r"(ghp_|github_pat_)[A-Za-z0-9_]+", r"\1<REDACTED>", s)
    return s


def gpu_info():
    info = []
    try:
        r = run(["powershell", "-NoProfile", "-Command",
                 "Get-CimInstance Win32_VideoController | Select-Object "
                 "Name,DriverVersion,AdapterLUID,PNPDeviceID,Status "
                 "| ConvertTo-Json -Compress"])
        if r["rc"] == 0 and r["out"]:
            data = json.loads(r["out"])
            if isinstance(data, dict):
                data = [data]
            for d in data:
                info.append({k: d.get(k) for k in
                             ("Name", "DriverVersion", "AdapterLUID",
                              "PNPDeviceID", "Status")})
    except Exception as e:  # noqa: BLE001
        info.append({"error": str(e)})
    return info


def msix_ep_packages():
    """Windows ML AMD EP framework packages via Get-AppxPackage (user scope)."""
    r = run(["powershell", "-NoProfile", "-Command",
             "Get-AppxPackage *WinML* | Select-Object Name,Version,PackageFullName "
             "| ConvertTo-Json -Compress"], timeout=90)
    out = []
    if r["rc"] == 0 and r["out"]:
        try:
            data = json.loads(r["out"])
            if isinstance(data, dict):
                data = [data]
            out = [{"Name": d.get("Name"), "Version": d.get("Version"),
                    "PackageFullName": d.get("PackageFullName")} for d in data]
        except Exception:  # noqa: BLE001
            out = [{"raw": r["out"][:1000]}]
    return out


def tool(name):
    p = shutil.which(name)
    return p if p else None


def disk_free(path):
    try:
        free = shutil.disk_usage(path).free
        return {"free_bytes": free, "free_gib": round(free / (1 << 30), 1)}
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}


def main():
    now = datetime.datetime.now(datetime.timezone.utc)
    base = {
        "captured_at_utc": now.isoformat(timespec="seconds"),
        "host_os": platform.platform(),
        "machine": redact(platform.node()),
    }

    # git / PR state for both repos
    repos = {}
    for label, path, branch in (("primary", REPO, "mission/r2-device-identity-real-models"),
                                 ("fork", FORK, "fix/windows-dxgi-hip-device-identity")):
        d = {"path": path, "branch": branch}
        d["head_local"] = run(["git", "rev-parse", "HEAD"], cwd=path)["out"]
        d["head_remote"] = run(["git", "ls-remote", "origin", branch], cwd=path)["out"].split("\t")[0]
        d["status_porcelain"] = run(["git", "status", "--porcelain=v1"], cwd=path)["out"][:1000]
        d["describe"] = run(["git", "describe", "--always", "--dirty"], cwd=path)["out"]
        d["eol_config"] = {
            "core.autocrlf": run(["git", "config", "core.autocrlf"], cwd=path)["out"] or "(unset)",
            "core.eol": run(["git", "config", "core.eol"], cwd=path)["out"] or "(unset)",
        }
        repos[label] = d
    pr2 = run(["gh", "pr", "view", "2", "--json",
               "number,state,isDraft,headRefOid,baseRefOid,url"], cwd=REPO)
    pr1 = run(["gh", "pr", "view", "1", "--repo", "AIwork4me/onnxruntime-ep-amdgpu",
               "--json", "number,state,isDraft,headRefOid,baseRefOid,url"])
    base["pr_primary_2"] = json.loads(pr2["out"]) if pr2["rc"] == 0 else pr2
    base["pr_fork_1"] = json.loads(pr1["out"]) if pr1["rc"] == 0 else pr1
    base["repos"] = repos

    # venv / python / ORT
    venvs = {}
    for v in ("venv-winml", "venv-cpu"):
        py = os.path.join(REPO, v, "Scripts", "python.exe")
        e = {"python_exe": py}
        if os.path.exists(py):
            r = run([py, "-c",
                     "import sys,json;print(json.dumps({'ver':sys.version,"
                     "'exe':sys.executable}))"], timeout=60)
            try:
                e["python"] = json.loads(r["out"])
            except Exception:  # noqa: BLE001
                e["python_raw"] = r["out"][:300]
            r = run([py, "-c",
                     "import onnxruntime as ort,json,os,hashlib;"
                     "d=os.path.join(os.path.dirname(ort.__file__),'capi','onnxruntime_pybind11_state.pyd');"
                     "print(json.dumps({'ort':ort.__version__,"
                     "'pyd_sha256':(hashlib.sha256(open(d,'rb').read()).hexdigest() if os.path.exists(d) else None),"
                     "'providers':ort.get_available_providers()}))"], timeout=120)
            try:
                e["onnxruntime"] = json.loads(r["out"])
            except Exception:  # noqa: BLE001
                e["onnxruntime_raw"] = (r["out"] + r["err"])[:400]
        else:
            e["missing"] = True
        venvs[v] = e
    base["venvs"] = venvs

    # GPU / driver / MSIX
    base["gpu_win32_videocontroller"] = gpu_info()
    base["msix_winml_packages"] = msix_ep_packages()

    # driver-carrying amdhip64
    hip_sys = r"C:\Windows\System32\amdhip64_6.dll"
    base["amdhip64_system32"] = ({"path": hip_sys, "sha256": sha256_file(hip_sys)}
                                 if os.path.exists(hip_sys) else None)

    # toolchain inventory (expected absent: no MSVC/SDK/HIP SDK per authorization)
    tools = {}
    for t in ("cl", "clang", "clang++", "gcc", "g++", "cmake", "ninja", "make",
              "git", "gh", "uv", "py-spy", "where"):
        tools[t] = tool(t)
    # MSVC via vswhere if present
    vswhere = (r"C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe")
    if os.path.exists(vswhere):
        r = run([vswhere, "-latest", "-property", "installationPath"])
        tools["vswhere_latest"] = r["out"] or "(none)"
    else:
        tools["vswhere_latest"] = "NOT_INSTALLED"
    base["tools"] = tools

    # disk free on repo drive and artifacts
    base["disk"] = {"repo": disk_free(REPO), "artifacts": disk_free(os.path.join(REPO, "artifacts")),
                    "system": disk_free("C:\\")}

    # environment (redacted subset)
    env = {}
    for k in ("PROCESSOR_ARCHITECTURE", "NUMBER_OF_PROCESSORS",
              "OS", " ComSpec", "PATH", "PYTHONPATH", "PYTHONHOME",
              "ONNXRUNTIME_EP_PATH", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY"):
        kk = k.strip()
        if kk in os.environ:
            env[kk] = redact(os.environ[kk])
    base["env_subset"] = env
    base["cpu_count"] = os.cpu_count()

    out_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(REPO, "reports", "r2_1")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "BASELINE.json")
    txt = json.dumps(base, indent=2, ensure_ascii=False)
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(redact(txt))
    print(f"WROTE {out_path} ({len(txt)} bytes)")


if __name__ == "__main__":
    main()
