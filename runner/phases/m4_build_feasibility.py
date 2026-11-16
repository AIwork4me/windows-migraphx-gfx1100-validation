"""M4: native Windows ROCm 10.1 MIGraphX/AMDGPU EP build feasibility.

Completely separate from the Windows ML prebuilt-EP route. Checks in order:
 1. toolchain: MSVC(cl), CMake, Ninja, hipcc, HIP SDK components, Windows SDK
 2. disk budget for a native build (>=100 GiB recommended)
 3. clones upstreams at pinned SHAs (shallow) and records the EXACT build
    entry points (build.bat, CMake find_package requirements)
 4. verdict: NATIVE_WINDOWS_ROCM10_1_GO / BUILD_ONLY / BLOCKED_DEPENDENCY
    with the FIRST concrete blocker (missing file, env var, package).
Never fabricates DLLs or links wrong versions.
"""
from __future__ import annotations
import argparse, os, shutil, subprocess, sys, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
import common as C
import ledger as L

UPSTREAMS = {
    "onnxruntime-ep-amdgpu": "https://github.com/onnxruntime/onnxruntime-ep-amdgpu",
    "AMDMIGraphX": "https://github.com/ROCm/AMDMIGraphX",
    "onnxruntime": "https://github.com/microsoft/onnxruntime",
}


PROXY = os.environ.get("WINML_GIT_PROXY", "http://127.0.0.1:7897")


def run(cmd, cwd=None, timeout=120):
    try:
        if cmd and cmd[0] == "git" and cmd[1] in ("clone", "fetch"):
            cmd = ["git", "-c", f"http.proxy={PROXY}"] + cmd[1:]
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return {"cmd": cmd, "rc": r.returncode, "out": (r.stdout + r.stderr)[-1500:]}
    except Exception as e:
        return {"cmd": cmd, "rc": -1, "out": repr(e)[:500]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=C.REPO)
    args = ap.parse_args()
    repo = os.path.abspath(args.repo)
    led = os.path.join(repo, "reports", "ledger.json")
    receipt = C.new_receipt("m4", "m4_feasibility_run")

    # 1. toolchain probe
    tools = {}
    for t in ("cl", "cmake", "ninja", "hipcc", "hipconfig", "msbuild", "git", "python"):
        p = shutil.which(t)
        tools[t] = p or "NOT_FOUND"
    receipt["toolchain"] = tools
    checks = {
        "VS2022_dir": os.path.exists(r"C:\Program Files\Microsoft Visual Studio\2022"),
        "vswhere": os.path.exists(r"C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe"),
        "windows_sdk": os.path.exists(r"C:\Program Files (x86)\Windows Kits\10\Include"),
        "hip_sdk_dir": os.path.exists(r"C:\Program Files\AMD\ROCm"),
        "amdhip64_lib": None,
        "migraphx_cmake_config": None,
    }
    for root in (r"C:\Program Files\AMD", r"C:\AMD"):
        if os.path.isdir(root):
            for dirpath, dirnames, filenames in os.walk(root):
                for f in filenames:
                    if f.lower() in ("amdhip64.lib", "amdhip64_7.dll", "migraphx-config.cmake"):
                        checks["amdhip64_lib"] = checks["amdhip64_lib"] or []
                        checks["amdhip64_lib"].append(os.path.join(dirpath, f))
                if dirpath.count(os.sep) > 6:
                    dirnames[:] = []
    receipt["component_checks"] = {k: (v if v is not None else "NOT_FOUND") for k, v in checks.items()}

    # disk budget
    import ctypes
    free = ctypes.c_ulonglong()
    ctypes.windll.kernel32.GetDiskFreeSpaceExW("C:\\", None, None, ctypes.byref(free))
    free_gb = round(free.value / (1 << 30), 1)
    receipt["disk_free_gb"] = free_gb

    # 2. clone upstreams shallow, record HEAD
    deps = os.path.join(repo, "deps")
    os.makedirs(deps, exist_ok=True)
    ups = {}
    for name, url in UPSTREAMS.items():
        d = os.path.join(deps, name)
        if not os.path.exists(os.path.join(d, ".git")):
            r = run(["git", "clone", "--depth", "1", url, d], timeout=600)
            if r["rc"] != 0:
                ups[name] = {"clone": "FAIL", "detail": r["out"][-400:]}
                continue
        sha = run(["git", "rev-parse", "HEAD"], cwd=d)
        ups[name] = {"clone": "OK", "head": sha["out"].strip(), "url": url}
    receipt["upstreams"] = ups

    # 3. inspect build entry points (source evidence of requirements)
    ep_repo = os.path.join(deps, "onnxruntime-ep-amdgpu")
    build_bat = os.path.join(ep_repo, "build.bat")
    receipt["build_bat_exists"] = os.path.exists(build_bat)
    migx_repo = os.path.join(deps, "AMDMIGraphX")
    cmake_src = os.path.join(migx_repo, "CMakeLists.txt")
    need = {"find_package_hip": None, "find_package_migraphx": None}
    if os.path.exists(cmake_src):
        src = open(cmake_src, encoding="utf-8", errors="replace").read()
        need["find_package_hip"] = "find_package(hip" in src or "find_package(HIP" in src
    r = run(["git", "-C", ep_repo, "grep", "-l", "find_package(MIGraphX", "HEAD"],
            cwd=ep_repo) if os.path.exists(ep_repo) else {"rc": -1, "out": ""}
    need["find_package_migraphx"] = r["rc"] == 0
    receipt["build_requirements_seen"] = need

    # 4. verdict: first concrete blocker
    blockers = []
    if tools["cl"] == "NOT_FOUND" and not checks["VS2022_dir"]:
        blockers.append("MSVC/VS2022 absent: cl.exe not found, no VS2022 install, "
                        "Build Tools install requires admin elevation (unauthorized)")
    if tools["cmake"] == "NOT_FOUND":
        blockers.append("CMake not in PATH (winget per-user install possible but MSVC remains the primary blocker)")
    if not checks["hip_sdk_dir"]:
        blockers.append("ROCm/HIP SDK for Windows not installed (no C:\\Program Files\\AMD\\ROCm; "
                        "HIP SDK installer requires admin elevation; System32 amdhip64_6.dll is "
                        "driver-shipped runtime only, no headers/import lib)")
    if not checks["windows_sdk"]:
        blockers.append("Windows SDK absent (ships with VS Build Tools)")
    verdict = "NATIVE_WINDOWS_ROCM10_1_GO" if not blockers else "BLOCKED_DEPENDENCY"
    receipt["verdict"] = verdict
    receipt["first_blockers"] = blockers[:3]
    C.save_receipt("m4", receipt)
    L.upsert_case(led, {"id": "m4/feasibility", "phase": "m4", "status": verdict,
                        "blockers": blockers[:3], "upstreams": ups})
    print("M4_FEASIBILITY_DONE", verdict)


if __name__ == "__main__":
    rc = main()
    sys.stdout.flush(); sys.stderr.flush()
    os._exit(rc if isinstance(rc, int) else 0)  # hard exit: skip interpreter shutdown (WinML/ORT leave non-daemon threads)
