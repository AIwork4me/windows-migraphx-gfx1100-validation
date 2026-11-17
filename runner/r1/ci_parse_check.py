"""CI-only: parse-check the ledgers and R1 manifest (pure CPU, no GPU)."""

import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main() -> int:
    json.load(open(os.path.join(REPO, "reports", "ledger.json"), encoding="utf-8"))
    json.load(open(os.path.join(REPO, "reports", "r1", "ledger.json"), encoding="utf-8"))
    p = os.path.join(REPO, "reports", "r1", "manifest.sha256")
    if os.path.isfile(p):
        n = 0
        for line in open(p, encoding="utf-8"):
            if line.strip():
                parts = line.split(None, 1)
                assert len(parts) == 2, f"bad manifest line: {line[:60]!r}"
                n += 1
        print(f"r1 manifest lines: {n}")
    print("parse OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
