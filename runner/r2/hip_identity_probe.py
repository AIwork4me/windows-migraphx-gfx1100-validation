"""R2.1 HIP device identity probe: ordinal -> (name, PCI domain/bus/device).

Loads exactly one amdhip64*.dll (the EP's own amdhip64_7.dll or the
driver-shipped amdhip64_6.dll) and records:

  - hipGetDeviceCount
  - per ordinal: hipDeviceGetAttribute for
      hipDeviceAttributePciBusId    = 13
      hipDeviceAttributePciDeviceId = 14
      hipDeviceAttributePciDomainID = 25
    (enum values are stable ABI, identical numbering in CUDA; validated
    below against the host PnP BDF), plus hipDeviceTotalMem and a
    layout-version-proof ASCII string scan of hipGetDeviceProperties.
  - negative-control ordinals for hipSetDevice.

Self-validation (fail-closed, like dxgi_probe): the caller passes
``--expect-bus/--expect-device/--expect-domain`` from the PnP receipt
(Get-PnpDeviceProperty DEVPKEY_Device_LocationInfo of the RX 7900 XTX).
If the measured BDF does not match, the receipt is marked validated=false
-- we do NOT claim an identity mapping the runtime did not confirm.

One DLL per process (same discipline as R1: HIP runtimes keep global
init state).
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import sys
from datetime import datetime, timezone


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


HIP_ATTR_PCI_BUS_ID = 13
HIP_ATTR_PCI_DEVICE_ID = 14
HIP_ATTR_PCI_DOMAIN_ID = 25
HIP_ERROR_INVALID_DEVICE = 101


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def printable_runs(buf: bytes, min_len: int = 6) -> list:
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
    ap.add_argument("--expect-bus", type=int, default=None,
                    help="PnP PCI bus of the target GPU (validation gate)")
    ap.add_argument("--expect-device", type=int, default=None)
    ap.add_argument("--expect-domain", type=int, default=0)
    args = ap.parse_args()

    receipt = {
        "case_id": f"r2_hip_identity_{args.tag}",
        "phase": "r2",
        "tag": args.tag,
        "requested_dll": args.dll,
        "expect_bdf": {"bus": args.expect_bus, "device": args.expect_device,
                       "domain": args.expect_domain},
        "started_utc": utc_now(),
        "validated": False,
    }

    lib = ctypes.CDLL(args.dll)
    # prove which file Windows actually mapped
    k32 = ctypes.WinDLL("kernel32")
    buf = ctypes.create_unicode_buffer(1024)
    n = k32.GetModuleFileNameW(ctypes.c_void_p(lib._handle), buf, 1024)
    receipt["resolved_module_path"] = buf.value if n else None
    if receipt["resolved_module_path"]:
        receipt["sha256"] = sha256_of(receipt["resolved_module_path"])

    def fn(name):
        f = getattr(lib, name)
        f.restype = ctypes.c_int
        return f

    n = ctypes.c_int(-1)
    rc = fn("hipGetDeviceCount")(ctypes.byref(n))
    receipt["hipGetDeviceCount"] = {"rc": rc, "count": n.value}
    count = n.value if rc == 0 and n.value >= 0 else 0

    devices = []
    for o in range(count):
        attrs = {}
        for label, attr in (("pci_bus_id", HIP_ATTR_PCI_BUS_ID),
                            ("pci_device_id", HIP_ATTR_PCI_DEVICE_ID),
                            ("pci_domain_id", HIP_ATTR_PCI_DOMAIN_ID)):
            v = ctypes.c_int(-1)
            rc_a = fn("hipDeviceGetAttribute")(ctypes.byref(v), ctypes.c_int(attr), ctypes.c_int(o))
            attrs[label] = {"rc": rc_a, "value": v.value, "attr_enum": attr}
        total = ctypes.c_size_t(0)
        rc_m = fn("hipDeviceTotalMem")(ctypes.byref(total), ctypes.c_int(o))
        raw = ctypes.create_string_buffer(4096)
        rc_p = fn("hipGetDeviceProperties")(raw, ctypes.c_int(o))
        devices.append({
            "ordinal": o,
            "attributes": attrs,
            "hipDeviceTotalMem": {"rc": rc_m, "bytes": total.value},
            "hipGetDeviceProperties": {"rc": rc_p,
                                       "ascii_strings_found": [s for s in printable_runs(ctypes.string_at(raw, 4096), 6) if len(s) < 100][:12]},
        })
    receipt["devices"] = devices

    neg = []
    for o in (count, 1, -1, 999):
        rc_s = fn("hipSetDevice")(ctypes.c_int(o))
        neg.append({"ordinal": o, "hipSetDevice_rc": rc_s})
    receipt["negative_controls"] = neg

    # validation: ordinal 0 BDF must match PnP expectation
    if devices and args.expect_bus is not None:
        a = devices[0]["attributes"]
        ok = (a["pci_bus_id"]["rc"] == 0 and a["pci_device_id"]["rc"] == 0
              and a["pci_domain_id"]["rc"] == 0
              and a["pci_bus_id"]["value"] == args.expect_bus
              and a["pci_device_id"]["value"] == args.expect_device
              and a["pci_domain_id"]["value"] == args.expect_domain)
        receipt["bdf_match_ordinal0"] = ok
        receipt["validated"] = ok
    elif devices:
        receipt["bdf_match_ordinal0"] = None  # no expectation supplied
        receipt["validated"] = False

    receipt["finished_utc"] = utc_now()
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(receipt, f, indent=1)
    print(json.dumps({k: receipt[k] for k in
                      ("validated", "resolved_module_path", "sha256",
                       "hipGetDeviceCount", "devices", "bdf_match_ordinal0")
                      if k in receipt}, indent=1))
    return 0 if receipt["validated"] else 3


if __name__ == "__main__":
    sys.exit(main())
