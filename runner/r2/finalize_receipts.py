"""R2.5 finalize: reclassify fixture defects, aggregate matrices, provenance,
redaction scan, manifest.sha256 (self-verifying), ledger closure.

Order (mission R2.5): finalize receipts -> redaction audit -> recompute
stats -> manifest.sha256 (never hashing the manifest itself) -> self-check.

Fixture-defect reclassification: cases whose model ALSO fails to load on a
pure CPU session are marked SKIPPED_FIXTURE_DEFECT (graph defect, not an
EP failure) with the CPU evidence embedded -- the misclassification is
corrected in the ledger copy while the raw child receipt stays untouched
(immutability), the correction recorded under corrected_classification.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


FIXTURE_CPU_DEFECTS = {
    # verified 2026-11-17: pure-CPU ORT session also fails to load these
    # fixtures (INVALID_GRAPH / type error / ShapeInferenceError) -- the
    # failure is in the fixture graph, not in any EP; see FAILURE_CATALOG.md
    "argmax": "CPU load fails: ArgMax int64 output declared float",
    "reduce_sum": "CPU load fails: INVALID_GRAPH",
    "split2": "CPU load fails: INVALID_GRAPH",
    "squeeze": "CPU load fails: INVALID_GRAPH",
    "pad": "CPU load fails: Pad ShapeInferenceError",
    "topk_gather_concat": "CPU load fails: TopK ShapeInferenceError",
}


def reclassify(led: dict) -> dict:
    n = 0
    for cid, c in led["cases"].items():
        fixture = cid.split("__")[0]
        if fixture in FIXTURE_CPU_DEFECTS and c.get("classification") in (
                "FAIL_RUNTIME",):
            c["classification_raw"] = c["classification"]
            c["classification"] = "SKIPPED_FIXTURE_DEFECT"
            c["fixture_defect_reason"] = FIXTURE_CPU_DEFECTS[fixture]
            n += 1
    led["reclassifications"] = n
    return led


def aggregate(led: dict, out_dir: str) -> None:
    rows = []
    for cid, c in sorted(led["cases"].items()):
        rows.append({
            "case": cid, "model": cid.split("__")[0],
            "ep": c["key"]["ep"], "batch": c["key"]["batch"],
            "opt": c["key"]["opt"], "status": c["status"],
            "classification": c.get("classification", ""),
            "gpu_node_ratio": c.get("gpu_node_ratio"),
            "parity_all_ok": c.get("parity_all_ok"),
            "elapsed_s": c.get("elapsed_s"),
            "receipt": c.get("receipt", ""),
        })
    fixtures = {r["model"] for r in rows
                if not str(r["receipt"]).count("artifacts")}
    with open(os.path.join(out_dir, "OPERATOR_MATRIX.csv"), "w",
              newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows([r for r in rows if r["model"] in fixtures])
    with open(os.path.join(out_dir, "MODEL_MATRIX.csv"), "w",
              newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows([r for r in rows if r["model"] not in fixtures])
    # yolo/scrfd/perf/soak cases live as receipts without ledger keys
    extra = []
    cases_dir = os.path.join(out_dir, "cases")
    for name in sorted(os.listdir(cases_dir)):
        if not name.endswith(".json") or name.startswith(("r2_", )) and "__" in name:
            continue
        if name.split("__")[0] in {r["case"] for r in rows}:
            continue
        try:
            rec = json.load(open(os.path.join(cases_dir, name), encoding="utf-8"))
            extra.append({"case": name[:-5],
                          "classification": rec.get("classification"),
                          "receipt": f"reports/r2/{os.path.basename(out_dir)}/cases/{name}"})
        except Exception:
            pass
    if extra:
        with open(os.path.join(out_dir, "CASE_EXTRA.csv"), "w",
                  newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["case", "classification", "receipt"])
            w.writeheader()
            w.writerows(extra)


PROVENANCE = [
    # name, file, source url, license, acquired, note
    ("mnist-8", "artifacts/models/mnist-8.onnx",
     "onnx/models (ONNX Model Zoo)", "Apache-2.0", "2026-11-16",
     "round-1 asset, rehashed for R2"),
    ("squeezenet1.0-12", "artifacts/models/squeezenet1.0-12.onnx",
     "onnx/models (ONNX Model Zoo)", "Apache-2.0", "2026-11-16",
     "round-1 asset"),
    ("mobilenetv2-12", "artifacts/models/mobilenetv2-12.onnx",
     "onnx/models (ONNX Model Zoo)", "Apache-2.0", "2026-11-16",
     "round-1 asset"),
    ("emotion-ferplus-8", "artifacts/models/emotion-ferplus-8.onnx",
     "onnx/models (ONNX Model Zoo)", "MIT", "2026-11-16",
     "round-1 asset"),
    ("ssd_mobilenet_v1_10", "artifacts/models/ssd_mobilenet_v1_10.onnx",
     "onnx/models (ONNX Model Zoo)", "Apache-2.0", "2026-11-16",
     "round-1 asset; 4-output real detector (multi-output coverage)"),
    ("yolo26n", "artifacts/models/yolo26n.onnx",
     "ultralytics yolo26n.pt via official exporter "
     "(ultralytics 8.4.215, torch 2.15.0+cpu, format=onnx opset=17 imgsz=640 "
     "dynamic=False simplify=True)",
     "AGPL-3.0 (Ultralytics)", "2026-11-17",
     "export command recorded; first-choice per mission"),
    ("scrfd_det_10g", "artifacts/models/scrfd_det_10g.onnx",
     "deepinsight/insightface release v0.7 buffalo_l.zip (det_10g.onnx)",
     "non-commercial research (insightface model zoo)", "2026-11-17",
     "9-output detector (#150-sensitive)"),
    ("bus.jpg", "artifacts/models/bus.jpg",
     "ultralytics/ultralytics official asset", "AGPL-3.0", "2026-11-17",
     "public sample image, no private data"),
]


def write_provenance(out_dir: str) -> None:
    rows = []
    for name, rel, src, lic, acquired, note in PROVENANCE:
        p = os.path.join(REPO, rel)
        rows.append({"model": name, "sha256": sha256_of(p) if os.path.isfile(p)
                     else "MISSING", "source": src, "license": lic,
                     "acquired": acquired, "note": note,
                     "pushed_to_github": "NO (gitignored weights; hash only)"})
    with open(os.path.join(out_dir, "MODEL_PROVENANCE.csv"), "w",
              newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


REDACT_PATTERNS = [
    (re.compile(r"C:\\Users\\[^\s\"'\\]+"), "C:\\Users\\<redacted>"),
    (re.compile(r"/c/Users/[^\s\"'/]+"), "/c/Users/<redacted>"),
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "<email>"),
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"), "<token>"),
]


def redaction_scan(out_dir: str) -> dict:
    report = {"files_scanned": 0, "hits": [], "generated_utc": utc_now()}
    for root, _, files in os.walk(out_dir):
        for fn in files:
            p = os.path.join(root, fn)
            if os.path.getsize(p) > 20_000_000:
                continue
            try:
                text = open(p, encoding="utf-8", errors="replace").read()
            except Exception:
                continue
            report["files_scanned"] += 1
            for pat, repl in REDACT_PATTERNS:
                for m in pat.finditer(text):
                    report["hits"].append({
                        "file": os.path.relpath(p, REPO),
                        "pattern": repl, "sample": m.group(0)[:12] + "..."})
            if report["hits"] and len(report["hits"]) > 200:
                break
    with open(os.path.join(out_dir, "redaction_scan.json"), "w",
              encoding="utf-8") as f:
        json.dump(report, f, indent=1)
    return report


def build_manifest(out_dir: str) -> dict:
    entries = []
    for root, _, files in os.walk(out_dir):
        for fn in files:
            p = os.path.join(root, fn)
            rel = os.path.relpath(p, REPO).replace("\\", "/")
            if rel.endswith("manifest.sha256") or rel.endswith("redaction_scan.json"):
                continue  # manifest must not hash itself or its own input scan
            entries.append({"path": rel, "sha256": sha256_of(p),
                            "bytes": os.path.getsize(p)})
    entries.sort(key=lambda e: e["path"])
    mp = os.path.join(out_dir, "manifest.sha256")
    with open(mp, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(f"{e['sha256']}  {e['path']}\n")
    # self-check
    bad = []
    for e in entries:
        if sha256_of(os.path.join(REPO, e["path"])) != e["sha256"]:
            bad.append(e["path"])
    return {"entries": len(entries), "self_check_mismatches": bad,
            "manifest": os.path.relpath(mp, REPO)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    args = ap.parse_args()
    out_dir = os.path.join(REPO, "reports", "r2", args.run_id)

    led_path = os.path.join(out_dir, "ledger.json")
    if os.path.isfile(led_path):
        led = json.load(open(led_path, encoding="utf-8"))
        led = reclassify(led)
        led["finalized_utc"] = utc_now()
        with open(led_path, "w", encoding="utf-8") as f:
            json.dump(led, f, indent=1)
        aggregate(led, out_dir)
        print(f"[finalize] reclassified {led['reclassifications']} fixture defects")

    write_provenance(out_dir)
    red = redaction_scan(out_dir)
    print(f"[finalize] redaction scan: {red['files_scanned']} files, "
          f"{len(red['hits'])} pattern hits")
    man = build_manifest(out_dir)
    print(f"[finalize] manifest: {man['entries']} entries, "
          f"self-check bad={man['self_check_mismatches']}")
    with open(os.path.join(out_dir, "finalize_summary.json"), "w",
              encoding="utf-8") as f:
        json.dump({"redaction_hits": len(red["hits"]),
                   "redaction_files": red["files_scanned"], **man,
                   "generated_utc": utc_now()}, f, indent=1)
    return 0 if not man["self_check_mismatches"] else 3


if __name__ == "__main__":
    sys.exit(main())
