"""Shared helpers for R1 receipts (schema, redaction, atomic write)."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import platform
import re
import tempfile
from datetime import datetime, timezone

SCHEMA_VERSION = "r1/1"
HOST_SUMMARY = {
    "machine": platform.machine(),
    "processor": platform.processor(),
    "python": platform.python_version(),
    "os": platform.platform(),
}

# Environment variables whose *presence* matters for device visibility.
VISIBILITY_ENV_VARS = [
    "HIP_VISIBLE_DEVICES", "ROCR_VISIBLE_DEVICES", "CUDA_VISIBLE_DEVICES",
    "GPU_DEVICE_ORDINAL", "HSA_OVERRIDE_GFX_VERSION", "HIP_HIDDEN_DEVICES",
    "AMD_VISIBLE_DEVICES", "GGML_VISIBLE_DEVICES",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_receipt(case_id: str, run_id: str, argv=None, extra=None) -> dict:
    r = {
        "schema_version": SCHEMA_VERSION,
        "case_id": case_id,
        "run_id": run_id,
        "source_head": os.environ.get("R1_SOURCE_HEAD", "<unset>"),
        "started_utc": utc_now(),
        "real_hardware": True,
        "isolated_subprocess": True,
        "argv": list(argv or []),
        "host_summary": dict(HOST_SUMMARY),
    }
    if extra:
        r.update(extra)
    return r


def finish_receipt(r: dict, exit_code, outcome, notes=None) -> dict:
    r["finished_utc"] = utc_now()
    r["exit_code"] = exit_code
    r["outcome"] = outcome
    if notes:
        r["notes"] = notes
    return r


def atomic_write_json(path: str, obj) -> None:
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=1, ensure_ascii=False)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def pe_machine(path: str) -> str:
    """PE header Machine field (arch proof) without executing anything."""
    try:
        with open(path, "rb") as f:
            f.seek(0x3C)
            off = int.from_bytes(f.read(4), "little")
            f.seek(off + 4)
            m = int.from_bytes(f.read(2), "little")
        return {0x8664: "x64", 0xAA64: "arm64", 0x14C: "x86"}.get(m, hex(m))
    except Exception:
        return "UNKNOWN"


def file_version(path: str) -> dict:
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
        base = ctypes.string_at(p.value, 24)
        if int.from_bytes(base[0:4], "little") != 0xFEEF04BD:
            return {}
        fms = int.from_bytes(base[8:12], "little")
        fls = int.from_bytes(base[12:16], "little")
        pms = int.from_bytes(base[16:20], "little")
        pls = int.from_bytes(base[20:24], "little")
        fmt = lambda ms, ls: f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
        return {"file_version": fmt(fms, fls), "product_version": fmt(pms, pls)}
    except Exception as e:
        return {"error": repr(e)}


def visibility_env_snapshot() -> dict:
    """Presence-only snapshot of device-visibility env vars (values redacted)."""
    out = {}
    for k in VISIBILITY_ENV_VARS:
        v = os.environ.get(k)
        out[k] = "<absent>" if v is None else "<present, value redacted>"
    return out


# --- redaction for anything that may reach the public repo -----------------

REDACTIONS = [
    (re.compile(re.escape(os.path.expanduser("~")), re.IGNORECASE), "<USERHOME>"),
    (re.compile(r"(?i)C:\\users\\[a-z0-9_. -]+"), "C:\\Users\\<USER>"),
    (re.compile(r"(?i)computername=[a-z0-9_-]+"), "computername=<HOST>"),
    (re.compile(r"\b[A-F0-9]{8}-[A-F0-9]{4}-[A-F0-9]{4}-[A-F0-9]{4}-[A-F0-9]{12}\b"), "<GUID>"),
]


def redact_text(s: str) -> str:
    for pat, rep in REDACTIONS:
        s = pat.sub(lambda m, _r=rep: _r, s)  # literal replacement, no \-escapes
    return s


def module_snapshot(only_amd: bool = True) -> list:
    """All (or AMD/HIP-related) modules mapped into this process.

    Used to audit which amdhip64*.dll an EP-bearing process actually loaded.
    """
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
        k32.CreateToolhelp32Snapshot.argtypes = [ctypes.c_uint, ctypes.c_uint]

        class MODULEENTRY32W(ctypes.Structure):
            _fields_ = [("dwSize", ctypes.c_ulong),
                        ("th32ModuleID", ctypes.c_ulong),
                        ("th32ProcessID", ctypes.c_ulong),
                        ("GlblcntUsage", ctypes.c_ulong),
                        ("ProccntUsage", ctypes.c_ulong),
                        ("modBaseAddr", ctypes.c_void_p),
                        ("modBaseSize", ctypes.c_ulong),
                        ("hModule", ctypes.c_void_p),
                        ("szModule", ctypes.c_wchar * 256),
                        ("szExePath", ctypes.c_wchar * 260)]

        TH32CS_SNAPMODULE = 0x8
        TH32CS_SNAPMODULE32 = 0x10
        snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, 0)
        if not snap or snap == 0xFFFFFFFFFFFFFFFF:
            return ["<snapshot failed: invalid handle>"]
        try:
            e = MODULEENTRY32W()
            e.dwSize = ctypes.sizeof(MODULEENTRY32W)
            out = []
            ok = k32.Module32FirstW(ctypes.c_void_p(snap), ctypes.byref(e))
            while ok:
                path = e.szExePath
                name = e.szModule.lower()
                if (not only_amd) or ("amd" in name or "hip" in name
                                      or "migraphx" in name or "miopen" in name):
                    out.append(path)
                ok = k32.Module32NextW(ctypes.c_void_p(snap), ctypes.byref(e))
            return out
        finally:
            k32.CloseHandle(ctypes.c_void_p(snap))
    except Exception as ex:
        return [f"<snapshot failed: {ex!r}>"]


def redact_json(obj):
    if isinstance(obj, str):
        return redact_text(obj)
    if isinstance(obj, list):
        return [redact_json(x) for x in obj]
    if isinstance(obj, dict):
        return {k: redajson_v(k, v) for k, v in obj.items()}
    return obj


def redajson_v(k, v):
    kl = str(k).lower()
    if any(t in kl for t in ("token", "secret", "password")):
        return "<redacted>"
    return redact_json(v)
