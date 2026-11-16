"""M1: multi-output adversarial correctness (upstream #150 focus).

- two_out_same_shape: outputs differ by known offsets (+1/+10) -> swapped
  outputs must be caught by BY-NAME comparison.
- nine_out: 9 outputs, offsets +1..+9 (#150 reproducer shape).
- two_out_diff_shape, split2: name/shape routing.
- Optimization levels: ORT_DISABLE_ALL / ORT_ENABLE_BASIC / default.
Dumps expected/actual JSON for any mismatch (open-source synthetic only).
"""
from __future__ import annotations
import argparse, os, sys, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
import common as C
from ep_runner import run_case, register_ep, find_winml_ep_library

EPS = ["AMDGPUExecutionProvider", "MIGraphXExecutionProvider", "DmlExecutionProvider"]
MODELS = ["two_out_same_shape", "nine_out", "two_out_diff_shape", "split2"]
LEVELS = ["default", "ORT_DISABLE_ALL", "ORT_ENABLE_BASIC"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=C.REPO)
    args = ap.parse_args()
    repo = os.path.abspath(args.repo)
    man = C.write_fixtures()
    receipt = C.new_receipt("m1", "m1_multi_output_run")
    receipt["cases"] = []
    mismatch_dir = os.path.join(repo, "reports", "m1", "mismatch")
    os.makedirs(mismatch_dir, exist_ok=True)
    for ep in EPS:
        register_ep(ep, [find_winml_ep_library(ep)])
        for m in MODELS:
            for lvl in LEVELS:
                for b in (1, 4):
                    cid = f"m1-mo/{ep}/{m}/{lvl}/b{b}"
                    case = run_case(repo, "m1", ep, os.path.join(C.FIXTURES, m + ".onnx"),
                                    man[m], b, cid, opt_level=lvl, warmup=1, timed=1)
                    pd = case.get("parity_detail") or {}
                    if not pd.get("all_ok", False):
                        safe = cid.replace("/", "_")
                        C.atomic_write_json(os.path.join(mismatch_dir, safe + ".json"),
                                            {"case": {k: v for k, v in case.items() if k != "parity_detail"},
                                             "parity": pd})
                    receipt["cases"].append({k: case.get(k) for k in
                                             ("id", "status", "opt_level", "parity_summary")})
                    print(cid, case.get("status"), flush=True)
    C.save_receipt("m1", receipt)
    print("M1_MULTIOUT_DONE")


if __name__ == "__main__":
    rc = main()
    sys.stdout.flush(); sys.stderr.flush()
    os._exit(rc if isinstance(rc, int) else 0)  # hard exit: skip interpreter shutdown (WinML/ORT leave non-daemon threads)
