"""Deterministic unit tests for the R2 fail-closed device resolver.

Mocks simulate HOST SHAPES (identity views), not hardware results. No mock
result here says anything about the physical machine; physical claims live
in receipts only. These tests verify the resolver's *policy*: never guess,
never fall back silently, never conflate DXGI index / LUID / PCI / HIP
ordinal.
"""

from __future__ import annotations

import os
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "runner"))

from r2.device_resolver import (  # noqa: E402
    AmbiguousGpuError, Candidate, DeviceResolution, GpuUnavailableError,
    build_candidates, resolve_device,
)

EP = "AMDGPUExecutionProvider"
PASS_CANARY = {"passed": True, "ep_nodes": 3, "total_nodes": 5,
               "parity_max_abs": 0.0}
FAIL_CANARY = {"passed": False, "reason": "HIP 101 invalid device ordinal"}
FALLBACK_CANARY = {"passed": False, "reason": "EP missing from session "
                    "providers (silent CPU fallback)"}


def dxgi_adapter(idx, luid, outputs=1, active=True, vendor="1002",
                 device="744c", flags=0):
    outs = [{"device_name": f"\\\\.\\DISPLAY{idx + 1}",
             "attached_to_desktop": active} for _ in range(outputs)]
    return {"dxgi_index": idx, "luid_key": luid, "vendor_id": vendor,
            "device_id": device, "description": "AMD Radeon RX 7900 XTX",
            "dedicated_video_memory_bytes": 25753026560, "flags": flags,
            "outputs": outs}


def ep_dev(luid, dxgi, discrete=None, ep=EP):
    md = {"DxgiAdapterNumber": str(dxgi), "LUID": str(luid)}
    if discrete is not None:
        md["Discrete"] = str(discrete)
    return {"ep_name": ep, "vendor_id": "1002", "metadata": md}


def view(dxgi_list, ep_list):
    return {"dxgi_adapters": dxgi_list, "ep_devices": ep_list}


class TestBuildCandidates(unittest.TestCase):
    def test_luid_join_attaches_dxgi_evidence(self):
        v = view([dxgi_adapter(0, 72346), dxgi_adapter(1, 118045, 0, False)],
                 [ep_dev(72346, 0, 1), ep_dev(118045, 1)])
        cands = build_candidates(v, EP)
        self.assertEqual(len(cands), 2)
        by_luid = {c.luid: c for c in cands}
        self.assertTrue(by_luid[72346].discrete)
        self.assertTrue(by_luid[72346].dxgi_has_active_output)
        self.assertIsNone(by_luid[118045].discrete)
        self.assertFalse(by_luid[118045].dxgi_has_active_output)

    def test_luid_not_in_dxgi_leaves_join_none(self):
        v = view([], [ep_dev(555, 9, 1)])
        c = build_candidates(v, EP)
        self.assertEqual(len(c), 1)
        self.assertIsNone(c[0].dxgi_index)
        self.assertIsNone(c[0].dxgi_has_active_output)

    def test_ignores_other_eps_and_vendors(self):
        v = view([], [ep_dev(1, 0, 1, ep="MIGraphXExecutionProvider")])
        self.assertEqual(build_candidates(v, EP), [])


class TestResolveSingleGpuHost(unittest.TestCase):
    """Host shape: one dGPU (possibly with indirect mirrors)."""

    MIRROR_HOST = view(
        [dxgi_adapter(0, 72346), dxgi_adapter(1, 118045, 0, False),
         dxgi_adapter(2, 80099, 0, False, vendor="8086", device="a780")],
        [ep_dev(72346, 0, 1), ep_dev(118045, 1)])

    def test_resolves_physical_by_evidence_not_order(self):
        res = resolve_device(self.MIRROR_HOST, EP, lambda c: PASS_CANARY)
        self.assertEqual(res.candidate.luid, 72346)
        self.assertIn("Discrete=1", " ".join(res.basis))

    def test_dxgi_order_permutation_still_resolves(self):
        # same host, DXGI enumeration order flipped (adapter re-enumeration)
        v = view(
            [dxgi_adapter(0, 118045, 0, False), dxgi_adapter(1, 72346),
             dxgi_adapter(2, 80099, 0, False, vendor="8086", device="a780")],
            [ep_dev(118045, 0), ep_dev(72346, 1, 1)])
        res = resolve_device(v, EP, lambda c: PASS_CANARY)
        self.assertEqual(res.candidate.luid, 72346)

    def test_canary_failure_fails_closed(self):
        with self.assertRaises(GpuUnavailableError) as cm:
            resolve_device(self.MIRROR_HOST, EP, lambda c: FAIL_CANARY)
        self.assertIn("HIP 101", str(cm.exception))

    def test_silent_cpu_fallback_canary_fails_closed(self):
        with self.assertRaises(GpuUnavailableError) as cm:
            resolve_device(self.MIRROR_HOST, EP, lambda c: FALLBACK_CANARY)
        self.assertIn("silent CPU fallback", str(cm.exception))

    def test_canary_selects_the_right_candidate(self):
        seen = []

        def canary(c):
            seen.append(c.luid)
            return PASS_CANARY if c.luid == 72346 else FAIL_CANARY

        res = resolve_device(self.MIRROR_HOST, EP, canary)
        self.assertEqual(res.candidate.luid, 72346)

    def test_explicit_luid_narrowing(self):
        res = resolve_device(self.MIRROR_HOST, EP, lambda c: PASS_CANARY,
                             requested={"luid": 72346})
        self.assertEqual(res.candidate.luid, 72346)
        with self.assertRaises(GpuUnavailableError):
            resolve_device(self.MIRROR_HOST, EP, lambda c: PASS_CANARY,
                           requested={"luid": 424242})

    def test_explicit_dxgi_override_pins_mirror_then_canary_gates_it(self):
        with self.assertRaises(GpuUnavailableError):
            resolve_device(self.MIRROR_HOST, EP, lambda c: FAIL_CANARY,
                           requested={"dxgi": 1})

    def test_pci_bdf_request_needs_pnp_join(self):
        # without a join table the PCI request fails closed (NOT_EXPOSED)
        with self.assertRaises(GpuUnavailableError):
            resolve_device(self.MIRROR_HOST, EP, lambda c: PASS_CANARY,
                           requested={"pci_bdf": (0, 3, 0)})
        v = dict(self.MIRROR_HOST)
        v["pnp_bdf_by_luid"] = {"72346": (0, 3, 0)}
        res = resolve_device(v, EP, lambda c: PASS_CANARY,
                             requested={"pci_bdf": (0, 3, 0)})
        self.assertEqual(res.candidate.luid, 72346)


class TestResolveNoPhysicalEvidence(unittest.TestCase):
    def test_missing_metadata_all_mirrors(self):
        v = view([dxgi_adapter(0, 1, 0, False), dxgi_adapter(1, 2, 0, False)],
                 [ep_dev(1, 0), ep_dev(2, 1)])
        with self.assertRaises(GpuUnavailableError) as cm:
            resolve_device(v, EP, lambda c: PASS_CANARY)
        self.assertIn("indirect-display mirrors", str(cm.exception))

    def test_no_candidates(self):
        with self.assertRaises(GpuUnavailableError):
            resolve_device(view([], []), EP, lambda c: PASS_CANARY)

    def test_single_candidate_without_evidence_still_needs_canary(self):
        # exactly one candidate, no Discrete metadata, no DXGI join:
        # resolver does not crash but the canary must still prove execution
        v = view([], [ep_dev(555, 7)])
        res = resolve_device(v, EP, lambda c: PASS_CANARY)
        self.assertEqual(res.candidate.luid, 555)
        with self.assertRaises(GpuUnavailableError):
            resolve_device(v, EP, lambda c: FAIL_CANARY)


class TestResolveMultiGpuHosts(unittest.TestCase):
    def test_two_real_amd_gpus_ambiguous_without_choice(self):
        v = view(
            [dxgi_adapter(0, 100, outputs=1, active=True),
             dxgi_adapter(1, 200, outputs=1, active=True)],
            [ep_dev(100, 0, 1), ep_dev(200, 1, 1)])
        with self.assertRaises(AmbiguousGpuError) as cm:
            resolve_device(v, EP, lambda c: PASS_CANARY)
        self.assertIn("explicitly", str(cm.exception))
        # explicit choice resolves
        res = resolve_device(v, EP, lambda c: PASS_CANARY,
                             requested={"luid": 200})
        self.assertEqual(res.candidate.luid, 200)

    def test_igpu_plus_dgpu_discrete_disambiguates(self):
        # iGPU (Discrete=0, has output) + dGPU (Discrete=1, no output
        # attached): physical-evidence conjunction fails -> explicit choice
        v = view(
            [dxgi_adapter(0, 111, outputs=1, active=True, device="a78k"),
             dxgi_adapter(1, 222, outputs=0, active=False)],
            [ep_dev(111, 0, 0), ep_dev(222, 1, 1)])
        with self.assertRaises(GpuUnavailableError):
            resolve_device(v, EP, lambda c: PASS_CANARY)
        res = resolve_device(v, EP, lambda c: PASS_CANARY,
                             requested={"luid": 222})
        self.assertEqual(res.candidate.luid, 222)

    def test_duplicate_luids_fail_closed(self):
        v = view([dxgi_adapter(0, 72346)],
                 [ep_dev(72346, 0, 1), ep_dev(72346, 0, 1)])
        with self.assertRaises(GpuUnavailableError):
            resolve_device(v, EP, lambda c: PASS_CANARY)

    def test_hip_device_removed_between_views(self):
        # 2 candidates yesterday, device gone today: canary is the gate
        v = view(
            [dxgi_adapter(0, 1), dxgi_adapter(1, 2, 0, False)],
            [ep_dev(1, 0, 1), ep_dev(2, 1)])
        with self.assertRaises(GpuUnavailableError):
            resolve_device(v, EP, lambda c: FAIL_CANARY)


class TestNamespacesNeverConflated(unittest.TestCase):
    def test_candidate_carries_all_ids_separately(self):
        v = view([dxgi_adapter(3, 999)],
                 [ep_dev(999, 3, 1)])
        c = build_candidates(v, EP)[0]
        self.assertEqual(c.dxgi_index, 3)        # DXGI namespace
        self.assertEqual(c.luid, 999)            # LUID namespace
        self.assertIsNone(c.extra_metadata.get("hip_ordinal"))
        self.assertNotEqual(c.dxgi_index, c.luid)  # trivial but explicit


if __name__ == "__main__":
    unittest.main()
