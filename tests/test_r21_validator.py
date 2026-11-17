"""Adversarial tests for runner/r2_1/r21_validate.py (R2.1-A4).

Every mutation below represents a receipt-forgery / false-positive pattern;
the validator MUST reject each one (verdict FAIL / errors). Genuine R2
receipt shapes and a valid fused single-node profile MUST validate.
Run: python -m unittest tests.test_r21_validator -v
"""
import copy
import json
import os
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "runner", "r2_1"))
import r21_validate as V  # noqa: E402

EP = "AMDGPUExecutionProvider"


def valid_receipt():
    """Minimal genuine-shape receipt modeled on reports/r2 case receipts."""
    return {
        "schema_version": 1,
        "case_id": "abs__AMDGPU__b1__ALL",
        "ep": EP,
        "model_sha256": "5b3a98a8ed3d58057caaa18121bacde3f927c079238af1c1c2723ac52768e02a",
        "session_providers": [EP, "CPUExecutionProvider"],
        "input_names": ["X"],
        "input_shapes": {"X": [1, 8]},
        "output_names": ["Y"],
        "tolerance": {"atol": 1e-5, "rtol": 1e-5},
        "parity": {
            "outputs_expected": ["Y"], "outputs_actual": ["Y"],
            "names_match": True,
            "per_output": {"Y": {"all_ok": True, "max_abs": 1.2e-06}},
            "all_ok": True,
        },
        "device_resolution": {
            "luid": 72346, "dxgi_adapter_number": "0", "discrete": True,
            "basis": ["dxcore_discrete", "active_output"],
            "canary": {"passed": True, "parity_max_abs": 0.0},
        },
        "profile": {
            "file": "onnxruntime_profile.json",
            "nodes_by_provider": {EP: 12},
            "ep_node_ops": {f"{EP}_1_0": 12},
            "total_node_events": 12,
        },
    }


class NegativeFixturesMustFail(unittest.TestCase):
    def _expect_fail(self, rec, why):
        verdict, errs = V.validate_receipt(rec, expected_ep=EP)
        self.assertEqual(verdict, "FAIL", f"{why}: validator must FAIL, got {verdict} ({errs})")
        self.assertTrue(errs, f"{why}: errors list must be non-empty")

    def test_cpu_only_receipt_rejected(self):
        """The exact R2 false-positive: EP in session_providers, CPU-only nodes."""
        r = valid_receipt()
        r["profile"]["nodes_by_provider"] = {"CPUExecutionProvider": 5587}
        self._expect_fail(r, "CPU-only receipt")

    def test_ep_present_zero_ep_nodes_rejected(self):
        r = valid_receipt()
        r["profile"]["nodes_by_provider"] = {EP: 0, "CPUExecutionProvider": 100}
        self._expect_fail(r, "zero EP nodes")

    def test_missing_declared_output_parity_rejected(self):
        r = valid_receipt()
        r["output_names"] = ["Y", "Z"]  # Z declared but no parity entry
        self._expect_fail(r, "missing output parity")

    def test_parity_contradiction_rejected(self):
        """all_ok=True but per-output False -> forged aggregate."""
        r = valid_receipt()
        r["parity"]["per_output"]["Y"]["all_ok"] = False
        self._expect_fail(r, "parity contradiction")

    def test_output_name_mismatch_rejected(self):
        r = valid_receipt()
        r["parity"]["outputs_actual"] = ["W"]
        self._expect_fail(r, "output name mismatch")

    def test_names_match_false_rejected(self):
        r = valid_receipt()
        r["parity"]["names_match"] = False
        self._expect_fail(r, "names_match False")

    def test_wrong_luid_rejected(self):
        for bad in (0, -5, "72346", None, 72346.0):
            r = valid_receipt()
            r["device_resolution"]["luid"] = bad
            self._expect_fail(r, f"bad luid {bad!r}")

    def test_canary_not_passed_rejected(self):
        r = valid_receipt()
        r["device_resolution"]["canary"]["passed"] = False
        self._expect_fail(r, "canary not passed")

    def test_poisoned_provider_map_rejected(self):
        r = valid_receipt()
        r["profile"]["nodes_by_provider"] = {EP: "12"}  # string count
        self._expect_fail(r, "string node count")
        r = valid_receipt()
        r["profile"]["nodes_by_provider"] = {EP: float("nan")}
        self._expect_fail(r, "NaN node count")
        r = valid_receipt()
        r["profile"]["nodes_by_provider"] = {EP: True}  # bool is not int evidence
        self._expect_fail(r, "bool node count")
        r = valid_receipt()
        r["profile"]["nodes_by_provider"] = {None: 12}
        self._expect_fail(r, "None provider key")

    def test_missing_model_sha_rejected(self):
        r = valid_receipt()
        r["model_sha256"] = "deadbeef"
        self._expect_fail(r, "short model sha")
        del r["model_sha256"]
        self._expect_fail(r, "missing model sha")

    def test_nan_parity_max_abs_rejected(self):
        r = valid_receipt()
        r["parity"]["per_output"]["Y"]["max_abs"] = float("nan")
        self._expect_fail(r, "NaN parity")
        r = valid_receipt()
        r["parity"]["per_output"]["Y"]["max_abs"] = float("inf")
        self._expect_fail(r, "inf parity")

    def test_ep_missing_from_session_providers_rejected(self):
        r = valid_receipt()
        r["session_providers"] = ["CPUExecutionProvider"]
        self._expect_fail(r, "EP dropped from session")

    def test_missing_tolerance_rejected(self):
        r = valid_receipt()
        del r["tolerance"]["rtol"]
        self._expect_fail(r, "missing rtol")


class PositiveFixturesMustPass(unittest.TestCase):
    def test_valid_fused_single_node_full_coverage(self):
        r = valid_receipt()
        r["profile"]["nodes_by_provider"] = {EP: 1}  # one fused node
        r["profile"]["ep_node_ops"] = {f"{EP}_1_0": 1}
        r["profile"]["total_node_events"] = 1
        verdict, errs = V.validate_receipt(r, expected_ep=EP)
        self.assertEqual(verdict, "PASS_GPU_EXECUTED_FULL")
        self.assertEqual(errs, [])

    def test_gpu_executed_with_cpu_nodes_is_partial_not_fail(self):
        r = valid_receipt()
        r["profile"]["nodes_by_provider"] = {EP: 9, "CPUExecutionProvider": 2}
        verdict, errs = V.validate_receipt(r, expected_ep=EP)
        self.assertEqual(verdict, "PASS_GPU_EXECUTED")
        self.assertTrue(any("cpu_nodes" in e for e in errs))

    def test_genuine_r2_receipt_validates(self):
        p = os.path.join(REPO, "reports", "r2", "r2_20261117T004504Z",
                         "cases", "abs__AMDGPU__b1__ALL.json")
        if not os.path.isfile(p):
            self.skipTest("R2 receipts not tracked in this checkout")
        rec = json.load(open(p, encoding="utf-8"))
        verdict, errs = V.validate_receipt(rec, expected_ep=EP)
        self.assertIn(verdict, ("PASS_GPU_EXECUTED", "PASS_GPU_EXECUTED_FULL"),
                      f"genuine receipt rejected: {errs}")


class LedgerMatrixAndManifest(unittest.TestCase):
    def _mk_run_dir(self, ledger, matrix_rows, manifest_lines):
        tmp = tempfile.mkdtemp(prefix="r21_val_")
        with open(os.path.join(tmp, "ledger.json"), "w", encoding="utf-8") as f:
            json.dump(ledger, f)
        if matrix_rows is not None:
            import csv as _csv
            with open(os.path.join(tmp, "OPERATOR_MATRIX.csv"), "w",
                      encoding="utf-8", newline="") as f:
                w = _csv.writer(f)
                w.writerow(["case", "model", "ep", "batch", "opt", "classification"])
                w.writerows(matrix_rows)
        if manifest_lines is not None:
            with open(os.path.join(tmp, "manifest.sha256"), "w", encoding="utf-8") as f:
                f.write("\n".join(manifest_lines) + "\n")
        return tmp

    def test_duplicate_case_ids_rejected(self):
        led = {"schema": "x", "cases": {"a__AMDGPU__b1__ALL": {"status": "DONE",
               "classification": "SKIPPED_FIXTURE_DEFECT"}}}
        tmp = self._mk_run_dir(led, [["a__AMDGPU__b1__ALL", "a", "AMDGPUExecutionProvider",
                                      1, "ALL", "SKIPPED_FIXTURE_DEFECT"],
                                     ["a__AMDGPU__b1__ALL", "a", "AMDGPUExecutionProvider",
                                      1, "ALL", "SKIPPED_FIXTURE_DEFECT"]], None)
        errs = V.validate_ledger_and_matrix(tmp)
        self.assertTrue(any("duplicate" in e for e in errs), errs)

    def test_orphan_and_missing_matrix_rows_rejected(self):
        led = {"schema": "x", "cases": {"a": {"status": "DONE", "classification": "PASS"},
                                        "b": {"status": "DONE", "classification": "PASS"}}}
        tmp = self._mk_run_dir(led, [["a", "m", "e", 1, "ALL", "PASS"]], None)  # b missing, none orphan
        errs = V.validate_ledger_and_matrix(tmp)
        self.assertTrue(any("missing" in e for e in errs), errs)

    def test_falsified_manifest_rejected(self):
        import hashlib
        # self-contained manifest root: entries are relative to the temp
        # base (CI checkouts live on a different drive than TEMP)
        base = tempfile.mkdtemp(prefix="r21_man_base_")
        sub = os.path.join(base, "ev")
        os.makedirs(sub)
        target = os.path.join(sub, "dummy.json")
        with open(target, "w") as f:
            f.write("{}")
        rel = "ev/dummy.json"
        wrong = "0" * 64
        with open(os.path.join(sub, "manifest.sha256"), "w") as f:
            f.write(f"{wrong}  {rel}\n")
        errs = V.verify_manifest(sub, base=base)
        self.assertTrue(any("mismatch" in e for e in errs), errs)
        good = hashlib.sha256(open(target, "rb").read()).hexdigest()
        with open(os.path.join(sub, "manifest.sha256"), "w") as f:
            f.write(f"{good}  {rel}\n")
        errs = V.verify_manifest(sub, base=base)
        self.assertEqual(errs, [])


if __name__ == "__main__":
    unittest.main()
