"""Deterministic unit tests for R1 helpers.

Mocks only ever simulate ORT *behaviors* (session providers list, profile
events) to verify our classification logic. No mock result here says
anything about physical hardware -- physical claims live in receipts only.
"""

from __future__ import annotations

import json
import os
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "runner"))

from r1.fail_closed import (  # noqa: E402
    GPUUnavailableError, assert_ep_executed, create_session_fail_closed,
    pick_device_by_dxgi_adapter,
)
from r1.r1_common import redact_text  # noqa: E402


class FakeSession:
    def __init__(self, providers, events):
        self._providers = providers
        self._events = events

    def get_providers(self):
        return list(self._providers)

    def end_profiling(self):
        import tempfile
        fd, p = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w") as f:
            json.dump(self._events, f)
        return p


class FakeORT:
    def __init__(self, devices):
        self._devices = devices

    def get_ep_devices(self):
        return self._devices


class FakeSessionOptions:
    def __init__(self):
        self.added = None

    def add_provider_for_devices(self, devices, opts):
        self.added = (list(devices), dict(opts))


class FakeDevice:
    def __init__(self, ep_name, metadata):
        self.ep_name = ep_name
        self.device = type("D", (), {"metadata": metadata})()


EP = "AMDGPUExecutionProvider"


def node(provider):
    return {"cat": "Node", "name": "n", "args": {"provider": provider}}


class TestFailClosed(unittest.TestCase):
    def test_session_ep_missing_raises(self):
        class O:  # minimal ort double
            SessionOptions = FakeSessionOptions

            def InferenceSession(self, path, so, providers=None):
                return FakeSession(["CPUExecutionProvider"], [])

        with self.assertRaises(GPUUnavailableError) as cm:
            create_session_fail_closed(O(), "m.onnx", EP)
        self.assertIn("silently dropped", str(cm.exception))

    def test_session_ep_present_ok(self):
        class O:
            SessionOptions = FakeSessionOptions

            def InferenceSession(self, path, so, providers=None):
                return FakeSession([EP, "CPUExecutionProvider"], [])

        s = create_session_fail_closed(O(), "m.onnx", EP)
        self.assertEqual(s.get_providers()[0], EP)

    def test_assert_ep_executed_pass(self):
        sess = FakeSession([EP], [node(EP), node("CPUExecutionProvider")])
        out = assert_ep_executed(sess, EP, lambda s: None)
        self.assertEqual(out, {"ep_nodes": 1, "total_nodes": 2,
                               "profile_file": out["profile_file"]})

    def test_assert_ep_executed_cpu_fallback_raises(self):
        sess = FakeSession([EP], [node("CPUExecutionProvider")])
        with self.assertRaises(GPUUnavailableError):
            assert_ep_executed(sess, EP, lambda s: None)

    def test_pick_device_by_dxgi_adapter(self):
        devs = [FakeDevice(EP, {"DxgiAdapterNumber": "1"}),
                FakeDevice(EP, {"DxgiAdapterNumber": "0", "Discrete": "1"})]
        d = pick_device_by_dxgi_adapter(FakeORT(devs), EP, "0")
        self.assertEqual(d.device.metadata["Discrete"], "1")
        with self.assertRaises(GPUUnavailableError):
            pick_device_by_dxgi_adapter(FakeORT(devs), EP, "9")

    def test_pick_device_tolerates_non_dict_metadata(self):
        devs = [FakeDevice(EP, "not-a-dict"),
                FakeDevice(EP, {"DxgiAdapterNumber": 0})]
        d = pick_device_by_dxgi_adapter(FakeORT(devs), EP, "0")
        self.assertIsNotNone(d)

    def test_pick_device_adapter_number_is_required(self):
        # R2.0 hardening: adapter_number must stay an explicit argument.
        # A default of "0" would turn this host-specific workaround into a
        # portable-looking policy (Dxgi index != HIP ordinal != PCI BDF).
        devs = [FakeDevice(EP, {"DxgiAdapterNumber": "0"})]
        with self.assertRaises(TypeError):
            pick_device_by_dxgi_adapter(FakeORT(devs), EP)

    def test_pick_device_duplicate_adapter_numbers_fails_closed(self):
        # Two candidates advertising the same DxgiAdapterNumber: picking
        # either one silently is a guess; the helper must not pretend the
        # mapping is unique. (Full identity-based resolution lives in
        # runner/r2/device_resolver.py; this guards the R1 entry point.)
        devs = [FakeDevice(EP, {"DxgiAdapterNumber": "0"}),
                FakeDevice(EP, {"DxgiAdapterNumber": "0"})]
        with self.assertRaises(GPUUnavailableError):
            pick_device_by_dxgi_adapter(FakeORT(devs), EP, "0")


class TestRedaction(unittest.TestCase):
    def test_userhome_redacted(self):
        s = "crash in C:\\Users\\Rocm\\AppData\\Local\\Temp\\x"
        self.assertNotIn("Rocm", redact_text(s))

    def test_guid_redacted(self):
        s = "id=12345678-1234-1234-1234-123456789ABC"
        self.assertNotIn("12345678-1234", redact_text(s))


if __name__ == "__main__":
    unittest.main()
