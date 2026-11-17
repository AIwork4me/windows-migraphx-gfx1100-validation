#!/usr/bin/env python3
"""Regenerate VALID_FIXTURE_MATRIX.csv + matrix.json from existing receipts.

Used when the on-metal matrix already ran (24 child receipts on disk) and
only the aggregation needs re-doing. Never re-runs GPU cases.
"""
from __future__ import annotations

import csv
import glob
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main() -> int:
    out_dir = sys.argv[1]
    cases = []
    for rcpt in sorted(glob.glob(os.path.join(out_dir, "*.json"))):
        base = os.path.basename(rcpt)
        if base in ("matrix.json",):
            continue
        rec = json.load(open(rcpt, encoding="utf-8"))
        cases.append(rec)
    with open(os.path.join(out_dir, "VALID_FIXTURE_MATRIX.csv"), "w",
              encoding="utf-8", newline="\n") as f:
        w = csv.writer(f)
        w.writerow(["fixture", "ep", "opt", "classification", "child_rc",
                    "wall_s", "ep_nodes", "cpu_nodes", "parity_all_ok",
                    "session_providers", "device_luid"])
        for c in sorted(cases, key=lambda x: (x["fixture"], x["ep"], x["opt"])):
            nbp = c.get("nodes_by_provider", {}) or {}
            par = None
            pv = c.get("parity")
            if isinstance(pv, list) and pv and all(
                    isinstance(i, dict) and isinstance(i.get("outputs"), dict)
                    for i in pv):
                par = all(all(o.get("match") is True
                              for o in i["outputs"].values()) for i in pv)
            w.writerow([c["fixture"], c["ep"], c["opt"], c["classification"],
                        c.get("child_rc"), c.get("wall_s"),
                        nbp.get(c["ep"], ""), nbp.get("CPUExecutionProvider", ""),
                        par, "|".join(c.get("session_providers", [])),
                        c.get("device_luid", "")])
    with open(os.path.join(out_dir, "matrix.json"), "w",
              encoding="utf-8", newline="\n") as f:
        json.dump({"schema": "r2_1_1/gpu_matrix_driver/1", "cases":
                   sorted(cases, key=lambda x: (x["fixture"], x["ep"], x["opt"]))},
                  f, indent=1, default=str)
    n_pass = sum(1 for c in cases if str(c["classification"]).startswith("PASS"))
    print(f"regen done: {n_pass}/{len(cases)} PASS_GPU_*")
    return 0


if __name__ == "__main__":
    sys.exit(main())
