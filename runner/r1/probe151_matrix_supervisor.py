"""R1.3 supervisor: run the full #151 differential matrix, both EPs.

Fresh subprocess per cell; stdout and stderr captured SEPARATELY (round-1
m5 used DEVNULL and lost the ORT 'EP Error ... Falling back' block, which
prints on stdout -- that omission is being corrected here).
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from r1.r1_common import atomic_write_json, finish_receipt, new_receipt, redact_json  # noqa: E402

CELLS = ["A", "B", "C0", "C1", "D", "E", "F1", "F2", "F3"]
EPS = ["AMDGPUExecutionProvider", "MIGraphXExecutionProvider"]
VENV_PY = os.path.join(REPO, "venv-winml", "Scripts", "python.exe")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--timeout", type=int, default=180)
    args = ap.parse_args()

    outdir = os.path.join(REPO, "reports", "r1", args.run_id)
    os.makedirs(outdir, exist_ok=True)
    child = os.path.join(REPO, "runner", "r1", "probe151_matrix_child.py")
    r = new_receipt("r1_151_matrix_supervisor", args.run_id, sys.argv)
    rows = []

    for ep in EPS:
        tag = ep.replace("ExecutionProvider", "")
        for cell in CELLS:
            out = os.path.join(outdir, f"probe151_{tag}_{cell}.json")
            logb = os.path.join(outdir, f"probe151_{tag}_{cell}")
            try:
                pr = subprocess.run([VENV_PY, child, "--cell", cell, "--ep", ep,
                                     "--out", out, "--run-id", args.run_id],
                                    capture_output=True, text=True,
                                    timeout=args.timeout, cwd=REPO)
                rc, so, se = pr.returncode, pr.stdout, pr.stderr
            except subprocess.TimeoutExpired as e:
                rc = -9
                so = (e.stdout or b"").decode("utf-8", "replace")
                se = f"TIMEOUT after {args.timeout}s"
            with open(logb + ".stdout.log", "w", encoding="utf-8") as f:
                f.write(so or "")
            with open(logb + ".stderr.log", "w", encoding="utf-8") as f:
                f.write(se or "")
            outcome, extra = "RECEIPT_MISSING", {}
            if os.path.isfile(out):
                try:
                    d = json.load(open(out, encoding="utf-8"))
                    outcome = d.get("outcome", "?")
                    extra = {"providers": d.get("session_get_providers"),
                             "seconds": d.get("session_create_seconds")}
                except Exception:
                    outcome = "RECEIPT_UNREADABLE"
            ep_error_in_stdout = "EP Error" in (so or "")
            fallback_msg = "Falling back" in (so or "")
            rows.append({"ep": ep, "cell": cell, "exit_code": rc,
                         "outcome": outcome,
                         "stdout_ep_error_block": ep_error_in_stdout,
                         "stdout_falling_back": fallback_msg,
                         "stderr_has_userwarning": "UserWarning" in (se or ""),
                         **{k: v for k, v in extra.items() if v is not None}})
            print(f"{tag} {cell}: {outcome} exit={rc} "
                  f"epErrBlock={ep_error_in_stdout} userWarn={'UserWarning' in (se or '')}")

    with open(os.path.join(outdir, "fallback_behavior_matrix.csv"), "w",
              newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    r["matrix"] = rows
    finish_receipt(r, 0, "MATRIX_DONE")
    atomic_write_json(os.path.join(outdir, "probe151_matrix_supervisor.json"),
                      redact_json(r))
    print("matrix done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
