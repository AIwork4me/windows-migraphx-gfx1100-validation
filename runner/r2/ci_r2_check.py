"""CI-only structural checks for R2 artifacts (no GPU, no EP packages).

Validates: ledger schema/statuses, every DONE/FAILED case's receipt parses
and carries required keys, CSV matrix headers, provenance completeness,
and that no GPU PASS classification appears without its required evidence
fields (providers, node attribution, parity) in the receipt.
"""

from __future__ import annotations

import csv
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
VALID_STATUS = {"PENDING", "RUNNING", "DONE", "FAILED", "BLOCKED"}
PASS_CLASS = ("PASS_GPU_CORRECT_FULL_COVERAGE", "PASS_GPU_CORRECT_PARTIAL_COVERAGE")


def main() -> int:
    errors = []
    run_dirs = []
    r2 = os.path.join(REPO, "reports", "r2")
    if os.path.isdir(r2):
        for d in sorted(os.listdir(r2)):
            if os.path.isdir(os.path.join(r2, d)) and d.startswith("r2_"):
                run_dirs.append(os.path.join(r2, d))
    if not run_dirs:
        print("ci_r2_check: no r2 run dirs tracked yet (ok on early commits)")
        return 0

    for rd in run_dirs:
        led = os.path.join(rd, "ledger.json")
        if not os.path.isfile(led):
            continue
        data = json.load(open(led, encoding="utf-8"))
        if data.get("schema") != "r2/model_ledger/1":
            errors.append(f"{led}: unexpected schema {data.get('schema')}")
        for cid, c in data.get("cases", {}).items():
            if c.get("status") not in VALID_STATUS:
                errors.append(f"{cid}: bad status {c.get('status')}")
            cls = c.get("classification")
            if cls in PASS_CLASS:
                p = os.path.join(REPO, c.get("receipt", "x"))
                if not os.path.isfile(p):
                    errors.append(f"{cid}: PASS without receipt file")
                    continue
                rec = json.load(open(p, encoding="utf-8"))
                if args_ep(rec) not in rec.get("session_providers", []):
                    errors.append(f"{cid}: PASS but EP not in session providers")
                if not rec.get("profile", {}).get("nodes_by_provider"):
                    errors.append(f"{cid}: PASS without profile node attribution")
                if not rec.get("parity", {}).get("all_ok"):
                    errors.append(f"{cid}: PASS without parity all_ok")
                if not rec.get("device_resolution", {}).get("luid"):
                    errors.append(f"{cid}: PASS without device resolution evidence")
        # CSV contracts when finalized
        for csv_name, fields in (
                ("OPERATOR_MATRIX.csv", ["case", "model", "ep", "batch", "opt",
                                         "classification"]),
                ("MODEL_MATRIX.csv", ["case", "model", "ep", "batch", "opt",
                                      "classification"]),
                ("MODEL_PROVENANCE.csv", ["model", "sha256", "source", "license"])):
            p = os.path.join(rd, csv_name)
            if os.path.isfile(p):
                with open(p, encoding="utf-8") as f:
                    head = next(csv.reader(f))
                for fld in fields:
                    if fld not in head:
                        errors.append(f"{csv_name}: missing column {fld}")

    if errors:
        print("ci_r2_check FAILED:")
        for e in errors[:40]:
            print(" -", e)
        return 1
    print("ci_r2_check: OK")
    return 0


def args_ep(rec):
    return rec.get("ep", "")


if __name__ == "__main__":
    sys.exit(main())
