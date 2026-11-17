"""R2.3 driver: run perf and/or soak children for GPU-correct models only.

Model selection: reads the R2.2 ledger; a model is eligible per EP only if
at least one of its cases is PASS_GPU_CORRECT_* for that EP (mission rule:
never measure CPU-fallback latency as GPU speed). Representative set:
one light synthetic (matmul b4), YOLO26n, mobilenetv2, plus SCRFD when
present -- kept explicit so the receipt set stays small.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PY_WINML = os.path.join(REPO, "venv-winml", "Scripts", "python.exe")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--soak", action="store_true")
    ap.add_argument("--max-minutes", type=float, default=120)
    ap.add_argument("--soak-minutes", type=float, default=15)
    ap.add_argument("--only", default=None,
                    help="restrict to models whose name contains this")
    args = ap.parse_args()

    out_dir = os.path.join(REPO, "reports", "r2", args.run_id)
    led = json.load(open(os.path.join(out_dir, "ledger.json"), encoding="utf-8"))
    passed = {}  # (model_name, ep) -> True  for GPU-correct models only
    for cid, c in led["cases"].items():
        if str(c.get("classification", "")).startswith("PASS_GPU_CORRECT"):
            model = cid.split("__")[0]
            passed[(model, c["key"]["ep"])] = True

    targets = []  # (model_path, ep)
    reps = [
        ("matmul", os.path.join(REPO, "fixtures", "matmul.onnx")),
        ("yolo26n", os.path.join(REPO, "artifacts", "models", "yolo26n.onnx")),
        ("mobilenetv2-12", os.path.join(REPO, "artifacts", "models",
                                        "mobilenetv2-12.onnx")),
        ("scrfd_det_10g", os.path.join(REPO, "artifacts", "models",
                                       "scrfd_det_10g.onnx")),
        ("squeezenet1.0-12", os.path.join(REPO, "artifacts", "models",
                                          "squeezenet1.0-12.onnx")),
    ]
    for name, path in reps:
        if not os.path.isfile(path):
            continue
        if args.only and args.only not in name:
            continue
        for ep in ("AMDGPUExecutionProvider", "MIGraphXExecutionProvider"):
            if passed.get((name, ep)):
                targets.append((path, ep, name))

    print(f"[{'soak' if args.soak else 'perf'}] eligible targets: "
          f"{[(n, e.replace('ExecutionProvider', '')) for _, e, n in targets]}")
    rows = []
    for path, ep, name in targets:
        child = "soak_child.py" if args.soak else "perf_child.py"
        tag = f"{name}__{ep.replace('ExecutionProvider', '')}"
        out_json = os.path.join(out_dir, "cases",
                                ("soak_" if args.soak else "perf_") + tag + ".json")
        cmd = [PY_WINML, "-I", os.path.join(REPO, "runner", "r2", child),
               "--model", path, "--ep", ep, "--out", out_json]
        if "scrfd" in name:
            cmd += ["--input-size", "640"]
        if args.soak:
            cmd += ["--minutes", str(args.soak_minutes)]
        print(f"[{'soak' if args.soak else 'perf'}] {tag} ...", flush=True)
        with open(out_json.replace(".json", ".stdout.log"), "wb") as fo, \
             open(out_json.replace(".json", ".stderr.log"), "wb") as fe:
            try:
                p = subprocess.run(cmd, stdout=fo, stderr=fe, cwd=REPO,
                                   timeout=(args.soak_minutes * 60 + 300
                                            if args.soak else 1800))
                rc = p.returncode
            except subprocess.TimeoutExpired:
                rc = "TIMEOUT"
        try:
            rec = json.load(open(out_json, encoding="utf-8"))
            cls = rec.get("classification", "NO_RECEIPT")
        except Exception:
            cls, rec = "NO_RECEIPT", {}
        print(f"[{'soak' if args.soak else 'perf'}] {tag} -> {cls} rc={rc}")
        rows.append({"case": tag, "classification": cls,
                     "rc": rc, "receipt": os.path.relpath(out_json, REPO)})

    summary_path = os.path.join(out_dir, "SOAK.csv" if args.soak else "PERFORMANCE.csv")
    import csv
    with open(summary_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["case", "classification", "rc", "receipt"])
        w.writeheader()
        w.writerows(rows)
    print(f"[{'soak' if args.soak else 'perf'}] summary -> {summary_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
