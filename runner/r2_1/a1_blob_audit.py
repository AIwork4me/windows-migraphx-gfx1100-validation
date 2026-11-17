#!/usr/bin/env python3
"""A1: byte-level audit of every Modified path vs merge-base.

For each path modified between <merge-base> and HEAD:
  - compare raw bytes (git blob) old vs new
  - if bytes differ, compare after EOL normalization (LF-only)
  - classify: IDENTICAL_BYTES (should not appear in M list), EOL_ONLY, CONTENT_CHANGED
Writes A1_BLOB_AUDIT.json and a restore list (paths to restore from merge-base).
Does NOT modify the worktree by itself; emit A1_RESTORE_LIST.txt.
"""
import hashlib
import json
import subprocess
import sys

REPO = r"C:\AIwork4me\windows-migraphx-gfx1100-validation"


def git(args):
    r = subprocess.run(["git", "-C", REPO] + args, capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(f"git {args}: {r.stderr.decode(errors='replace')[:300]}")
    return r.stdout


def blob_bytes(rev, path):
    return git(["cat-file", "blob", f"{rev}:{path}"])


def sha256(b):
    return hashlib.sha256(b).hexdigest()


def blob_id(rev, path):
    return git(["rev-parse", f"{rev}:{path}"]).decode().strip()


def main():
    mb, head, out_dir = sys.argv[1], sys.argv[2], sys.argv[3]
    modified = git(["diff", "--name-status", f"{mb}...{head}"]).decode().splitlines()
    mpaths = [l.split("\t", 1)[1] for l in modified if l.startswith("M\t")]
    rows = []
    for p in mpaths:
        old = blob_bytes(mb, p)
        new = blob_bytes(head, p)
        if old == new:
            cls = "IDENTICAL_BYTES"  # index says M but content same (mode change?)
        elif old.replace(b"\r\n", b"\n") == new.replace(b"\r\n", b"\n"):
            cls = "EOL_ONLY"
        else:
            cls = "CONTENT_CHANGED"
        rows.append({
            "path": p,
            "class": cls,
            "blob_merge_base": blob_id(mb, p),
            "blob_head": blob_id(head, p),
            "sha256_merge_base": sha256(old),
            "sha256_head": sha256(new),
            "bytes_merge_base": len(old),
            "bytes_head": len(new),
            "crlf_count_merge_base": old.count(b"\r\n"),
            "crlf_count_head": new.count(b"\r\n"),
        })
    summary = {}
    for r in rows:
        summary[r["class"]] = summary.get(r["class"], 0) + 1
    report = {
        "merge_base": mb,
        "head_at_audit": head,
        "total_modified": len(rows),
        "summary": summary,
        "files": rows,
    }
    with open(f"{out_dir}/A1_BLOB_AUDIT.json", "w", encoding="utf-8", newline="\n") as f:
        json.dump(report, f, indent=1)
    with open(f"{out_dir}/A1_RESTORE_LIST.txt", "w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            if r["class"] == "EOL_ONLY":
                f.write(r["path"] + "\n")
    print(json.dumps(summary, indent=1))
    cc = [r["path"] for r in rows if r["class"] == "CONTENT_CHANGED"]
    print(f"CONTENT_CHANGED ({len(cc)}):")
    for p in cc:
        print(" ", p)


if __name__ == "__main__":
    main()
