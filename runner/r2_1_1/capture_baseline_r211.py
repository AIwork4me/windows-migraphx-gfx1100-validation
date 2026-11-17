#!/usr/bin/env python3
"""R2.1.1-P0 baseline capture: live PR/CI state, env, model/DLL SHA256.

CPU-only, read-only. Writes reports/r2_1_1/<run_id>/BASELINE.json.
Reuses R2.1 capture conventions (redaction, field names) for comparability.
"""
import datetime
import hashlib
import json
import os
import platform
import re
import subprocess
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
RUN_ID = sys.argv[1] if len(sys.argv) > 1 else None
if not RUN_ID:
    print("usage: capture_baseline_r211.py <run_id>")
    sys.exit(2)
OUT = os.path.join(REPO, "reports", "r2_1_1", RUN_ID, "BASELINE.json")

MODELS = [
    "yolo26n.onnx", "scrfd_det_10g.onnx", "ssd_mobilenet_v1_10.onnx",
    "emotion-ferplus-8.onnx", "mnist-8.onnx", "mobilenetv2-12.onnx",
    "squeezenet1.0-12.onnx",
]


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def run(cmd, cwd=None, timeout=90):
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                           timeout=timeout, shell=(os.name == "nt"))
        return {"rc": r.returncode, "out": r.stdout.strip()[:6000],
                "err": r.stderr.strip()[:2000]}
    except Exception as e:  # noqa: BLE001
        return {"rc": None, "out": "", "err": f"EXC:{type(e).__name__}:{e}"}


def redact(s):
    user = os.environ.get("USERNAME", "")
    if user:
        s = s.replace(user, "<USER>")
        s = re.sub(r"[A-Za-z]:\\+Users\\+\S*?<USER>", "<USERPROFILE>", s)
        s = re.sub(r"/[A-Za-z]/+Users/+\S*?<USER>", "<USERPROFILE>", s)
    s = re.sub(r"(ghp_|github_pat_)[A-Za-z0-9_]+", r"\1<REDACTED>", s)
    return s


def gpu_info():
    r = run(["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_VideoController | Select-Object "
             "Name,DriverVersion,AdapterLUID,PNPDeviceID,Status "
             "| ConvertTo-Json -Compress"])
    out = []
    if r["rc"] == 0 and r["out"]:
        try:
            data = json.loads(r["out"])
            if isinstance(data, dict):
                data = [data]
            for d in data:
                out.append({k: d.get(k) for k in (
                    "Name", "DriverVersion", "AdapterLUID", "PNPDeviceID",
                    "Status")})
        except json.JSONDecodeError:
            out.append({"raw": r["out"][:500]})
    else:
        out.append({"error": r["err"] or r["out"][:300]})
    return out


def main():
    base = {
        "captured_at_utc": datetime.datetime.now(datetime.timezone.utc)
            .replace(microsecond=0).isoformat(),
        "mission": "R2.1.1",
        "host_os": platform.platform(),
        "machine": platform.node(),
        "cpu_count": os.cpu_count(),
        "note_interpreter": (
            "WindowsApps python.exe store stub currently exits 49 with no "
            "output on this host; all repo tooling uses venv interpreters "
            "recorded below"),
    }
    gh2 = run(["gh", "pr", "view", "2", "--repo",
               "AIwork4me/windows-migraphx-gfx1100-validation", "--json",
               "state,isDraft,mergeable,mergeStateStatus,headRefOid,baseRefOid,url"])
    base["pr2_live"] = gh2
    ghf = run(["gh", "pr", "view", "1", "--repo",
               "AIwork4me/onnxruntime-ep-amdgpu", "--json",
               "state,isDraft,headRefOid,baseRefOid,url"])
    base["pr_fork1_live"] = ghf
    git = run(["git", "-C", REPO, "log", "-1", "--format=%H %ci"])
    base["git_head_local"] = redact(git["out"])
    lsrem = run(["git", "-C", REPO, "ls-remote", "origin",
                 "refs/heads/main",
                 "refs/heads/mission/r2-device-identity-real-models"])
    base["git_remote_heads"] = redact(lsrem["out"])
    st = run(["git", "-C", REPO, "status", "--porcelain"])
    base["git_status_clean"] = (st["out"] == "")

    base["gpu_win32_videocontroller"] = gpu_info()

    msix = run(["powershell", "-NoProfile", "-Command",
                "Get-AppxPackage *WinML* | Select-Object Name,Version "
                "| ConvertTo-Json -Compress"], timeout=120)
    base["msix_winml_packages"] = msix.get("out", "") or msix.get("err", "")

    hip = os.path.expandvars(
        r"%SystemRoot%\System32\amdhip64.dll")
    base["amdhip64_system32"] = (
        {"path": hip, "sha256": sha256_file(hip),
         "bytes": os.path.getsize(hip)} if os.path.isfile(hip)
        else "NOT_FOUND")

    models = {}
    for m in MODELS:
        p = os.path.join(REPO, "artifacts", "models", m)
        models[m] = ({"sha256": sha256_file(p), "bytes": os.path.getsize(p)}
                     if os.path.isfile(p) else "NOT_FOUND")
    base["model_sha256"] = models

    for name, sub in (("venv-cpu", "venv-cpu"), ("venv-winml", "venv-winml")):
        py = os.path.join(REPO, sub, "Scripts", "python.exe")
        if os.path.isfile(py):
            v = run([py, "-c",
                     "import sys,onnxruntime as o;print(sys.version.split()[0],"
                     "o.__version__,o.__file__)"])
            base[name] = redact(v["out"])
        else:
            base[name] = "NOT_FOUND"

    base["r21_final_verdict_ref"] = {
        "evidence_root": "reports/r2_1/r21_20261117T040739Z/",
        "soak": "RCA_NOT_REPRODUCED",
        "ssd": "LOOP_BARRIER_ISOLATED (to be re-examined by R2.1.1 causal "
               "fixtures)",
        "guard": "GUARD_HELPER_COMPILED_TESTED (3 compilers), native build "
                 "BLOCKED_TOOLCHAIN",
    }

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        json.dump(base, f, indent=1, ensure_ascii=False)
    print("wrote", OUT)


if __name__ == "__main__":
    main()
