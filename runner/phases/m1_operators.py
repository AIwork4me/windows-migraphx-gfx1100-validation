"""M1: broad operator coverage on available EPs (placement + parity + fallback map)."""
from __future__ import annotations
import argparse, os, sys, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
import common as C
import ledger as L
from ep_runner import run_case, register_ep, find_winml_ep_library

EPS = ["AMDGPUExecutionProvider", "MIGraphXExecutionProvider", "DmlExecutionProvider"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=C.REPO)
    ap.add_argument("--batches", default="1,2")
    args = ap.parse_args()
    repo = os.path.abspath(args.repo)
    man = C.write_fixtures()
    models = sorted(man.keys())
    batches = [int(b) for b in args.batches.split(",")]
    receipt = C.new_receipt("m1", "m1_operators_run")
    receipt["cases"] = []
    for ep in EPS:
        reg = register_ep(ep, [find_winml_ep_library(ep)])
        for m in models:
            for b in batches:
                cid = f"m1/{ep}/{m}/b{b}"
                case = run_case(repo, "m1", ep, os.path.join(C.FIXTURES, m + ".onnx"),
                                man[m], b, cid, warmup=2, timed=1)
                receipt["cases"].append({k: case.get(k) for k in
                                         ("id", "status", "placement", "parity_summary", "gpu_node_verified")})
                print(cid, case.get("status"), flush=True)
    C.save_receipt("m1", receipt)
    print("M1_OPERATORS_DONE")


if __name__ == "__main__":
    rc = main()
    sys.stdout.flush(); sys.stderr.flush()
    os._exit(rc if isinstance(rc, int) else 0)  # hard exit: skip interpreter shutdown (WinML/ORT leave non-daemon threads)
