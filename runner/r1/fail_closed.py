"""R1.3.6 fail-closed session helper for application developers.

ONNX Runtime 1.30.x plugin-EP sessions can silently end up CPU-only
(#151-class behavior; see docs/ISSUE_151_SCOPE_AND_LIMITATIONS.md). This
helper refuses to hand back a session unless the requested EP is actually
in get_providers() and (optionally) actually executed at least one node
according to the ORT profile. GPU absence becomes a catchable
GPUUnavailableError instead of a quiet 100x slowdown.

Design rule: never raises ORT's own exceptions for the "EP got dropped"
case -- that case is not an error ORT reports, so we must detect it.
"""

from __future__ import annotations


class GPUUnavailableError(RuntimeError):
    """The requested GPU EP is not usable in this session/process."""


def create_session_fail_closed(ort, model_path: str, ep_name: str,
                               *, device=None,
                               providers_fallback: bool = False):
    """Create an InferenceSession or raise GPUUnavailableError.

    ort            the onnxruntime module (injected for testability)
    model_path     ONNX file
    ep_name        e.g. 'AMDGPUExecutionProvider'
    device         optional OrtEpDevice to select via add_provider_for_devices
                   (recommended: the device whose DxgiAdapterNumber == 0)
    providers_fallback  if False (default), do NOT append CPUExecutionProvider
                   to the classic providers list; a dropped EP then fails
                   loudly at session creation instead of producing a CPU session
    """
    so = ort.SessionOptions()
    so.enable_profiling = True
    if device is not None:
        so.add_provider_for_devices([device], {})
        sess = ort.InferenceSession(model_path, so)
    else:
        provs = [ep_name] + (["CPUExecutionProvider"] if providers_fallback else [])
        sess = ort.InferenceSession(model_path, so, providers=provs)

    got = sess.get_providers()
    if ep_name not in got:
        raise GPUUnavailableError(
            f"{ep_name} requested but session providers are {got}; "
            f"ONNX Runtime silently dropped the EP (issue #151 class). "
            f"Check that the EP library is registered and the device "
            f"selection is supported.")
    return sess


def assert_ep_executed(sess, ep_name: str, run_fn) -> dict:
    """Run once via run_fn(sess), read the profile, prove >=1 node ran on ep_name.

    Raises GPUUnavailableError when the session is a silent CPU fallback
    (EP present in get_providers() but zero profile nodes attributed to it),
    or returns {'ep_nodes': n, 'total_nodes': m, 'profile_file': path}.
    """
    run_fn(sess)
    prof = sess.end_profiling()
    import json
    with open(prof, encoding="utf-8") as f:
        ev = json.load(f)
    events = ev if isinstance(ev, list) else ev.get("traceEvents", [])
    nodes = [e for e in events if e.get("cat") == "Node"]
    ep_nodes = [n for n in nodes
                if (n.get("args") or {}).get("provider") == ep_name]
    if not ep_nodes:
        raise GPUUnavailableError(
            f"{ep_name} is in get_providers() but executed 0 of {len(nodes)} "
            f"nodes (silent CPU fallback).")
    return {"ep_nodes": len(ep_nodes), "total_nodes": len(nodes),
            "profile_file": prof}


def pick_device_by_dxgi_adapter(ort, ep_name: str, adapter_number: str):
    """Pick the ep device whose metadata DxgiAdapterNumber == adapter_number.

    HOST-SPECIFIC WORKAROUND / EXPLICIT EXPERIMENT ENTRY -- not a portable
    default policy. adapter_number is deliberately required (no default):
    "DxgiAdapterNumber=0" is only known to map to the physical RX 7900 XTX
    on THIS host's current adapter enumeration. Discrete=1 + Dxgi=0 does
    NOT prove the entry maps to HIP ordinal 0 on any other host; DXGI
    index, LUID, PCI BDF and HIP ordinal are different namespaces (see
    R2 docs/R2_DEVICE_IDENTITY.md and runner/r2/device_resolver.py for the
    fail-closed identity-based selection that supersedes this helper).

    Rationale (R1 root cause): amdgpu-ep uses DxgiAdapterNumber as the HIP
    device ordinal; on hosts where the AMD GPU is not DXGI adapter 0 the
    other entry fails with HIP 101 'invalid device ordinal'.
    Raises GPUUnavailableError when no such device exists.
    """
    matches = []
    for d in ort.get_ep_devices():
        if d.ep_name != ep_name:
            continue
        try:
            md = dict(getattr(d.device, "metadata", {}) or {})
        except Exception:
            md = {}
        if str(md.get("DxgiAdapterNumber", "")) == adapter_number:
            matches.append(d)
    if len(matches) > 1:
        # Duplicate DxgiAdapterNumber across ep devices: choosing either
        # one silently would be a guess (DXGI index is not a unique
        # physical identity). Fail closed; see R2 device resolver.
        raise GPUUnavailableError(
            f"{len(matches)} {ep_name} devices advertise DxgiAdapterNumber="
            f"{adapter_number!r}; physical identity ambiguous")
    if matches:
        return matches[0]
    raise GPUUnavailableError(
        f"no {ep_name} device with DxgiAdapterNumber={adapter_number!r}; "
        f"available: {[d.ep_name for d in ort.get_ep_devices()]}")
