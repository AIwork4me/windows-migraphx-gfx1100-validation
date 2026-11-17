#!/usr/bin/env python3
"""R2.1-E: redaction scan + deterministic manifest for the R2.1 run dir.

Order (mirrors R2 contract):
  1. redaction scan over every file in the run dir -> redaction_scan.json
     (0 hits required to proceed)
  2. manifest.sha256 over every file EXCLUDING manifest.sha256 itself and
     finalize_summary.json (post-manifest metadata, same exclusions as R2)
  3. finalize_summary.json written after the manifest, excluded by design
  4. self-check: every manifest entry re-hashed, count reported

Deterministic: sorted relative paths, LF, sha256 lowercase hex.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_patterns():
    user = os.environ.get("USERNAME", "")
    pats = {
        "win_token": re.compile(rb"(ghp_|github_pat_|gho_|ghs_)[A-Za-z0-9_]{10,}"),
        "ssh_key": re.compile(rb"-----BEGIN (OPENSSH|RSA|EC) PRIVATE KEY-----"),
        "user_profile": None,  # built below (needs user)
        "username": None,
        "deploy_key_note": re.compile(rb"r2-deploy-key[^\s]*PRIVATE"),
    }
    if user:
        ub = user.encode()
        pats["username"] = re.compile(re.escape(ub))
        pats["user_profile"] = re.compile(
            rb"[A-Za-z]:\\+Users\\+" + re.escape(ub) + rb"\b|/[A-Za-z]/+Users/+"
            + re.escape(ub) + rb"\b")
    return pats


def scan(run_dir) -> dict:
    pats = build_patterns()
    hits, n_files = [], 0
    for root, _, files in os.walk(run_dir):
        for fn in files:
            fp = os.path.join(root, fn)
            n_files += 1
            if os.path.splitext(fn)[1].lower() in (".zip", ".7z", ".dmp", ".onnx",
                                                   ".ort", ".pt"):
                hits.append({"file": os.path.relpath(fp, run_dir),
                             "pattern": "BINARY_OR_WEIGHTS_FORBIDDEN_IN_RUNDIR",
                             "note": "weights/dumps must not be copied into evidence dir"})
                continue
            data = open(fp, "rb").read()
            for name, pat in pats.items():
                if pat is None:
                    continue
                for m in pat.finditer(data):
                    start = max(0, m.start() - 20)
                    ctx = data[start:m.end() + 20]
                    hits.append({"file": os.path.relpath(fp, run_dir).replace(os.sep, "/"),
                                 "pattern": name,
                                 "context": ctx.decode("utf-8", errors="replace")[:80]})
    return {"files_scanned": n_files, "hits": hits,
            "generated_utc": __import__("time").strftime("%Y-%m-%dT%H:%M:%SZ",
                                                         __import__("time").gmtime())}


def build_manifest(run_dir) -> dict:
    entries = {}
    for root, _, files in os.walk(run_dir):
        for fn in files:
            if fn in ("manifest.sha256", "finalize_summary.json", "redaction_scan.json"):
                continue  # post-manifest metadata, excluded by design (R2 contract)
            fp = os.path.join(root, fn)
            rel = os.path.relpath(fp, REPO).replace(os.sep, "/")
            entries[rel] = sha256_file(fp)
    man_path = os.path.join(run_dir, "manifest.sha256")
    with open(man_path, "w", encoding="utf-8", newline="\n") as f:
        for rel in sorted(entries):
            f.write(f"{entries[rel]}  {rel}\n")
    # self-check
    bad = []
    for line in open(man_path, encoding="utf-8"):
        want, rel = line.rstrip("\n").split("  ", 1)
        fp = os.path.join(REPO, rel.replace("/", os.sep))
        if sha256_file(fp) != want:
            bad.append(rel)
    return {"entries": len(entries), "self_check_mismatches": bad,
            "excluded_by_design": ["manifest.sha256", "finalize_summary.json",
                                   "redaction_scan.json"],
            "manifest": os.path.relpath(man_path, REPO).replace(os.sep, "/")}


def sanitize(run_dir, pats) -> int:
    """In-place redact hits in text files (byte-safe for ASCII patterns)."""
    n = 0
    for root, _, files in os.walk(run_dir):
        for fn in files:
            fp = os.path.join(root, fn)
            if os.path.splitext(fn)[1].lower() in (".zip", ".7z", ".dmp"):
                continue
            data = open(fp, "rb").read()
            out = data
            for name, pat in pats.items():
                if pat is None:
                    continue
                if name in ("username", "user_profile"):
                    out = pat.sub(b"<REDACTED>", out)
                else:
                    out = pat.sub(name.upper().encode() + b"_REDACTED", out)
            if out != data:
                open(fp, "wb").write(out)
                n += 1
    return n


def main():
    run_dir = sys.argv[1]
    if len(sys.argv) > 2 and sys.argv[2] == "--sanitize":
        n = sanitize(run_dir, build_patterns())
        print(f"sanitized {n} files")
        return 0
    red = scan(run_dir)
    with open(os.path.join(run_dir, "redaction_scan.json"), "w",
              encoding="utf-8", newline="\n") as f:
        json.dump(red, f, indent=1)
    print(f"redaction: {red['files_scanned']} files, {len(red['hits'])} hits")
    if red["hits"]:
        for h in red["hits"][:10]:
            print("  HIT:", h["file"], h["pattern"], h.get("context", "")[:60])
        print("REFUSING to build manifest with redaction hits; fix sources first")
        return 1
    man = build_manifest(run_dir)
    fin = dict(man)
    fin["redaction_hits"] = len(red["hits"])
    fin["redaction_files"] = red["files_scanned"]
    fin["generated_utc"] = red["generated_utc"]
    with open(os.path.join(run_dir, "finalize_summary.json"), "w",
              encoding="utf-8", newline="\n") as f:
        json.dump(fin, f, indent=1)
    print(f"manifest: {man['entries']} entries, self-check mismatches: "
          f"{len(man['self_check_mismatches'])}")
    return 0 if not man["self_check_mismatches"] else 1


if __name__ == "__main__":
    sys.exit(main())
