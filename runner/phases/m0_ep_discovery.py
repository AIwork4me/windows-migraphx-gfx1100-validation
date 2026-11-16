"""M0: EP discovery, registration, minimal end-to-end real GPU compute.

Real-machine test. Subprocess-per-EP; every case produces a JSON receipt with
get_providers(), profile node attribution (gpu_nodes/total_nodes) and by-name
CPU parity. No PASS without a substantive GPU compute node.

Catalog bridge uses the official C API (WinMLEpCatalog.h) from the
Microsoft.Windows.AI.MachineLearning 2.5.96 NuGet (runtimes/win-x64/native).
The EP download via WinMLEpEnsureReady is the officially authorized AMD EP
download for this validation.
"""
from __future__ import annotations
import argparse, ctypes, json, os, sys, time, traceback, shutil

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import common as C
import ledger as L

NUGET_DIR = r"C:\AIwork4me\nuget\mlwp256\runtimes\win-x64\native"
CATALOG_DLL = os.path.join(NUGET_DIR, "Microsoft.Windows.AI.MachineLearning.dll")

READY_STATE = {0: "Ready", 1: "NotReady", 2: "NotPresent"}
CERT = {0: "Unknown", 1: "Certified", 2: "Uncertified"}


# ------------------------------------------------------------- catalog bridge
class EpCatalog:
    def __init__(self, log):
        os.add_dll_directory(NUGET_DIR)
        self.dll = ctypes.WinDLL(CATALOG_DLL)
        self.log = log
        d = self.dll
        # manual HRESULT handling (restype c_long, never auto-raise)
        d.WinMLEpCatalogCreate.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
        d.WinMLEpCatalogCreate.restype = ctypes.c_long
        d.WinMLEpCatalogRelease.argtypes = [ctypes.c_void_p]
        d.WinMLEpCatalogRelease.restype = None
        d.WinMLEpCatalogEnumProviders.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        d.WinMLEpCatalogEnumProviders.restype = ctypes.c_long
        d.WinMLEpCatalogFindProvider.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p,
                                                 ctypes.POINTER(ctypes.c_void_p)]
        d.WinMLEpCatalogFindProvider.restype = ctypes.c_long
        for fn in ("WinMLEpGetName", "WinMLEpGetVersion", "WinMLEpGetLibraryPath",
                   "WinMLEpGetPackageFamilyName", "WinMLEpGetPackageRootPath"):
            getattr(d, fn).argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_char_p,
                                       ctypes.POINTER(ctypes.c_size_t)]
            getattr(d, fn).restype = ctypes.c_long
        for fn in ("WinMLEpGetNameSize", "WinMLEpGetVersionSize", "WinMLEpGetLibraryPathSize",
                   "WinMLEpGetPackageFamilyNameSize", "WinMLEpGetPackageRootPathSize"):
            getattr(d, fn).argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t)]
            getattr(d, fn).restype = ctypes.c_long
        d.WinMLEpGetReadyState.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
        d.WinMLEpGetReadyState.restype = ctypes.c_long
        d.WinMLEpEnsureReady.argtypes = [ctypes.c_void_p]
        d.WinMLEpEnsureReady.restype = ctypes.c_long

    def create(self):
        h = ctypes.c_void_p()
        hr = self.dll.WinMLEpCatalogCreate(ctypes.byref(h))
        if hr != 0:
            raise OSError(hr, f"WinMLEpCatalogCreate hr=0x{hr & 0xFFFFFFFF:08X}")
        return h

    def _gs(self, fn_size, fn_get, ep, tag):
        n = ctypes.c_size_t()
        hr = fn_size(ep, ctypes.byref(n))
        if hr != 0:
            return f"<err 0x{hr & 0xFFFFFFFF:08X} {tag}size>"
        # some getters need extra headroom; retry with growing buffers
        size = max(n.value, 4)
        for _ in range(5):
            buf = ctypes.create_string_buffer(size)
            used = ctypes.c_size_t()
            hr = fn_get(ep, size, buf, ctypes.byref(used))
            if hr == 0:
                return buf.value.decode("utf-8", "replace")
            if (hr & 0xFFFFFFFF) == 0x8007007A:  # ERROR_INSUFFICIENT_BUFFER
                size = size * 2 + 16
                continue
            return f"<err 0x{hr & 0xFFFFFFFF:08X} {tag}get>"
        return f"<err buffer-giveup {tag}>"

    def info_of(self, ep) -> dict:
        d = self.dll
        st = ctypes.c_int()
        d.WinMLEpGetReadyState(ep, ctypes.byref(st))
        return {
            "name": self._gs(d.WinMLEpGetNameSize, d.WinMLEpGetName, ep, "name"),
            "version": self._gs(d.WinMLEpGetVersionSize, d.WinMLEpGetVersion, ep, "ver"),
            "packageFamilyName": self._gs(d.WinMLEpGetPackageFamilyNameSize, d.WinMLEpGetPackageFamilyName, ep, "pfn"),
            "libraryPath": self._gs(d.WinMLEpGetLibraryPathSize, d.WinMLEpGetLibraryPath, ep, "lib"),
            "packageRootPath": self._gs(d.WinMLEpGetPackageRootPathSize, d.WinMLEpGetPackageRootPath, ep, "root"),
            "readyState": READY_STATE.get(st.value, st.value),
        }

    def find(self, catalog, name: str):
        ep = ctypes.c_void_p()
        hr = self.dll.WinMLEpCatalogFindProvider(catalog, name.encode(), None, ctypes.byref(ep))
        if hr != 0:
            return None, hr
        return ep, 0

    def ensure_ready(self, ep):
        hr = self.dll.WinMLEpEnsureReady(ep)
        return hr

    def release(self, catalog):
        self.dll.WinMLEpCatalogRelease(catalog)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=os.path.join(C.REPO))
    ap.add_argument("--ep", choices=["AMDGPUExecutionProvider", "MIGraphXExecutionProvider"], required=True)
    ap.add_argument("--models", default="identity_add,matmul,conv_relu,softmax,reshape_transpose,two_out_same_shape,nine_out")
    ap.add_argument("--batches", default="1,2,4")
    ap.add_argument("--download", choices=["yes", "no"], default="yes",
                    help="WinMLEpEnsureReady = official EP download (authorized)")
    ap.add_argument("--ensure-attempts", type=int, default=2)
    args = ap.parse_args()
    C.REPO = os.path.abspath(args.repo)
    globals()["LEDGER"] = os.path.join(C.REPO, "reports", "ledger.json")

    phase = "m0"
    ep_name = args.ep
    log_lines: list[str] = []

    def log(msg):
        line = f"[{C.now_pair()['utc']}] {msg}"
        print(line, flush=True)
        log_lines.append(line)

    receipt = C.new_receipt(phase, f"m0_{ep_name}_run")
    receipt.update({"ep": ep_name, "download": args.download,
                    "ort_pkg": "onnxruntime-windowsml", "catalog_nuget": "2.5.96"})

    # ---------------- Step A: catalog enumeration (no download) ----------------
    catalog_state = {"attempted": True}
    try:
        cat = EpCatalog(log)
        h = cat.create()
        catalog_state["catalog_created"] = True
        # enumerate all providers without downloading
        found = []
        @ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
        def on_ep(ep, info, ctx):
            try:
                found.append(cat.info_of(ep))
            except Exception as e:
                found.append({"err": repr(e)})
            return True
        hr = cat.dll.WinMLEpCatalogEnumProviders(h, on_ep, None)
        catalog_state["enum_hr"] = f"0x{hr & 0xFFFFFFFF:08X}"
        catalog_state["providers"] = found
        # find our target
        ep_h, hr = cat.find(h, ep_name)
        if ep_h:
            catalog_state["target"] = cat.info_of(ep_h)
            # ---------------- Step B: ensure ready (authorized download) -------
            if catalog_state["target"]["readyState"] != "Ready" and args.download == "yes":
                for attempt in range(1, args.ensure_attempts + 1):
                    log(f"WinMLEpEnsureReady attempt {attempt} for {ep_name}")
                    t0 = time.time()
                    hr2 = cat.ensure_ready(ep_h)
                    catalog_state.setdefault("ensure_ready", []).append({
                        "attempt": attempt, "hr": f"0x{hr2 & 0xFFFFFFFF:08X}",
                        "seconds": round(time.time() - t0, 1)})
                    catalog_state["target"] = cat.info_of(ep_h)
                    if hr2 == 0 and catalog_state["target"]["readyState"] == "Ready":
                        break
            ep_h2, _ = cat.find(h, ep_name)
            if ep_h2:
                catalog_state["final"] = cat.info_of(ep_h2)
        else:
            catalog_state["find_hr"] = f"0x{hr & 0xFFFFFFFF:08X}"
        cat.release(h)
    except Exception as e:
        catalog_state["exception"] = traceback.format_exc()
        log(f"catalog bridge error: {e!r}")
    receipt["catalog"] = catalog_state

    lib_path = (catalog_state.get("final") or {}).get("libraryPath") or ""
    if not lib_path or not os.path.exists(lib_path):
        receipt["outcome"] = "EP_LIBRARY_NOT_AVAILABLE"
        receipt["log"] = log_lines
        C.save_receipt(phase, receipt)
        L.upsert_case(os.path.join(C.REPO, "reports", "ledger.json"),
                      {"id": f"m0/{ep_name}/catalog", "phase": phase,
                       "status": "BLOCKED_DEPENDENCY",
                       "reason": f"libraryPath not obtainable: {catalog_state.get('find_hr') or catalog_state.get('target')}",
                       "evidence": f"reports/{phase}/m0_{ep_name}_run.json"})
        print("M0_PHASE_DONE: EP_LIBRARY_NOT_AVAILABLE", flush=True)
        return 0

    # ---------------- Step C: register with ORT, run graphs ----------------
    import numpy as np
    import onnxruntime as ort
    log(f"register_execution_provider_library({ep_name!r}, {lib_path!r})")
    receipt["register_call"] = [ep_name, lib_path]
    reg_err = None
    try:
        ort.register_execution_provider_library(ep_name, lib_path)
    except Exception as e1:
        try:
            ort.register_execution_provider_library(lib_path, ep_name)
        except Exception as e2:
            reg_err = f"both arg orders failed: {e1!r} / {e2!r}"
    if reg_err:
        receipt["register_error"] = reg_err
        receipt["outcome"] = "REGISTER_FAILED"
        receipt["log"] = log_lines
        C.save_receipt(phase, receipt)
        print("M0_PHASE_DONE: REGISTER_FAILED", flush=True)
        import sys as _s, os as _o
        _s.stdout.flush(); _o._exit(1)
    devices = []
    for d in ort.get_ep_devices():
        try:
            devices.append(d.ep_name)
        except Exception:
            devices.append("?")
    receipt["ep_devices_after_register"] = devices
    log(f"ep devices: {devices}")

    man = C.write_fixtures()
    models = args.models.split(",")
    batches = [int(b) for b in args.batches.split(",")]

    prof_dir = os.path.join(C.REPO, "reports", phase, "profiles")
    os.makedirs(prof_dir, exist_ok=True)
    os.chdir(prof_dir)

    # CPU baseline (same ORT build, CPUExecutionProvider) per model+batch
    cpu_cache: dict[tuple, dict] = {}
    case_results = []
    for m in models:
        meta = man[m]
        path = os.path.join(C.FIXTURES, m + ".onnx")
        for b in batches:
            cid = f"m0/{ep_name}/{m}/b{b}"
            case = {"id": cid, "phase": phase, "ep": ep_name, "model": m, "batch": b,
                    "fixture_sha256": meta["sha256"]}
            try:
                feeds = C.make_feed(m, meta, b)
                # CPU reference
                if (m, b) not in cpu_cache:
                    so_cpu = ort.SessionOptions()
                    sess_cpu = ort.InferenceSession(path, sess_options=so_cpu,
                                                    providers=["CPUExecutionProvider"])
                    cpu_out = sess_cpu.run(None, feeds)
                    cpu_cache[(m, b)] = {name: arr for name, arr in
                                         zip([o.name for o in sess_cpu.get_outputs()], cpu_out)}
                expected = cpu_cache[(m, b)]
                # GPU session via plugin device API
                so = ort.SessionOptions()
                so.log_severity_level = 3
                from ep_runner import add_ep_for_devices
                try:
                    n_dev = add_ep_for_devices(so, ort, ep_name)
                    add_prov_err = None
                except Exception as e:
                    n_dev = 0
                    add_prov_err = repr(e)
                so.enable_profiling = True
                if n_dev == 0:
                    raise RuntimeError(f"EP {ep_name} not in get_ep_devices(): {add_prov_err}")
                sess = ort.InferenceSession(path, sess_options=so)
                provs = list(sess.get_providers())
                out_objs = sess.run(None, feeds)
                actual = {name: arr for name, arr in
                          zip([o.name for o in sess.get_outputs()], out_objs)}
                prof_file = sess.end_profiling()
                placement = C.analyze_profile(prof_file, ep_name)
                par = C.parity_check(expected, actual, **C.TOL_FP32)
                gpu_ok = placement["gpu_nodes"] >= 1
                status = "PASS" if (par["all_ok"] and gpu_ok and ep_name in provs) else "FAIL"
                if not par["all_ok"]:
                    status = "FAIL_CORRECTNESS"
                case.update({
                    "status": status,
                    "get_providers": provs,
                    "add_provider_error": add_prov_err,
                    "placement": placement,
                    "parity": par,
                    "gpu_node_verified": gpu_ok,
                    "profile_file": os.path.basename(prof_file),
                    "outputs": par["outputs_expected"],
                })
                log(f"{cid}: {status} gpu_nodes={placement['gpu_nodes']}/{placement['total_node_events']} "
                    f"parity_ok={par['all_ok']}")
            except Exception as e:
                case.update({"status": "FAIL", "exception": traceback.format_exc(limit=6)})
                log(f"{cid}: EXC {e!r}")
            case_results.append(case)
            L.upsert_case(os.path.join(C.REPO, "reports", "ledger.json"), case)

    receipt["cases"] = [{k: v for k, v in c.items() if k != "parity"} for c in case_results]
    receipt["outcome"] = "RUN_COMPLETE"
    receipt["log"] = log_lines
    C.save_receipt(phase, receipt)
    print("M0_PHASE_DONE", flush=True)
    return 0


if __name__ == "__main__":
    rc = main()
    sys.stdout.flush(); sys.stderr.flush()
    os._exit(rc if isinstance(rc, int) else 0)  # hard exit: skip interpreter shutdown (WinML/ORT leave non-daemon threads)
