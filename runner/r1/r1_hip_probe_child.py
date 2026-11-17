"""R1.1 child: independent HIP Runtime probe for one amdhip64*.dll.

Loads a single candidate DLL via ctypes (no onnxruntime, no WinML, no CUDA-
style wrapper), then exercises the minimal HIP device-init API surface:

    hipGetDeviceCount / hipSetDevice / hipGetDevice
    hipRuntimeGetVersion / hipDriverGetVersion
    hipGetDeviceProperties / hipDeviceTotalMem / hipErrorString

and records, as hard evidence:
  - the module path Windows actually resolved (GetModuleFileNameW of the
    loaded handle -- proves the load path, not the requested path)
  - SHA256 + file version of that exact file
  - every call's raw HIP return code
  - negative-control ordinals (-1, 999, count) that must return
    hipErrorInvalidDevice(101); if ordinal 0 also returns 101 the round-1
    EP failure is reproduced *inside the raw runtime*, independent of ORT.

Run one DLL per process: HIP runtimes keep global init state, so two
amdhip64 major versions must never be probed in one process.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import sys
from datetime import datetime, timezone


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def file_version(path: str) -> dict:
    """VS_FIXEDFILEINFO via version.dll; returns {} when absent."""
    try:
        ver = ctypes.WinDLL("version")
        size = ver.GetFileVersionInfoSizeW(path, None)
        if not size:
            return {}
        buf = ctypes.create_string_buffer(size)
        if not ver.GetFileVersionInfoW(path, 0, size, buf):
            return {}
        p = ctypes.c_void_p()
        ln = ctypes.c_uint()
        if not ver.VerQueryValueW(buf, "\\", ctypes.byref(p), ctypes.byref(ln)):
            return {}
        # VS_FIXEDFILEINFO: dwSignature(0xFEEF04BD) ... fileVersionMS/LS at +8/+12
        base = ctypes.string_at(p.value, 52)
        sig = int.from_bytes(base[0:4], "little")
        if sig != 0xFEEF04BD:
            return {"signature": hex(sig)}
        fms = int.from_bytes(base[8:12], "little")
        fls = int.from_bytes(base[12:16], "little")
        pms = int.from_bytes(base[16:20], "little")
        pls = int.from_bytes(base[20:24], "little")
        fmt = lambda ms, ls: f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
        return {"file_version": fmt(fms, fls), "product_version": fmt(pms, pls)}
    except Exception as e:  # pragma: no cover - evidence path
        return {"error": repr(e)}


HIP_PROBE_SYMBOLS = [
    "hipGetDeviceCount",
    "hipSetDevice",
    "hipGetDevice",
    "hipRuntimeGetVersion",
    "hipDriverGetVersion",
    "hipGetDeviceProperties",
    "hipDeviceTotalMem",
    "hipErrorString",
    "hipGetVersionString",
]

HIP_ERRORS = {
    0: "hipSuccess",
    1: "hipErrorInvalidValue",
    101: "hipErrorInvalidDevice",
    304: "hipErrorNoDevice",
    305: "hipErrorUnknown",
    306: "hipErrorInvalidContext",  # approximate map; raw code always kept
}


def printable_runs(buf: bytes, min_len: int = 4) -> list:
    runs, cur = [], bytearray()
    for b in buf:
        if 32 <= b < 127:
            cur.append(b)
        else:
            if len(cur) >= min_len:
                runs.append(cur.decode("ascii"))
            cur = bytearray()
    if len(cur) >= min_len:
        runs.append(cur.decode("ascii"))
    return runs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dll", required=True)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    receipt = {
        "case_id": f"r1_hip_probe_{args.tag}",
        "phase": "r1",
        "tag": args.tag,
        "requested_dll": args.dll,
        "started_utc": utc_now(),
        "steps": [],
    }

    def step(label, data):
        data = dict(data)
        data["step"] = label
        receipt["steps"].append(data)
        print(f"[{args.tag}] {label}: {data}", flush=True)

    # ---- load the DLL --------------------------------------------------
    dll_dir = os.path.dirname(args.dll)
    if os.path.isfile(args.dll):
        os.add_dll_directory(dll_dir)
        try:
            lib = ctypes.CDLL(args.dll)
            receipt["load"] = {"ok": True}
            step("load_dll", {"ok": True, "mode": "ctypes.CDLL(abs path)"})
        except OSError as e:
            receipt["load"] = {"ok": False, "error": str(e), "winerror": getattr(e, "winerror", None)}
            step("load_dll", {"ok": False, "error": str(e)})
            receipt["finished_utc"] = utc_now()
            with open(args.out, "w", encoding="utf-8") as f:
                json.dump(receipt, f, indent=1)
            return 2
    else:
        receipt["load"] = {"ok": False, "error": "file not found"}
        step("load_dll", {"ok": False, "error": "file not found"})
        receipt["finished_utc"] = utc_now()
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(receipt, f, indent=1)
        return 2

    # ---- prove which file Windows actually mapped ----------------------
    try:
        k32 = ctypes.WinDLL("kernel32")
        buf = ctypes.create_unicode_buffer(1024)
        n = k32.GetModuleFileNameW(ctypes.c_void_p(lib._handle), buf, 1024)
        resolved = buf.value if n else None
    except Exception as e:
        resolved = f"<err {e}>"
    receipt["resolved_module_path"] = resolved
    step("resolved_module_path", {"path": resolved})

    if resolved and os.path.isfile(resolved):
        receipt["sha256"] = sha256_of(resolved)
        receipt["file_size"] = os.path.getsize(resolved)
        receipt["file_version"] = file_version(resolved)
        step("file_identity", {"sha256": receipt["sha256"], "size": receipt["file_size"], "version": receipt["file_version"]})

    # ---- exports present? ----------------------------------------------
    exports = {}
    for sym in HIP_PROBE_SYMBOLS:
        try:
            exports[sym] = bool(getattr(lib, sym, None))
        except Exception:
            exports[sym] = False
    receipt["exports"] = exports
    step("exports", exports)

    def fn(name, restype=ctypes.c_int):
        f = getattr(lib, name)
        f.restype = restype
        return f

    def hiprc(code: int) -> dict:
        d = {"rc": code, "name": HIP_ERRORS.get(code, "hipError(?)")}
        try:
            es = fn("hipErrorString", ctypes.c_char_p)(code)
            if es:
                d["text"] = es.decode("utf-8", "replace")
        except Exception:
            pass
        return d

    # ---- version APIs ---------------------------------------------------
    v = ctypes.c_int(-1)
    rc = fn("hipRuntimeGetVersion")(ctypes.byref(v))
    receipt["hipRuntimeGetVersion"] = {**hiprc(rc), "value": v.value}
    step("hipRuntimeGetVersion", receipt["hipRuntimeGetVersion"])
    v2 = ctypes.c_int(-1)
    rc = fn("hipDriverGetVersion")(ctypes.byref(v2))
    receipt["hipDriverGetVersion"] = {**hiprc(rc), "value": v2.value}
    step("hipDriverGetVersion", receipt["hipDriverGetVersion"])

    if exports.get("hipGetVersionString"):
        sbuf = ctypes.create_string_buffer(256)
        rc = fn("hipGetVersionString")(sbuf, 256)
        receipt["hipGetVersionString"] = {**hiprc(rc), "value": sbuf.value.decode()}
        step("hipGetVersionString", receipt["hipGetVersionString"])

    # ---- device enumeration --------------------------------------------
    n = ctypes.c_int(-1)
    rc = fn("hipGetDeviceCount")(ctypes.byref(n))
    receipt["hipGetDeviceCount"] = {**hiprc(rc), "count": n.value}
    step("hipGetDeviceCount", receipt["hipGetDeviceCount"])

    count = n.value if rc == 0 and n.value >= 0 else -1
    receipt["set_device_matrix"] = []
    ordinals = list(range(0, max(count, 0))) + [count, -1, 999]
    seen = set()
    for o in ordinals:
        if o in seen:
            continue
        seen.add(o)
        rc = fn("hipSetDevice")(ctypes.c_int(o))
        cur = ctypes.c_int(-9)
        rc2 = fn("hipGetDevice")(ctypes.byref(cur))
        row = {"ordinal": o, "hipSetDevice": hiprc(rc), "hipGetDevice": {**hiprc(rc2), "value": cur.value}}
        receipt["set_device_matrix"].append(row)
        step("set_device", row)

    # ---- per-device properties (string scan, layout-version-proof) -----
    receipt["devices"] = []
    if count > 0 and exports.get("hipGetDeviceProperties"):
        for o in range(count):
            raw = ctypes.create_string_buffer(4096)
            try:
                rc = fn("hipGetDeviceProperties")(raw, ctypes.c_int(o))
                strings = [s for s in printable_runs(ctypes.string_at(raw, 4096), 6)
                           if len(s) < 100][:12]
                total = ctypes.c_size_t(0)
                rcm = fn("hipDeviceTotalMem")(ctypes.byref(total), ctypes.c_int(o))
                receipt["devices"].append({
                    "ordinal": o,
                    "hipGetDeviceProperties": hiprc(rc),
                    "ascii_strings_found": strings,
                    "hipDeviceTotalMem": {**hiprc(rcm), "bytes": total.value},
                })
                step("device_props", {"ordinal": o, "rc": rc, "strings": strings[:4], "total_mem": total.value})
            except Exception as e:
                receipt["devices"].append({"ordinal": o, "error": repr(e)})
                step("device_props", {"ordinal": o, "error": repr(e)})

    receipt["finished_utc"] = utc_now()
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(receipt, f, indent=1)
    print(f"[{args.tag}] receipt -> {args.out}", flush=True)
    # ORT/WinML never involved; no lingering threads expected. Plain exit.
    return 0


if __name__ == "__main__":
    sys.exit(main())
