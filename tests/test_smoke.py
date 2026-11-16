"""Smoke tests: run with  venv-winml\Scripts\python.exe -m pytest tests\ -q
(or plain python -m unittest). Verifies fixture integrity and CPU parity
machinery; GPU cases live in runner/phases (evidence-grade, receipted).
"""
import os, sys, unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(REPO, "runner"))
import common as C  # noqa: E402


class Fixtures(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.man = C.write_fixtures()

    def test_fixture_count(self):
        self.assertGreaterEqual(len(self.man), 30)

    def test_manifest_sha256_stable(self):
        p = os.path.join(C.FIXTURES, "identity_add.onnx")
        self.assertEqual(C.sha256_file(p), self.man["identity_add"]["sha256"])

    def test_parity_by_name_detects_swap(self):
        import numpy as np
        exp = {"A": np.full(4, 2.0, np.float32), "B": np.full(4, 11.0, np.float32)}
        act = {"A": np.full(4, 11.0, np.float32), "B": np.full(4, 2.0, np.float32)}
        r = C.parity_check(exp, act, atol=1e-3, rtol=1e-3)
        self.assertFalse(r["all_ok"])  # swapped outputs MUST fail by name

    def test_parity_ok_case(self):
        import numpy as np
        exp = {"A": np.full(4, 2.0, np.float32)}
        r = C.parity_check(exp, {"A": np.full(4, 2.0, np.float32)}, atol=1e-3, rtol=1e-3)
        self.assertTrue(r["all_ok"])

    def test_cpu_session_runs_fixture(self):
        import onnxruntime as ort
        man = self.man
        feeds = C.make_feed("identity_add", man["identity_add"], 2)
        sess = ort.InferenceSession(os.path.join(C.FIXTURES, "identity_add.onnx"),
                                    providers=["CPUExecutionProvider"])
        out = sess.run(None, feeds)
        self.assertAlmostEqual(float(out[0][0, 0]),
                               float(feeds["X"][0, 0] + 0.25), places=4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
