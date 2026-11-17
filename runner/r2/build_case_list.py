"""Build the R2.2 case list (fixtures + opt levels + real models)."""

from __future__ import annotations

import argparse
import glob
import json
import os

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EPS = ("AMDGPUExecutionProvider", "MIGraphXExecutionProvider")
MULTI_OUTPUT = ("two_out_same_shape", "two_out_diff_shape", "nine_out", "split2")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    cases = []
    for f in sorted(glob.glob(os.path.join(REPO, "fixtures", "*.onnx"))):
        name = os.path.splitext(os.path.basename(f))[0]
        for ep in EPS:
            for b in (1, 2, 4):
                cases.append({"model": f, "ep": ep, "batch": b, "opt": "ALL",
                              "family": "synthetic", "timeout_s": 240})
            if name in MULTI_OUTPUT:
                for b in (1, 2, 4):
                    for opt in ("DISABLE_ALL", "BASIC", "EXTENDED"):
                        cases.append({"model": f, "ep": ep, "batch": b,
                                      "opt": opt, "family": "synthetic",
                                      "timeout_s": 240})
    for f in sorted(glob.glob(os.path.join(REPO, "artifacts", "models", "*.onnx"))):
        name = os.path.splitext(os.path.basename(f))[0]
        for ep in EPS:
            cases.append({"model": f, "ep": ep, "batch": 1, "opt": "ALL",
                          "family": "real", "timeout_s": 900,
                          "input_size": 640 if "scrfd" in name else
                          (300 if "ssd" in name else None)})
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(cases, f, indent=1)
    print(f"[cases] {len(cases)} cases -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
