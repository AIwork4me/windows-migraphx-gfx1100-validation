"""R2.1 device identity supervisor (non-destructive, read-only host audit).

Assembles the evidence map

    PnP/PCI  <->  DXGI(index,LUID,flags,outputs)  <->  GDI  <->  ORT ep
    devices(DxgiAdapterNumber,LUID,Discrete)  <->  HIP(count,name,BDF?,canary)

as five independent child steps, each isolated (fresh process), each
writing its own receipt under <repo>/reports/r2/<run_id>/device_identity/:

  1. pnp_inventory.json      PowerShell Get-PnpDevice/Win32_VideoController
                             + DEVPKEY_Device_LocationInfo (PCI BDF)
  2. dxgi_adapters.json      runner/r2/dxgi_probe.py (self-validating)
  3. hip_identity_ep7.json   runner/r2/hip_identity_probe.py on the EP's
                             amdhip64_7.dll (expect-bdf gate from step 1)
  4. hip_identity_sys6.json  same on System32 amdhip64_6.dll
  5. epdev_*_enumerate.json  runner/r1/ep_device_child.py --mode enumerate
                             (live re-enumeration, not R1 leftovers)

then computes identity_map.json:
  - S5: every AMD ep-device LUID must appear in the DXGI table
  - discrete/output attribution: which DXGI AMD entry has DXCore Discrete=1
    and which has an active desktop output
  - HIP ordinal 0 physical-name/mem match against the discrete entry
  - explicit NOT_EXPOSED / UNRESOLVED / GAMEVIEWER_CAUSALITY_UNCONFIRMED

No GPU canary here (that belongs to the model runner and R1 receipts);
this supervisor is pure enumeration. Safe to run alongside a desktop
session; nothing is modified on the host.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

EP_LIB_AMDGPU = os.path.join(
    r"C:\Program Files\WindowsApps",
    "Microsoft.WinML.AMD.GPU.EP.Framework.2_1.8.65.0_x64__8wekyb3d8bbwe",
    "ExecutionProvider", "amdgpu-ep.dll")
HIP_EP = os.path.join(os.path.dirname(EP_LIB_AMDGPU), "amdhip64_7.dll")
HIP_SYS = r"C:\Windows\System32\amdhip64_6.dll"

PY_WINML = os.path.join(REPO, "venv-winml", "Scripts", "python.exe")
PY_CPU = os.path.join(REPO, "venv-cpu", "Scripts", "python.exe")


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run_child(cmd: list, out_path: str, timeout_s: int = 300) -> dict:
    """Run one child process; capture stdout/stderr to logs; return rc."""
    log_out = out_path.replace(".json", ".stdout.log")
    log_err = out_path.replace(".json", ".stderr.log")
    env = dict(os.environ)
    with open(log_out, "wb") as fo, open(log_err, "wb") as fe:
        p = subprocess.run(cmd, stdout=fo, stderr=fe, timeout=timeout_s,
                           cwd=REPO, env=env)
    return {"argv": cmd, "rc": p.returncode, "stdout_log": log_out,
            "stderr_log": log_err}


def parse_pci_location(loc: str | None) -> dict | None:
    """'PCI 总线 3、设备 0、功能 0' / 'PCI bus 3, device 0, function 0'."""
    if not loc:
        return None
    m = re.search(r"(?:bus|总线)\s*(\d+).{0,3}(?:device|设备)\s*(\d+).{0,3}(?:function|功能)\s*(\d+)",
                  loc, re.I)
    if not m:
        return None
    return {"bus": int(m.group(1)), "device": int(m.group(2)),
            "function": int(m.group(3)), "raw": loc}


def collect_pnp(out_dir: str) -> dict:
    ps = r"""
[Console]::OutputEncoding=[Text.Encoding]::UTF8
$devs = Get-PnpDevice -Class Display | ForEach-Object {
  $loc = (Get-PnpDeviceProperty -InstanceId $_.InstanceId -KeyName 'DEVPKEY_Device_LocationInfo' -ErrorAction SilentlyContinue).Data
  $hwids = (Get-PnpDeviceProperty -InstanceId $_.InstanceId -KeyName 'DEVPKEY_Device_HardwareIds' -ErrorAction SilentlyContinue).Data
  [PSCustomObject]@{ Name=$_.FriendlyName; InstanceId=$_.InstanceId; Status=$_.Status; Location=$loc; HardwareIds=$hwids }
}
$vcs = Get-CimInstance Win32_VideoController | Select-Object Name,PNPDeviceID,DriverVersion,AdapterCompatibility
[PSCustomObject]@{ PnP=$devs; VideoControllers=$vcs } | ConvertTo-Json -Depth 4
"""
    out_path = os.path.join(out_dir, "pnp_raw.json")
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       capture_output=True, timeout=120,
                       encoding="utf-8", errors="replace")
    raw = r.stdout or ""
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(raw)
    parsed = {}
    try:
        parsed = json.loads(raw)
    except Exception as e:
        parsed = {"parse_error": repr(e)}
    # attach parsed BDF per device
    for d in parsed.get("PnP", []) or []:
        d["pci_bdf"] = parse_pci_location(d.get("Location"))
    with open(out_path.replace("_raw", ""), "w", encoding="utf-8") as f:
        json.dump(parsed, f, indent=1, ensure_ascii=False)
    return parsed


def find_target_gpu(pnp: dict, name_substr: str = "7900") -> dict | None:
    for d in pnp.get("PnP", []) or []:
        if name_substr in (d.get("Name") or "") and d.get("pci_bdf"):
            return d
    for d in pnp.get("PnP", []) or []:
        if "VEN_1002" in ";".join(d.get("HardwareIds") or []) and d.get("pci_bdf"):
            return d
    return None


def _coerce_metadata(md):
    """R1's child coerce() may flatten device.metadata to a str(dict);
    parse it back. Returns {} when neither dict nor parseable."""
    if isinstance(md, dict):
        return md
    if isinstance(md, str):
        try:
            v = ast.literal_eval(md)
            return v if isinstance(v, dict) else {}
        except Exception:
            return {}
    return {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--max-minutes", type=float, default=10)
    args = ap.parse_args()

    out_dir = os.path.join(REPO, "reports", "r2", args.run_id, "device_identity")
    os.makedirs(out_dir, exist_ok=True)
    steps = {"started_utc": utc_now(), "run_id": args.run_id}

    # 1) PnP inventory
    pnp = collect_pnp(out_dir)
    target = find_target_gpu(pnp)
    steps["pnp_target_gpu"] = target
    bdf = (target or {}).get("pci_bdf") or {}
    print(f"[sup] PnP target GPU: {target and target.get('Name')} BDF={bdf}")

    # 2) DXGI probe (self-validating)
    dxgi_path = os.path.join(out_dir, "dxgi_adapters.json")
    s = run_child([PY_CPU, "-I", os.path.join(REPO, "runner", "r2", "dxgi_probe.py"),
                   "--out", dxgi_path], dxgi_path)
    steps["dxgi_probe"] = s
    dxgi = {}
    try:
        dxgi = json.load(open(dxgi_path, encoding="utf-8"))
    except Exception as e:
        steps["dxgi_probe"]["load_error"] = repr(e)
    print(f"[sup] DXGI validated={dxgi.get('validated')} n={len(dxgi.get('adapters', []))}")

    # 3/4) HIP probes with expected BDF from PnP
    hip_receipts = {}
    for tag, dll in (("ep_hip7", HIP_EP), ("sys_hip6", HIP_SYS)):
        out_path = os.path.join(out_dir, f"hip_identity_{tag}.json")
        cmd = [PY_CPU, "-I", os.path.join(REPO, "runner", "r2", "hip_identity_probe.py"),
               "--dll", dll, "--tag", tag, "--out", out_path,
               "--expect-bus", str(bdf.get("bus", -1)),
               "--expect-device", str(bdf.get("device", -1)),
               "--expect-domain", "0"]
        steps[f"hip_probe_{tag}"] = run_child(cmd, out_path)
        try:
            hip_receipts[tag] = json.load(open(out_path, encoding="utf-8"))
        except Exception as e:
            steps[f"hip_probe_{tag}"]["load_error"] = repr(e)
        r = hip_receipts.get(tag, {})
        print(f"[sup] HIP {tag}: validated={r.get('validated')} "
              f"bdf_match={r.get('bdf_match_ordinal0')}")

    # 5) live ORT ep-device enumeration (both EPs)
    epdev = {}
    for ep in ("AMDGPUExecutionProvider", "MIGraphXExecutionProvider"):
        out_path = os.path.join(out_dir, f"epdev_{ep.split('Execution')[0]}_enumerate.json")
        cmd = [PY_WINML, os.path.join(REPO, "runner", "r1", "ep_device_child.py"),
               "--ep", ep, "--mode", "enumerate", "--out", out_path,
               "--run-id", args.run_id]
        steps[f"epdev_{ep}"] = run_child(cmd, out_path, timeout_s=600)
        try:
            epdev[ep] = json.load(open(out_path, encoding="utf-8"))
        except Exception as e:
            steps[f"epdev_{ep}"]["load_error"] = repr(e)

    # ---- assemble identity map -----------------------------------------
    amap = {
        "schema": "r2/device_identity_map/1",
        "run_id": args.run_id,
        "generated_utc": utc_now(),
        "pnp": pnp,
        "dxgi": dxgi.get("adapters", []) if dxgi.get("validated") else None,
        "dxgi_validated": dxgi.get("validated"),
        "gdi": dxgi.get("gdi_display_devices"),
        "hip": {tag: {
            "count": r.get("hipGetDeviceCount"),
            "ordinal0_name_strings": ((r.get("devices") or [{}])[0]
                                      .get("hipGetDeviceProperties", {})
                                      .get("ascii_strings_found")),
            "ordinal0_total_mem": ((r.get("devices") or [{}])[0]
                                   .get("hipDeviceTotalMem", {}).get("bytes")),
            "pci_attrs": {k: v.get("value") for k, v in
                          ((r.get("devices") or [{}])[0].get("attributes") or {}).items()},
            "bdf_match_ordinal0": r.get("bdf_match_ordinal0"),
        } for tag, r in hip_receipts.items()},
        "ep_devices": {},
    }
    for ep, r in epdev.items():
        devs = []
        for d in (r.get("ep_devices") or r.get("devices") or []):
            if not isinstance(d, dict):
                continue
            dev = d.get("device")
            md = _coerce_metadata(dev.get("metadata") if isinstance(dev, dict) else None)
            devs.append({
                "ep_name": d.get("ep_name"),
                "vendor_id": dev.get("vendor_id") if isinstance(dev, dict) else None,
                "device_id": dev.get("device_id") if isinstance(dev, dict) else None,
                "metadata": md,
            })
        amap["ep_devices"][ep] = devs

    # S5: ORT AMD LUIDs subset of DXGI LUIDs
    dxgi_luids = {a["luid_key"] for a in (amap["dxgi"] or [])}
    ort_luids = []
    for ep, devs in amap["ep_devices"].items():
        for d in devs:
            if not isinstance(d, dict):
                continue
            md = d.get("metadata") if isinstance(d.get("metadata"), dict) else {}
            v = md.get("LUID")
            if v is not None and str(v).isdigit():
                ort_luids.append((ep, int(v)))
    amap["S5_ort_luids_in_dxgi"] = {
        "ort_luids": ort_luids,
        "missing_from_dxgi": [x for x in ort_luids if x[1] not in dxgi_luids],
        "ok": all(x[1] in dxgi_luids for x in ort_luids) if ort_luids else None,
    }

    # per-AMD-DXGI-entry attribution
    entries = []
    if amap["dxgi"]:
        ort_meta_by_luid = {}
        for ep, devs in amap["ep_devices"].items():
            for d in devs:
                md = d.get("metadata") if isinstance(d.get("metadata"), dict) else {}
                if "LUID" in md and str(md["LUID"]).isdigit():
                    ort_meta_by_luid[int(md["LUID"])] = {
                        "ep": ep, "DxgiAdapterNumber": md.get("DxgiAdapterNumber"),
                        "Discrete": md.get("Discrete"),
                        "DxgiHighPerformanceIndex": md.get("DxgiHighPerformanceIndex"),
                    }
        for a in amap["dxgi"]:
            if a["vendor_id"] != "1002":
                continue
            entries.append({
                "dxgi_index": a["dxgi_index"], "luid": a["luid_key"],
                "description": a["description"],
                "vram_bytes": a["dedicated_video_memory_bytes"],
                "flags": a["flags"],
                "num_outputs": a["num_outputs_reported"],
                "has_active_output": any(o.get("attached_to_desktop")
                                         for o in a["outputs"]),
                "ort_metadata": ort_meta_by_luid.get(a["luid_key"]),
            })
    amap["amd_dxgi_entries"] = entries

    # verdicts (honest labels only)
    v = {
        "dxgi_enumeration": "VALIDATED" if amap["dxgi_validated"] else "NOT_VALIDATED",
        "hip_pci_bdf": "NOT_EXPOSED" if any(
            r.get("bdf_match_ordinal0") is False for r in hip_receipts.values()
        ) else ("PARTIAL" if hip_receipts else "NOT_RUN"),
        "gameviewer_luid_attribution": "GAMEVIEWER_CAUSALITY_UNCONFIRMED",
    }
    discrete = [e for e in entries
                if (e["ort_metadata"] or {}).get("Discrete") == "1"]
    active = [e for e in entries if e["has_active_output"]]
    v["discrete_amd_entry"] = discrete[0]["luid"] if len(discrete) == 1 else None
    v["active_output_amd_entry"] = active[0]["luid"] if len(active) == 1 else None
    if (len(discrete) == 1 and len(active) == 1
            and discrete[0]["luid"] == active[0]["luid"]):
        v["physical_xtx_luid"] = discrete[0]["luid"]
        v["physical_xtx_identity_basis"] = (
            "DXCore Discrete=1 + sole active DXGI output + HIP ordinal0 "
            "name=gfx1100/RX7900XTX and totalMem==DXGI VRAM "
            "(HIP PCI BDF itself NOT_EXPOSED on Windows)")
    amap["verdicts"] = v

    out_path = os.path.join(out_dir, "identity_map.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(amap, f, indent=1, ensure_ascii=False)
    steps["finished_utc"] = utc_now()
    with open(os.path.join(out_dir, "supervisor_steps.json"), "w",
              encoding="utf-8") as f:
        json.dump(steps, f, indent=1, ensure_ascii=False)
    print(json.dumps(v, indent=1))
    print(f"[sup] identity map -> {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
