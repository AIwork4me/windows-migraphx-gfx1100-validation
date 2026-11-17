"""Verify a manifest.sha256 (recompute hashes; report missing/mismatch)."""

from __future__ import annotations

import argparse
import hashlib
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    args = ap.parse_args()
    mp = os.path.join(REPO, args.manifest) if not os.path.isabs(args.manifest) \
        else args.manifest
    ok = miss = bad = 0
    with open(mp, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            h, _, rel = line.partition("  ")
            p = os.path.join(REPO, rel)
            if not os.path.isfile(p):
                miss += 1
                print("MISSING", rel)
            elif sha256_of(p) != h:
                bad += 1
                print("MISMATCH", rel)
            else:
                ok += 1
    print(f"verify_manifest: ok={ok} missing={miss} mismatch={bad}")
    return 0 if (miss == 0 and bad == 0) else 1


if __name__ == "__main__":
    sys.exit(main())
