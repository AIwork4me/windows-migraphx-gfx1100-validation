"""R1.1 child: independent HIP Runtime probe for exactly one amdhip64*.dll.

Pure ctypes. Imports neither onnxruntime nor WinML: every result here is by
construction independent of ORT / the AMD EP plugin.

ABI policy (mission R1.1.4): no large HIP C struct (hipDeviceProp_t /
hipGetDeviceProperties) is passed through ctypes in this official probe --
we have no header that provably matches these DLL builds, so that call is
recorded as SKIPPED_ABI_UNVERIFIED and replaced with struct-free calls:
hipDeviceGetName / hipDeviceTotalMem / hipGetErrorString.

Run one DLL per process: HIP runtimes keep global init state.
"""

from __future__ import annotations

import argparse
import ctypes
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from r1.r1_common import (  # noqa: E402
    file_version, finish_receipt, new_receipt, pe_machine,
    sha256_of, atomic_write_json, visibility_env_snapshot, module_snapshot,
)

HIP_SYMBOLS = [
    "hipGetDeviceCount", "hipSetDevice", "hipGetDevice",
    "hipRuntimeGetVersion", "hipDriverGetVersion",
    "hipDeviceGetName", "hipGetErrorString", "hipGetErrorName",
    "hipGetVersionString", "hipInit", "hipGetDeviceProperties",
    "hipDeviceTotalMem",
]



def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dll", required=True)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--run-id", default="r1")
    ap.add_argument("--hipinit", action="store_true",
                    help="call hipInit(0) before device queries")
    args = ap.parse_args()

    r = new_receipt(f"r1_hip_probe_{args.tag}", args.run_id, sys.argv,
                    {"requested_dll": args.dll, "tag": args.tag,
                     "hipinit_first": args.hipinit,
                     "visibility_env": visibility_env_snapshot()})
    steps = []

    def step(label, data):
        steps.append({"step": label, **data})
        print(f"[{args.tag}] {label}: {data}", flush=True)

    # ---- load ------------------------------------------------------------
    if not os.path.isfile(args.dll):
        finish_receipt(r, 2, "DLL_NOT_FOUND")
        r["steps"] = steps
        atomic_write_json(args.out, r)
        return 2
    r["dll_identity"] = {
        "path": args.dll, "sha256": sha256_of(args.dll),
        "size": os.path.getsize(args.dll), "pe_machine": pe_machine(args.dll),
        "version": file_version(args.dll),
    }
    step("dll_identity", {k: v for k, v in r["dll_identity"].items() if k != "path"})

    os.add_dll_directory(os.path.dirname(args.dll))
    try:
        lib = ctypes.CDLL(args.dll)
        r["load"] = {"ok": True}
    except OSError as e:
        r["load"] = {"ok": False, "error": str(e),
                     "winerror": getattr(e, "winerror", None),
                     "get_last_error": ctypes.get_last_error()}
        step("load", r["load"])
        finish_receipt(r, 3, "HIP_LOAD_FAILED")
        r["steps"] = steps
        atomic_write_json(args.out, r)
        return 3
    step("load", {"ok": True})

    # prove which file Windows actually mapped + full HIP-related module set
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    buf = ctypes.create_unicode_buffer(1024)
    n = k32.GetModuleFileNameW(ctypes.c_void_p(lib._handle), buf, 1024)
    r["resolved_module_path"] = buf.value if n else None
    r["hip_modules_in_process"] = module_snapshot(lib._handle)
    step("resolved_module_path", {"path": r["resolved_module_path"]})
    step("hip_modules_in_process", {"count": len(r["hip_modules_in_process"])})

    # ---- exports ----------------------------------------------------------
    exports = {}
    for sym in HIP_SYMBOLS:
        try:
            exports[sym] = hasattr(lib, sym)
        except Exception:
            exports[sym] = False
    r["exports"] = exports
    step("exports", exports)

    def fn(name):
        f = getattr(lib, name)
        f.restype = ctypes.c_int
        return f

    def rc_info(code: int) -> dict:
        d = {"rc": code}
        try:
            cb = ctypes.c_char_p()
            f = getattr(lib, "hipGetErrorString", None)
            if f:
                f.restype = ctypes.c_char_p
                s = f(code)
                if s:
                    d["name"] = s.decode("utf-8", "replace")
        except Exception:
            pass
        return d

    # ---- optional hipInit(0) ----------------------------------------------
    if args.hipinit and exports.get("hipInit"):
        rc = fn("hipInit")(0)
        r["hipInit"] = rc_info(rc)
        step("hipInit", r["hipInit"])

    # ---- versions ----------------------------------------------------------
    for api in ("hipRuntimeGetVersion", "hipDriverGetVersion"):
        if exports.get(api):
            v = ctypes.c_int(-1)
            rc = fn(api)(ctypes.byref(v))
            r[api] = {**rc_info(rc), "value": v.value}
            step(api, r[api])

    # ---- device count ------------------------------------------------------
    cnt = ctypes.c_int(-1)
    rc = fn("hipGetDeviceCount")(ctypes.byref(cnt))
    r["hipGetDeviceCount"] = {**rc_info(rc), "count": cnt.value}
    step("hipGetDeviceCount", r["hipGetDeviceCount"])

    count = cnt.value if rc == 0 and cnt.value >= 0 else -1
    r["set_device_matrix"] = []
    test_ordinals = list(range(0, max(count, 0))) + [count, -1, 999]
    seen = set()
    for o in test_ordinals:
        if o in seen:
            continue
        seen.add(o)
        rc1 = fn("hipSetDevice")(ctypes.c_int(o))
        cur = ctypes.c_int(-9)
        rc2 = fn("hipGetDevice")(ctypes.byref(cur))
        row = {"ordinal": o, "expect": "valid" if 0 <= o < count else "negative_control",
               "hipSetDevice": rc_info(rc1),
               "hipGetDevice": {**rc_info(rc2), "value": cur.value}}
        # struct-free device naming for valid ordinals
        if 0 <= o < count and exports.get("hipDeviceGetName"):
            nb = ctypes.create_string_buffer(256)
            rcn = fn("hipDeviceGetName")(nb, 255, ctypes.c_int(o))
            row["hipDeviceGetName"] = {**rc_info(rcn),
                                       "name": nb.value.decode("utf-8", "replace")}
        if 0 <= o < count and exports.get("hipDeviceTotalMem"):
            tm = ctypes.c_size_t(0)
            rcm = fn("hipDeviceTotalMem")(ctypes.byref(tm), ctypes.c_int(o))
            row["hipDeviceTotalMem"] = {**rc_info(rcm), "bytes": tm.value}
        r["set_device_matrix"].append(row)
        step("set_device", row)

    # ---- ABI-gated call ----------------------------------------------------
    r["hipGetDeviceProperties"] = {
        "status": "SKIPPED_ABI_UNVERIFIED",
        "reason": "no header provably matching these DLL builds is available on "
                  "this host; mission R1.1.4 forbids unverified hipDeviceProp_t "
                  "layout; hipDeviceGetName/hipDeviceTotalMem used instead",
    }

    # verdict for this DLL
    if count > 0 and any(x["ordinal"] == 0 and x["hipSetDevice"]["rc"] == 0
                         for x in r["set_device_matrix"]):
        verdict = "HIP_SET_OK"
    elif count > 0:
        verdict = "HIP_COUNT_POSITIVE_SET_FAIL"
    elif count == 0:
        verdict = "HIP_DEVICE_COUNT_ZERO"
    else:
        verdict = "HIP_COUNT_QUERY_FAILED"
    r["verdict"] = verdict

    finish_receipt(r, 0, verdict)
    r["steps"] = steps
    atomic_write_json(args.out, r)
    print(f"[{args.tag}] verdict={verdict}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
