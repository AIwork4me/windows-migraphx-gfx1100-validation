"""R2.1 fail-closed device resolver (application-facing, mockable).

Supersedes runner/r1/fail_closed.py::pick_device_by_dxgi_adapter (a
host-specific workaround that must not become a portable default).

Identity model -- four DIFFERENT namespaces that must never be conflated:

  DXGI adapter index   OS display enumeration order; changes when adapters
                       appear/disappear (RDP connect, virtual displays)
  DXGI LUID            locally unique per adapter instance per boot
  PCI BDF (bus:dev:fn) physical hardware location (PnP LocationInfo)
  HIP ordinal          index into hipGetDeviceCount from THIS runtime DLL

On this host (evidence reports/r2/<run_id>/device_identity/identity_map.json)
the RX 7900 XTX is the ONLY AMD entry with DXCore ``Discrete=1`` AND an
active DXGI output; indirect-display mirrors duplicate vendor:device/VRAM
but expose neither. HIP's PCI attributes on Windows return placeholders
(NOT_EXPOSED), so identity is established by the conjunction:

    Discrete=1 (DXCore)  AND  active DXGI output  AND  canary PASS
    (EP in session providers + >=1 profile node on the EP + CPU parity)

Resolution algorithm (fail-closed at every step):

  1. enumerate candidates for the requested EP (AMD vendor only)
  2. join each candidate to the DXGI table by LUID
  3. attach physical-evidence bits (discrete, has_active_output)
  4. if the caller requested an explicit identity (luid / dxgi / pnp pci),
     narrow to it and REQUIRE the join to succeed
  5. survivors must have positive physical evidence unless a request
     pinned exactly one candidate AND the canary proves it executes
  6. run the injected canary per surviving candidate (isolated process in
     production); a candidate that cannot prove execution is dropped --
     never silently used
  7. exactly one proven candidate -> DeviceResolution
     >1 -> AmbiguousGpuError (with the evidence table)
     0 -> GpuUnavailableError (with actionable reasons)

The resolver itself never talks to ORT/DXGI/HIP directly: everything comes
through the injected ``identity_view`` and ``canary_fn`` so unit tests can
simulate any host (single dGPU, iGPU+dGPU, two dGPUs, mirrors, permuted
orders, missing metadata) without hardware.
"""

from __future__ import annotations

from dataclasses import dataclass, field


class GpuUnavailableError(RuntimeError):
    """No candidate could be proven to be the requested physical GPU."""


class AmbiguousGpuError(GpuUnavailableError):
    """Multiple candidates pass all evidence; user must choose explicitly."""


@dataclass
class Candidate:
    ep_name: str
    luid: int | None
    dxgi_adapter_number: str | None
    description: str | None
    vendor_id: int | str | None
    device_id: int | str | None
    discrete: bool | None          # DXCore Discrete metadata ('1' -> True)
    dxgi_has_active_output: bool | None
    dxgi_flags: int | None
    dxgi_index: int | None         # informational ONLY (never a selector)
    extra_metadata: dict = field(default_factory=dict)


@dataclass
class DeviceResolution:
    candidate: Candidate
    basis: list[str]
    canary: dict

    @property
    def ort_device_hint(self) -> dict:
        """What to match against ort.get_ep_devices() in the real process."""
        return {"ep_name": self.candidate.ep_name,
                "LUID": str(self.candidate.luid) if self.candidate.luid is not None else None,
                "DxgiAdapterNumber": self.candidate.dxgi_adapter_number}


def _to_bool(v) -> bool | None:
    if v is None:
        return None
    return str(v) == "1" or v is True


def build_candidates(identity_view: dict, ep_name: str,
                     vendor_id: str = "1002") -> list[Candidate]:
    """Join ep-device metadata with the validated DXGI table by LUID."""
    dxgi_by_luid = {}
    for a in identity_view.get("dxgi_adapters") or []:
        if str(a.get("vendor_id")) == vendor_id:
            dxgi_by_luid[int(a["luid_key"])] = a

    out = []
    for d in identity_view.get("ep_devices") or []:
        if d.get("ep_name") != ep_name:
            continue
        md = d.get("metadata") or {}
        luid = int(md["LUID"]) if str(md.get("LUID", "")).isdigit() else None
        a = dxgi_by_luid.get(luid) if luid is not None else None
        out.append(Candidate(
            ep_name=ep_name,
            luid=luid,
            dxgi_adapter_number=md.get("DxgiAdapterNumber"),
            description=a.get("description") if a else md.get("Description"),
            vendor_id=(a or {}).get("vendor_id") or d.get("vendor_id"),
            device_id=(a or {}).get("device_id") or d.get("device_id"),
            discrete=_to_bool(md.get("Discrete")),
            dxgi_has_active_output=(
                any(o.get("attached_to_desktop") for o in a.get("outputs", []))
                if a else None),
            dxgi_flags=(a or {}).get("flags"),
            dxgi_index=(a or {}).get("dxgi_index"),
            extra_metadata={k: v for k, v in md.items()
                            if k not in ("LUID", "DxgiAdapterNumber", "Discrete",
                                         "Description")},
        ))
    return out


def resolve_device(identity_view: dict, ep_name: str, canary_fn,
                   *, requested: dict | None = None) -> DeviceResolution:
    """Fail-closed resolution. See module docstring for the algorithm.

    canary_fn(candidate) -> dict with at least {'passed': bool, ...}; it
    must create an isolated session FOR THIS CANDIDATE and prove:
    EP in providers, >=1 profile node on the EP, CPU parity. The resolver
    does not trust any weaker signal.
    """
    requested = requested or {}
    cands = build_candidates(identity_view, ep_name)
    reasons: list[str] = []

    if not cands:
        raise GpuUnavailableError(
            f"no ep-device candidates for {ep_name}; available ep_devices: "
            f"{[d.get('ep_name') for d in identity_view.get('ep_devices') or []]}")

    # step 4: explicit identity narrowing
    if requested.get("luid") is not None:
        want = int(requested["luid"])
        cands = [c for c in cands if c.luid == want]
        if not cands:
            raise GpuUnavailableError(
                f"requested LUID {want} not present among {ep_name} candidates "
                f"{[c.luid for c in build_candidates(identity_view, ep_name)]}")
        reasons.append(f"narrowed by explicit LUID {want}")
    if requested.get("dxgi") is not None:
        # explicit DXGI index is accepted ONLY as an override, and the
        # joined LUID must exist (we do not treat the index as identity)
        want = str(requested["dxgi"])
        cands = [c for c in cands if c.dxgi_adapter_number == want]
        if not cands:
            raise GpuUnavailableError(
                f"requested DxgiAdapterNumber {want} not present; candidates: "
                f"{[c.dxgi_adapter_number for c in build_candidates(identity_view, ep_name)]}")
        reasons.append(f"narrowed by explicit DxgiAdapterNumber {want} "
                       "(host-specific override)")
    if requested.get("pci_bdf") is not None:
        # Windows HIP does not expose BDF and ORT metadata has none either;
        # an explicit PCI request can only be honored when a PnP join is
        # supplied in the identity_view (pnp_bdf_by_luid).
        join = identity_view.get("pnp_bdf_by_luid") or {}
        want = tuple(requested["pci_bdf"])
        cands = [c for c in cands
                 if c.luid is not None and tuple(join.get(str(c.luid), ())) == want]
        if not cands:
            raise GpuUnavailableError(
                f"requested PCI BDF {want} cannot be joined to any candidate "
                f"(join table: {join}); HIP PCI attrs NOT_EXPOSED on Windows")

    # step 5: positive physical evidence (unless exactly one pinned candidate)
    if len(cands) > 1:
        evidence_ok = [c for c in cands
                       if c.discrete and c.dxgi_has_active_output is True]
        if len(evidence_ok) == 1:
            cands = evidence_ok
            reasons.append("unique candidate with Discrete=1 + active DXGI output")
        elif len(evidence_ok) > 1:
            # multiple physically-proven candidates: need explicit choice
            raise AmbiguousGpuError(
                f"{len(evidence_ok)} candidates carry Discrete+active-output "
                f"evidence (LUIDs {[c.luid for c in evidence_ok]}); specify "
                f"requested={{'luid': ...}} explicitly")
        else:
            raise GpuUnavailableError(
                "no candidate carries both Discrete=1 and an active DXGI "
                f"output (candidates: {[(c.luid, c.discrete, c.dxgi_has_active_output) for c in cands]}); "
                "indirect-display mirrors are not selectable as the physical GPU")

    # step 6: canary -- the candidate must PROVE execution
    c = cands[0]
    canary = canary_fn(c)
    if not canary.get("passed"):
        raise GpuUnavailableError(
            f"candidate LUID {c.luid} failed the GPU canary: {canary}; "
            "refusing to return it (would be a silent CPU fallback)")
    basis = reasons + [
        f"ep={c.ep_name}",
        f"description={c.description}",
        f"discrete={c.discrete}",
        f"active_output={c.dxgi_has_active_output}",
        "canary: EP in providers + profile node + CPU parity",
    ]
    return DeviceResolution(candidate=c, basis=basis, canary=canary)
