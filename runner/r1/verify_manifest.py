"""R1.4: verify the round-1 manifest, then generate the R1 manifest.

- Round-1 `reports/manifest.sha256` is verified read-only; missing/mismatched
  entries are REPORTED, never rewritten (mission R1.4.4).
- R1 manifest covers reports/r1/** plus the new docs, excluding state-ish
  files (LATEST.md, ledger.json) and the manifest itself. A small
  self-verification follows generation.
- Also runs a secret/privacy scan over everything the R1 manifest covers.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from r1.r1_common import atomic_write_json, finish_receipt, new_receipt  # noqa: E402

ROUND1_MANIFEST = os.path.join(REPO, "reports", "manifest.sha256")
R1_ROOT = os.path.join(REPO, "reports", "r1")
EXCLUDE_NAMES = {"manifest.sha256", "LATEST.md", "ledger.json"}

SECRET_PATTERNS = [
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"), "github token"),
    (re.compile(r"(?i)(api[_-]?key|secret|password)\s*[=:]\s*\S+"), "credential assignment"),
    (re.compile(r"C:\\Users\\(?!<USER>)[A-Za-z0-9_.\-]+"), "user path"),
    (re.compile(r"(?i)computername\s*=\s*[A-Za-z0-9\-]+"), "hostname"),
]


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_round1() -> dict:
    res = {"entries": 0, "ok": 0, "missing": [], "mismatched": [], "unparsable": []}
    if not os.path.isfile(ROUND1_MANIFEST):
        res["error"] = "round-1 manifest absent"
        return res
    for line in open(ROUND1_MANIFEST, encoding="utf-8", errors="replace"):
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            res["unparsable"].append(line[:80])
            continue
        digest, rel = parts
        rel = rel.strip().lstrip("*").replace("/", os.sep)
        p = os.path.join(REPO, rel)
        if not os.path.isfile(p):
            res["missing"].append(rel)
            continue
        res["entries"] += 1
        if sha256_of(p).lower() == digest.lower():
            res["ok"] += 1
        else:
            res["mismatched"].append(rel)
    res["verdict"] = ("ROUND1_MANIFEST_OK" if res["entries"] == res["ok"]
                      and not res["missing"] and not res["mismatched"]
                      and not res["unparsable"] else "ROUND1_MANIFEST_DISCREPANCIES")
    return res


def r1_files() -> list:
    out = []
    for root, _dirs, files in os.walk(R1_ROOT):
        for fn in files:
            if fn in EXCLUDE_NAMES:
                continue
            p = os.path.join(root, fn)
            out.append(os.path.relpath(p, REPO))
    for extra in ["docs/ISSUE_151_SCOPE_AND_LIMITATIONS.md",
                  "docs/R1_REPRODUCE.md", "docs/R1_UPSTREAM_READINESS.md"]:
        if os.path.isfile(os.path.join(REPO, extra)):
            out.append(extra)
    return sorted(out)


def secret_scan(files: list) -> dict:
    hits = []
    for rel in files:
        p = os.path.join(REPO, rel)
        try:
            text = open(p, encoding="utf-8", errors="replace").read()
        except Exception:
            continue
        for pat, label in SECRET_PATTERNS:
            for m in pat.finditer(text):
                hits.append({"file": rel.replace("\\", "/"), "label": label,
                             "sample": text[max(0, m.start() - 10):m.end() + 10]
                             .replace("\n", " ")[:60]})
    return {"hits": hits[:100], "hit_count": len(hits)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    args = ap.parse_args()
    r = new_receipt("r1_verify_manifest", args.run_id, sys.argv)

    r["round1_verification"] = verify_round1()

    files = r1_files()
    lines = []
    for rel in files:
        lines.append(f"{sha256_of(os.path.join(REPO, rel))}  "
                     f"{rel.replace(os.sep, '/')}")

    r["manifest"] = {
        "files": len(files),
        "excluded": sorted(EXCLUDE_NAMES),
        "note": "state files excluded; manifest not self-referential",
    }

    # write manifest, then re-verify it immediately
    mpath = os.path.join(R1_ROOT, "manifest.sha256")
    with open(mpath, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    bad = []
    for line in lines:
        digest, rel = line.split(None, 1)
        if sha256_of(os.path.join(REPO, rel)) != digest:
            bad.append(rel)
    r["manifest"]["self_check"] = "OK" if not bad else {"bad": bad}

    r["secret_scan"] = secret_scan(files + [
        "reports/r1/LATEST.md", "reports/r1/ledger.json"])
    r["secret_scan"]["verdict"] = ("CLEAN" if r["secret_scan"]["hit_count"] == 0
                                   else "REVIEW_NEEDED")

    outdir = os.path.join(R1_ROOT, args.run_id)
    finish_receipt(r, 0, "VERIFY_DONE")
    atomic_write_json(os.path.join(outdir, "redaction_scan.json"), r)
    print(json.dumps({"round1": r["round1_verification"]["verdict"],
                      "manifest_files": r["manifest"]["files"],
                      "self_check": r["manifest"]["self_check"],
                      "secret_scan": r["secret_scan"]["verdict"],
                      "secret_hits": r["secret_scan"]["hit_count"]}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
