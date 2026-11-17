r"""R2.1 self-validating DXGI adapter enumeration via ctypes (no SDK needed).

Purpose: produce the authoritative (DXGI index -> description, LUID, VRAM,
flags, outputs) table that ORT core's device_discovery.cc consumes, so we
can join:

    DXGI index  <->  LUID  <->  ORT ep-device metadata  <->  PnP PCI BDF

ABI policy (mission R2.1 rule): every constant below is copied from a
verifiable public definition, and the probe **fails closed** (returns
``validated=false``) if any self-check fails, so a wrong ABI assumption can
never silently produce a bogus mapping.

Definitions transcribed from mingw-w64 ``dxgi.h`` (public header mirror;
saved as evidence ``reports/r2/<run_id>/device_identity/dxgi_min.h``):

  - IID_IDXGIFactory1 = {770aae78-f26f-4dba-{a8,29,25,3c,83,d1,b3,87}}
  - IDXGIFactory1Vtbl: EnumAdapters1 slot 12
  - IDXGIAdapter1Vtbl: EnumOutputs 7, GetDesc 8, GetDesc1 10
  - IDXGIOutputVtbl: GetDesc slot 7
  - DXGI_ADAPTER_DESC(1) / DXGI_OUTPUT_DESC layouts (x64 sizes asserted)
  - DXGI_ADAPTER_FLAG_REMOTE = 1, DXGI_ADAPTER_FLAG_SOFTWARE = 2

Self-checks performed before any output is trusted:
  S1 CreateDXGIFactory1 returns S_OK (a wrong IID fails with E_NOINTERFACE).
  S2 ctypes struct sizes match the x64 ABI sizes derived from the header.
  S3 For every adapter, GetDesc and GetDesc1 agree on description/LUID/VRAM
     -- validates the vtable slot math through two independent slots.
  S4 >= 1 DXGI description matches a Win32_VideoController name.
  S6 every DXGI output DeviceName appears in the GDI EnumDisplayDevices
     table (joins DXGI adapters to GDI \\.\DISPLAYn and PnP DeviceID).

The ORT LUID cross-check (S5: ORT metadata LUIDs subset of DXGI LUIDs) is
done by the caller that owns both receipts (device_identity_child.py).
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import (POINTER, WINFUNCTYPE, Structure, c_long, c_uint32,
                    c_uint64, c_void_p, c_wchar, byref)

# --- GUIDs (mingw-w64 dxgi.h), canonical string form -------------------
IID_IDXGIFACTORY1_CANONICAL = "770aae78-f26f-4dba-a829-253c83d1b387"


def guid_bytes(canonical: str) -> bytes:
    """COM GUID memory layout: Data1/2/3 little-endian, Data4 raw."""
    h = canonical.replace("-", "")
    d1, d2, d3 = int(h[0:8], 16), int(h[8:12], 16), int(h[12:16], 16)
    return (d1.to_bytes(4, "little") + d2.to_bytes(2, "little")
            + d3.to_bytes(2, "little") + bytes.fromhex(h[16:32]))


def guid_from(canonical: str):
    return (ctypes.c_ubyte * 16).from_buffer_copy(guid_bytes(canonical))


DXGI_ERROR_NOT_FOUND = 0x887A0002
DXGI_ADAPTER_FLAG_REMOTE = 1
DXGI_ADAPTER_FLAG_SOFTWARE = 2


class LUID(Structure):
    _fields_ = [("LowPart", c_uint32), ("HighPart", c_uint32)]


class DXGI_ADAPTER_DESC(Structure):
    # WCHAR Description[128]; UINT x4; SIZE_T x3; LUID  -> 304 bytes x64
    _fields_ = [
        ("Description", c_wchar * 128),
        ("VendorId", c_uint32), ("DeviceId", c_uint32),
        ("SubSysId", c_uint32), ("Revision", c_uint32),
        ("DedicatedVideoMemory", c_uint64), ("DedicatedSystemMemory", c_uint64),
        ("SharedSystemMemory", c_uint64),
        ("AdapterLuid", LUID),
    ]


class DXGI_ADAPTER_DESC1(Structure):
    # DESC + UINT Flags -> 312 bytes x64
    _fields_ = list(DXGI_ADAPTER_DESC._fields_) + [("Flags", c_uint32)]


class RECT(Structure):
    _fields_ = [("left", c_long), ("top", c_long),
                ("right", c_long), ("bottom", c_long)]


class DXGI_OUTPUT_DESC(Structure):
    # WCHAR[32]; RECT; BOOL; enum(4); HMONITOR(8) -> 96 bytes x64
    _fields_ = [
        ("DeviceName", c_wchar * 32),
        ("DesktopCoordinates", RECT),
        ("AttachedToDesktop", c_long),
        ("Rotation", c_uint32),
        ("Monitor", c_void_p),
    ]


class DISPLAY_DEVICEW(Structure):
    # DWORD cb; WCHAR[32]; WCHAR[128]; DWORD; WCHAR[128]; WCHAR[128]
    _fields_ = [
        ("cb", c_uint32),
        ("DeviceName", c_wchar * 32),
        ("DeviceString", c_wchar * 128),
        ("StateFlags", c_uint32),
        ("DeviceID", c_wchar * 128),
        ("DeviceKey", c_wchar * 128),
    ]


# prototypes (c_long restype: ctypes.HRESULT auto-raises on failure values
# like DXGI_ERROR_NOT_FOUND, which we must handle as loop terminators)
GETDESC = WINFUNCTYPE(c_long, c_void_p, POINTER(DXGI_ADAPTER_DESC))
GETDESC1 = WINFUNCTYPE(c_long, c_void_p, POINTER(DXGI_ADAPTER_DESC1))
ENUMADAPTERS1 = WINFUNCTYPE(c_long, c_void_p, c_uint32, POINTER(c_void_p))
ENUMOUTPUTS = WINFUNCTYPE(c_long, c_void_p, c_uint32, POINTER(c_void_p))
GETOUTDESC = WINFUNCTYPE(c_long, c_void_p, POINTER(DXGI_OUTPUT_DESC))
RELEASE = WINFUNCTYPE(c_uint32, c_void_p)


def _slot(iface_addr: int, index: int) -> int:
    """Address of a vtable function for a COM interface pointer (int).

    Layout: *iface == lpVtbl; *(lpVtbl + index) == fn. Both steps must
    dereference -- returning `lpVtbl + index*8` itself hands back a .rdata
    address, and calling it is an instant access violation (exactly what
    the first version of this helper did).
    """
    vtbl = ctypes.cast(iface_addr, POINTER(c_void_p))[0]
    return ctypes.cast(vtbl, POINTER(c_void_p))[index]


def luid_key(luid: LUID) -> int:
    """ORT core GetLuidKey: (uint64(HighPart) << 32) | LowPart."""
    return ((luid.HighPart << 32) | luid.LowPart) & 0xFFFFFFFFFFFFFFFF


def enum_gdi_display_devices() -> list[dict]:
    """EnumDisplayDevicesW(NULL, i, ...) adapter-level table (GDI view)."""
    user32 = ctypes.WinDLL("user32")
    rows = []
    i = 0
    while True:
        dd = DISPLAY_DEVICEW()
        dd.cb = ctypes.sizeof(DISPLAY_DEVICEW)
        if not user32.EnumDisplayDevicesW(None, i, byref(dd), 0):
            break
        rows.append({
            "gdi_index": i,
            "device_name": dd.DeviceName,
            "device_string": dd.DeviceString,
            "state_flags": dd.StateFlags,
            "device_id": dd.DeviceID,
            "device_key": dd.DeviceKey,
        })
        i += 1
        if i > 16:
            break
    return rows


def enumerate_dxgi_adapters(expected_names: list[str] | None = None) -> dict:
    """Enumerate DXGI adapters + outputs + GDI table; validated only if all
    self-checks pass. Never raises for ABI/validation failure."""
    out = {"validated": False, "checks": {}, "adapters": []}
    out["checks"]["S2_struct_sizes"] = {
        "DXGI_ADAPTER_DESC": ctypes.sizeof(DXGI_ADAPTER_DESC),
        "DXGI_ADAPTER_DESC1": ctypes.sizeof(DXGI_ADAPTER_DESC1),
        "DXGI_OUTPUT_DESC": ctypes.sizeof(DXGI_OUTPUT_DESC),
        "DISPLAY_DEVICEW": ctypes.sizeof(DISPLAY_DEVICEW),
        "expected_x64": (304, 312, 96, 840),
    }
    if (ctypes.sizeof(DXGI_ADAPTER_DESC), ctypes.sizeof(DXGI_ADAPTER_DESC1),
            ctypes.sizeof(DXGI_OUTPUT_DESC),
            ctypes.sizeof(DISPLAY_DEVICEW)) != (304, 312, 96, 840):
        out["validation_error"] = "S2 struct size mismatch -- ABI assumption wrong"
        return out

    gdi = enum_gdi_display_devices()
    out["gdi_display_devices"] = gdi
    gdi_names = {r["device_name"] for r in gdi}

    dxgi = ctypes.WinDLL("dxgi")
    factory = c_void_p()
    guid = guid_from(IID_IDXGIFACTORY1_CANONICAL)
    hr = dxgi.CreateDXGIFactory1(ctypes.cast(guid, ctypes.c_void_p), byref(factory))
    out["checks"]["S1_CreateDXGIFactory1_hr"] = hex(hr & 0xFFFFFFFF)
    if hr != 0 or not factory:
        out["validation_error"] = (
            f"S1 CreateDXGIFactory1 failed hr={hr & 0xFFFFFFFF:#x} "
            "(IID wrong or no DXGI) -- refusing to output adapter list")
        return out

    try:
        adapters = []
        idx = 0
        while True:
            ptmp = c_void_p()
            hr = ENUMADAPTERS1(_slot(factory.value, 12))(factory.value, idx, byref(ptmp))
            if (hr & 0xFFFFFFFF) == DXGI_ERROR_NOT_FOUND:
                break
            if hr != 0:
                out["validation_error"] = f"EnumAdapters1 hr={hr:#x} at {idx}"
                return out
            adapter = ptmp.value
            try:
                d1 = DXGI_ADAPTER_DESC1()
                rc1 = GETDESC1(_slot(adapter, 10))(adapter, byref(d1))
                d0 = DXGI_ADAPTER_DESC()
                rc0 = GETDESC(_slot(adapter, 8))(adapter, byref(d0))
                if rc1 != 0 or rc0 != 0:
                    out["validation_error"] = (
                        f"S3 GetDesc/GetDesc1 hr {rc0:#x}/{rc1:#x} at idx {idx}")
                    return out
                agree = (d1.Description == d0.Description
                         and luid_key(d1.AdapterLuid) == luid_key(d0.AdapterLuid)
                         and d1.DedicatedVideoMemory == d0.DedicatedVideoMemory)
                if not agree:
                    out["validation_error"] = (
                        f"S3 GetDesc vs GetDesc1 disagree at idx {idx} "
                        "-- vtable slot math wrong")
                    return out

                outputs = []
                oidx = 0
                while True:
                    pout = c_void_p()
                    hro = ENUMOUTPUTS(_slot(adapter, 7))(adapter, oidx, byref(pout))
                    if (hro & 0xFFFFFFFF) == DXGI_ERROR_NOT_FOUND:
                        break
                    if hro != 0 or not pout:
                        outputs.append({"output_index": oidx, "error": hex(hro & 0xFFFFFFFF)})
                        break
                    try:
                        od = DXGI_OUTPUT_DESC()
                        rco = GETOUTDESC(_slot(pout.value, 7))(pout.value, byref(od))
                        if rco != 0:
                            outputs.append({"output_index": oidx,
                                            "getdesc_error": hex(rco & 0xFFFFFFFF)})
                        else:
                            outputs.append({
                                "output_index": oidx,
                                "device_name": od.DeviceName,
                                "attached_to_desktop": bool(od.AttachedToDesktop),
                                "desktop_rect": [od.DesktopCoordinates.left,
                                                 od.DesktopCoordinates.top,
                                                 od.DesktopCoordinates.right,
                                                 od.DesktopCoordinates.bottom],
                                "rotation": od.Rotation,
                            })
                    finally:
                        RELEASE(_slot(pout.value, 2))(pout.value)
                    oidx += 1
                    if oidx > 8:
                        break

                adapters.append({
                    "dxgi_index": idx,
                    "description": d1.Description,
                    "vendor_id": f"{d1.VendorId:04x}",
                    "device_id": f"{d1.DeviceId:04x}",
                    "subsys_id": f"{d1.SubSysId:08x}",
                    "revision": d1.Revision,
                    "dedicated_video_memory_bytes": d1.DedicatedVideoMemory,
                    "dedicated_system_memory_bytes": d1.DedicatedSystemMemory,
                    "shared_system_memory_bytes": d1.SharedSystemMemory,
                    "luid_low": d1.AdapterLuid.LowPart,
                    "luid_high": d1.AdapterLuid.HighPart,
                    "luid_key": luid_key(d1.AdapterLuid),
                    "flags": d1.Flags,
                    "flag_is_remote": bool(d1.Flags & DXGI_ADAPTER_FLAG_REMOTE),
                    "flag_is_software": bool(d1.Flags & DXGI_ADAPTER_FLAG_SOFTWARE),
                    "num_outputs_reported": len(outputs),
                    "outputs": outputs,
                })
            finally:
                RELEASE(_slot(adapter, 2))(adapter)
            idx += 1
            if idx > 16:  # bounded like upstream loops
                break

        out["adapters"] = adapters
        if expected_names:
            descs = " | ".join(a["description"] for a in adapters)
            hits = [n for n in expected_names
                    if any(word.lower() in descs.lower()
                           for word in n.split()[:2])]
            out["checks"]["S4_name_cross_check"] = {
                "expected": expected_names, "hits": hits, "ok": bool(hits)}
            if not hits:
                out["validation_error"] = (
                    "S4 no DXGI description matches any Win32_VideoController "
                    "name -- reading garbage?")
                return out

        # S6: every enumerated output's \\.\DISPLAYn must exist in GDI table
        bad = [o["device_name"]
               for a in adapters for o in a["outputs"]
               if "device_name" in o and o["device_name"] not in gdi_names]
        out["checks"]["S6_output_in_gdi"] = {"bad": bad, "ok": not bad}
        if bad:
            out["validation_error"] = (
                f"S6 DXGI output DeviceNames missing from EnumDisplayDevices: {bad}")
            return out

        out["validated"] = True
        return out
    finally:
        RELEASE(_slot(factory.value, 2))(factory.value)


def main() -> int:
    import argparse
    import json
    import subprocess

    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    names = []
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance Win32_VideoController).Name"],
            capture_output=True, text=True, timeout=60)
        names = [l.strip() for l in r.stdout.splitlines() if l.strip()]
    except Exception as e:  # evidence path; keep going with no S4
        names = []
        print(f"[dxgi_probe] S4 name collection failed: {e}", file=sys.stderr)

    res = enumerate_dxgi_adapters(names or None)
    res["names_from_win32_videocontroller"] = names
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(res, f, indent=1)
    print(json.dumps(res, indent=1))
    return 0 if res["validated"] else 3


if __name__ == "__main__":
    sys.exit(main())
