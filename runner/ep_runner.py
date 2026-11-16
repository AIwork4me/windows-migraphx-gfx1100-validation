"""Shared per-case EP session runner used by M1/M3/M5 phase scripts.

Encapsulates: EP registration check, session creation via the plugin device
API, profiling with node->provider attribution, by-name CPU parity, ledger
upsert. One helper, many thin phase scripts.
"""
from __future__ import annotations
import json, os, sys, time, traceback

sys.path.insert(0, os.path.dirname(__file__))
import common as C
import ledger as L

_registered: dict[str, str] = {}  # ep_name -> libraryPath


def ep_device_info(d) -> dict:
    info = {"ep_name": getattr(d, "ep_name", "?")}
    dev = getattr(d, "device", None)
    for attr in ("device_id", "vendor_id", "vendor", "type", "architecture", "metadata"):
        try:
            info[attr] = str(getattr(dev, attr))
        except Exception:
            pass
    return info


def matching_devices(ort, ep_name: str) -> list:
    devs = [d for d in ort.get_ep_devices() if d.ep_name == ep_name]
    def is_amd(d):
        try:
            return "Advanced Micro Devices" in str(getattr(d.device, "vendor", ""))
        except Exception:
            return False
    amd = [d for d in devs if is_amd(d)]
    return amd + [d for d in devs if d not in amd]


def add_ep_for_devices(so, ort, ep, opts: dict | None = None) -> int:
    """add_provider_for_devices(Sequence[OrtEpDevice], Mapping[str,str]).

    AMD plugin EPs reject multi-device selection ("only supports selection
    for a single device"), so we add exactly ONE device per SessionOptions.
    """
    devs = matching_devices(ort, ep)
    if devs:
        so.add_provider_for_devices([devs[0]], dict(opts or {}))
    return len(devs)


def register_ep(ep_name: str, lib_path_variants: list[str] | None = None) -> dict:
    """Ensure an EP library is registered in this process. Idempotent."""
    import onnxruntime as ort
    if ep_name in _registered:
        return {"ok": True, "cached": True, "lib": _registered[ep_name]}
    out: dict = {"ok": False}
    # discover from ep_devices first (maybe already registered process-wide)
    try:
        for d in ort.get_ep_devices():
            if d.ep_name == ep_name:
                _registered[ep_name] = f"<already:{ep_name}>"
                return {"ok": True, "already_registered": True}
    except Exception as e:
        out["devices_err"] = repr(e)
    for lib in (lib_path_variants or []):
        if lib and os.path.exists(lib):
            try:
                try:
                    ort.register_execution_provider_library(ep_name, lib)
                except TypeError:
                    ort.register_execution_provider_library(lib)
                _registered[ep_name] = lib
                out.update({"ok": True, "lib": lib})
                return out
            except Exception as e:
                out.setdefault("attempts", []).append({"lib": lib, "err": repr(e)})
    out["error"] = "no usable library path"
    return out


def find_winml_ep_library(ep_name: str) -> str | None:
    """Query the WinML EP catalog (C API) for an installed EP's library path."""
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "phases"))
        from m0_ep_discovery import EpCatalog  # noqa
        cat = EpCatalog(lambda *a: None)
        h = cat.create()
        try:
            ep, hr = cat.find(h, ep_name)
            if not ep:
                return None
            info = cat.info_of(ep)
            return info.get("libraryPath") if info.get("readyState") == "Ready" else None
        finally:
            cat.release(h)
    except Exception:
        return None


def run_case(repo: str, phase: str, ep_name: str, model_path: str, model_meta: dict,
             batch: int, case_id: str, opt_level: str = "default",
             provider_options: dict | None = None, warmup: int = 3,
             timed: int = 0, tol: dict | None = None) -> dict:
    """Run one (EP, model, batch) case with full evidence. Returns case record."""
    import numpy as np
    import onnxruntime as ort
    tol = tol or C.TOL_FP32
    led = os.path.join(repo, "reports", "ledger.json")
    case = {"id": case_id, "phase": phase, "ep": ep_name,
            "model": os.path.basename(model_path), "batch": batch,
            "opt_level": opt_level}
    try:
        reg = register_ep(ep_name, [find_winml_ep_library(ep_name)])
        if not reg.get("ok"):
            case.update({"status": "BLOCKED_DEPENDENCY", "reason": f"EP not registered: {reg}"})
            L.upsert_case(led, case)
            return case
        man_path = os.path.join(C.FIXTURES, "manifest.json")
        meta = model_meta
        feeds = C.make_feed(model_path, meta, batch)
        # CPU reference
        so_cpu = ort.SessionOptions()
        if opt_level in C.ORT_OPT_LEVELS and opt_level != "default":
            so_cpu.graph_optimization_level = C.ORT_OPT_LEVELS[opt_level]
        sess_cpu = ort.InferenceSession(model_path, sess_options=so_cpu,
                                        providers=["CPUExecutionProvider"])
        t_cpu0 = time.perf_counter()
        cpu_out = sess_cpu.run(None, feeds)
        cpu_ms = (time.perf_counter() - t_cpu0) * 1e3
        expected = {o.name: arr for o, arr in zip(sess_cpu.get_outputs(), cpu_out)}
        # GPU session
        so = ort.SessionOptions()
        so.log_severity_level = 3
        if opt_level in C.ORT_OPT_LEVELS and opt_level != "default":
            so.graph_optimization_level = C.ORT_OPT_LEVELS[opt_level]
        if provider_options:
            n_dev = add_ep_for_devices(so, ort, ep_name, provider_options)
        else:
            n_dev = add_ep_for_devices(so, ort, ep_name)
        if n_dev == 0:
            case.update({"status": "BLOCKED_DEPENDENCY",
                         "reason": f"EP {ep_name} not in get_ep_devices()"})
            L.upsert_case(led, case)
            return case
        t0 = time.perf_counter()
        so.enable_profiling = True
        sess = ort.InferenceSession(model_path, sess_options=so)
        session_create_ms = (time.perf_counter() - t0) * 1e3
        provs = list(sess.get_providers())
        out_names = [o.name for o in sess.get_outputs()]
        actual: dict = {}
        # warmup
        for _ in range(warmup):
            outs = sess.run(None, feeds)
        # timed
        times_ms: list[float] = []
        for _ in range(max(timed, 1)):
            t = time.perf_counter()
            outs = sess.run(None, feeds)
            times_ms.append((time.perf_counter() - t) * 1e3)
        actual = {n: a for n, a in zip(out_names, outs)}
        prof_file = sess.end_profiling()
        placement = C.analyze_profile(prof_file, ep_name)
        par = C.parity_check(expected, actual, **tol)
        gpu_ok = placement["gpu_nodes"] >= 1
        if par["all_ok"] and gpu_ok and ep_name in provs:
            status = "PASS"
        elif not par["all_ok"]:
            status = "FAIL_CORRECTNESS"
        else:
            status = "FAIL_NO_GPU_NODES" if ep_name in provs else "FAIL_EP_NOT_IN_PROVIDERS"
        import statistics
        if ep_name == "DmlExecutionProvider" and status == "PASS":
            status = "PASS_CONTROL_DML"  # never count as AMD/MIGraphX success
        case.update({
            "status": status,
            "get_providers": provs,
            "placement": placement,
            "parity_summary": {"all_ok": par["all_ok"],
                               "per_output": {k: v.get("status") for k, v in par["per_output"].items()}},
            "parity_detail": par,
            "gpu_node_verified": gpu_ok,
            "profile_file": os.path.basename(prof_file),
            "timing": {"session_create_ms": round(session_create_ms, 2),
                       "cpu_single_ms": round(cpu_ms, 2),
                       "gpu_mean_ms": round(statistics.mean(times_ms), 2),
                       "gpu_p50_ms": round(statistics.median(times_ms), 2),
                       "n_timed": len(times_ms)},
        })
    except Exception as e:
        case.update({"status": "FAIL", "exception": traceback.format_exc(limit=8)})
    L.upsert_case(led, case)
    return case
