#!/usr/bin/env python3
"""A3: derive R2_RECOUNT.json + projection CSVs from frozen R2 ledger/receipts.

Read-only w.r.t. reports/r2/r2_20261117T004504Z; writes only to R2.1 run dir.
"""
import csv
import hashlib
import io
import json
import os
import sys

REPO = r"C:\AIwork4me\windows-migraphx-gfx1100-validation"
R2 = os.path.join(REPO, "reports", "r2", "r2_20261117T004504Z")
REAL_MODELS = {"emotion-ferplus-8", "mnist-8", "mobilenetv2-12", "squeezenet1.0-12",
               "ssd_mobilenet_v1_10", "scrfd_det_10g", "yolo26n"}


def main():
    out_dir = sys.argv[1]
    ledger = json.load(open(os.path.join(R2, "ledger.json"), encoding="utf-8"))
    cases = ledger["cases"]

    # ---- receipt existence + identity cross-check ----
    missing, mismatched = [], []
    rows_op, rows_model = [], []
    for cid, c in sorted(cases.items()):
        model = cid.split("__")[0]
        real = model in REAL_MODELS
        # receipt path (ledger stores windows rel path)
        rp = os.path.join(REPO, c["receipt"].replace("\\", os.sep))
        if not os.path.exists(rp):
            missing.append(cid)
            continue
        rec = json.load(open(rp, encoding="utf-8"))
        # identity: receipt ep/model sha must match ledger key
        k = c["key"]
        rec_ep = rec.get("ep")
        rec_sha = rec.get("model_sha256")
        rec_cid = rec.get("case_id")
        if rec_ep is not None and rec_ep != k["ep"]:
            mismatched.append((cid, "ep", rec_ep, k["ep"]))
        if rec_sha is not None and rec_sha != k["model_sha"]:
            mismatched.append((cid, "model_sha", rec_sha[:12], k["model_sha"][:12]))
        if rec_cid is not None and rec_cid != cid:
            # canonical transform: ledger short form <-> receipt full form
            # r2_<model>.onnx_<EPEXEC>_b<N>_<OPT>  ==  <model>__<epalias>__b<N>__<OPT>
            canon = rec_cid.removeprefix("r2_").replace(".onnx_", "_")
            canon = canon.replace("AMDGPUExecutionProvider", "AMDGPU")
            canon = canon.replace("MIGraphXExecutionProvider", "MIGraphX")
            canon = canon.replace("_b", "__b").replace("__b", "__b", 1)
            # rebuild: model__EP__bN__OPT  (EP already embedded)
            parts = canon.split("_")
            # safer: direct regex-free rebuild
            import re as _re
            m2 = _re.match(r"^(?P<model>.+?)_(?P<ep>AMDGPU|MIGraphX)__b(?P<b>\d+)_(?P<opt>.+)$", canon)
            if not m2:
                mismatched.append((cid, "case_id_unparseable", rec_cid, cid))
            else:
                norm = f"{m2.group('model')}__{m2.group('ep')}__b{m2.group('b')}__{m2.group('opt')}"
                if norm != cid:
                    mismatched.append((cid, "case_id_normalized", norm, cid))
        case_id_format_note = ("ledger case ids use short EP alias (abs__AMDGPU__b1__ALL); "
                               "receipt case_id uses full form (r2_abs.onnx_AMDGPUExecutionProvider_b1_ALL); "
                               "compared after canonical normalization")
        row = {
            "case": cid, "model": model, "ep": k["ep"], "batch": k["batch"],
            "opt": k["opt"], "real_model": real,
            "ledger_status": c["status"],
            "ledger_classification": c["classification"],
            "gpu_node_ratio": c.get("gpu_node_ratio"),
            "parity_all_ok": c.get("parity_all_ok"),
            "elapsed_s": c.get("elapsed_s"),
            "receipt": c["receipt"].replace("\\", "/"),
        }
        rows_op.append(row)
        if real:
            rows_model.append(row)

    # ---- counts ----
    total = len(cases)
    cls = {}
    for c in cases.values():
        cls[c["classification"]] = cls.get(c["classification"], 0) + 1
    synthetic = [c for c in cases.values() if c["key"].get("model") not in REAL_MODELS
                 and c["case"].split("__")[0] not in REAL_MODELS] if False else None
    # classify by case-id prefix (ledger key has no model name field beyond receipt)
    def is_real(cid):
        return cid.split("__")[0] in REAL_MODELS

    real_ids = [cid for cid in cases if is_real(cid)]
    syn_ids = [cid for cid in cases if not is_real(cid)]

    def stat(ids):
        s = {}
        for cid in ids:
            cl = cases[cid]["classification"]
            s[cl] = s.get(cl, 0) + 1
        return s

    per_ep = {}
    for cid, c in cases.items():
        ep = c["key"]["ep"]
        per_ep.setdefault(ep, {}).setdefault(c["classification"], 0)
        per_ep[ep][c["classification"]] += 1

    recount = {
        "source": {
            "ledger": "reports/r2/r2_20261117T004504Z/ledger.json",
            "ledger_sha256": hashlib.sha256(
                open(os.path.join(R2, "ledger.json"), "rb").read()).hexdigest(),
            "run_id": ledger.get("run_id"),
            "finalized_utc": ledger.get("finalized_utc"),
        },
        "total": total,
        "by_classification": cls,
        "synthetic_operator": {"total": len(syn_ids), "by_classification": stat(syn_ids)},
        "real_model": {"total": len(real_ids), "by_classification": stat(real_ids),
                       "models": sorted({cid.split("__")[0] for cid in real_ids})},
        "per_ep": {ep: per_ep[ep] for ep in sorted(per_ep)},
        "derived_facts": {
            "synthetic_pass_per_ep": stat(syn_ids).get("PASS_GPU_CORRECT_FULL_COVERAGE", 0) // 2
                if stat(syn_ids).get("PASS_GPU_CORRECT_FULL_COVERAGE", 0) % 2 == 0 else "ODD",
            "real_pass_per_ep": stat(real_ids).get("PASS_GPU_CORRECT_FULL_COVERAGE", 0) // 2,
            "note_117_clarification": (
                "117 PASS per EP = 111 synthetic + 6 real-model. The earlier phrase "
                "'117 synthetic PASS per EP' in the review request conflated these."),
        },
        "receipt_integrity": {
            "cases": total, "receipt_missing": missing,
            "identity_mismatches": mismatched,
            "identity_check_note": case_id_format_note,
        },
    }

    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "R2_RECOUNT.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(recount, f, indent=1, ensure_ascii=False)

    def write_csv(path, rows):
        cols = ["case", "model", "ep", "batch", "opt", "real_model", "ledger_status",
                "ledger_classification", "gpu_node_ratio", "parity_all_ok", "elapsed_s", "receipt"]
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            w = csv.DictWriter(f, fieldnames=cols, lineterminator="\n")
            w.writeheader()
            w.writerows(rows)

    write_csv(os.path.join(out_dir, "R2_OPERATOR_DERIVED.csv"), rows_op)
    write_csv(os.path.join(out_dir, "R2_REAL_MODEL_DERIVED.csv"), rows_model)

    # ---- manifest reconciliation ----
    man = {}
    with open(os.path.join(R2, "manifest.sha256"), encoding="utf-8") as f:
        for line in f:
            parts = line.rstrip("\n").split("  ", 1)
            if len(parts) == 2:
                man[parts[1]] = parts[0]
    all_files = []
    for root, _, files in os.walk(R2):
        for fn in files:
            fp = os.path.join(root, fn)
            rel = os.path.relpath(fp, REPO).replace(os.sep, "/")
            all_files.append(rel)
    not_in_manifest = sorted(set(all_files) - set(man))
    man_missing_files = sorted(set(man) - set(all_files))
    # verify hashes of manifest entries
    bad_hash = []
    for rel, want in man.items():
        fp = os.path.join(REPO, rel.replace("/", os.sep))
        if os.path.exists(fp):
            got = hashlib.sha256(open(fp, "rb").read()).hexdigest()
            if got != want:
                bad_hash.append(rel)
    recon = {
        "dir_files_total": len(all_files),
        "manifest_entries": len(man),
        "excluded_from_manifest": not_in_manifest,
        "manifest_entries_missing_on_disk": man_missing_files,
        "manifest_hash_mismatches": bad_hash,
        "stale_doc_claims": {
            "R2_FINAL_CN/EN says": "1314 files / 1314 entries",
            "actual_manifest_entries": len(man),
            "finalize_summary_says": {"entries": 1319, "redaction_files_scanned": 1322},
            "explanation": (
                "1314 was a pre-refresh snapshot count. Final manifest has 1319 entries; "
                "finalize scanned 1322 files including post-manifest additions "
                "(finalize_summary.json written after manifest, excluded by design)."),
        },
    }
    with open(os.path.join(out_dir, "MANIFEST_RECON.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(recon, f, indent=1, ensure_ascii=False)

    print(json.dumps({
        "total": total, "by_classification": cls,
        "synthetic": len(syn_ids), "real": len(real_ids),
        "receipt_missing": len(missing), "identity_mismatches": len(mismatched),
        "manifest": {"entries": len(man), "dir_files": len(all_files),
                     "excluded": not_in_manifest, "bad_hash": len(bad_hash)},
    }, indent=1))


if __name__ == "__main__":
    main()
