"""Wrap-up: STATUS.md, manifest.sha256, redaction scan. Safe to re-run."""
from __future__ import annotations
import argparse, hashlib, os, sys, re, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
import common as C
import ledger as L

SENSITIVE_PATTERNS = [
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"), "gh****"),
    (re.compile(r"C:\\Users\\Rocm", re.I), "C:\\Users\\<redacted>"),
    (re.compile(r"RdpIdd_IndirectDisplay&SESSIONID_\w+"), "RdpIdd<redacted>"),
]


def redact_text(t: str) -> str:
    for pat, rep in SENSITIVE_PATTERNS:
        t = pat.sub(rep, t)
    return t


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=C.REPO)
    args = ap.parse_args()
    repo = os.path.abspath(args.repo)
    led_path = os.path.join(repo, "reports", "ledger.json")

    # 1. redaction scan + in-place redact of text evidence under reports/
    findings = []
    for root, _, files in os.walk(os.path.join(repo, "reports")):
        for fn in files:
            p = os.path.join(root, fn)
            if not fn.endswith((".json", ".log", ".txt", ".md", ".csv")):
                continue
            try:
                with open(p, "r", encoding="utf-8") as f:
                    t = f.read()
            except Exception:
                continue
            t2 = redact_text(t)
            if t2 != t:
                with open(p, "w", encoding="utf-8") as f:
                    f.write(t2)
                findings.append(os.path.relpath(p, repo))

    # 2. manifest.sha256 over tracked evidence
    man_lines = []
    for sub in ("fixtures", "reports", "scripts", "runner", "tests", "docs"):
        d = os.path.join(repo, sub)
        for root, _, files in os.walk(d):
            for fn in files:
                p = os.path.join(root, fn)
                if fn.endswith((".json", ".log", ".txt", ".md", ".csv", ".onnx", ".py", ".ps1")):
                    man_lines.append(f"{C.sha256_file(p)}  {os.path.relpath(p, repo)}")
    with open(os.path.join(repo, "reports", "manifest.sha256"), "w", encoding="utf-8") as f:
        f.write("\n".join(sorted(man_lines)))

    # 3. STATUS.md
    led = L.load_ledger(led_path)
    summ = L.phase_summary(led)
    lines = ["# STATUS (auto-generated)", "",
             f"Updated: {C.now_pair()['utc']} UTC / {C.now_pair()['local']} local", "",
             "| phase | statuses |", "|---|---|"]
    for ph, sts in sorted(summ.items()):
        lines.append(f"| {ph} | {sts} |")
    lines += ["", "## supervisor tasks", ""]
    for k, v in (led.get("supervisor_tasks") or {}).items():
        lines.append(f"- {k}: {v.get('status')} rc={v.get('exit_code')}")
    lines += ["", f"Redacted files this pass: {len(findings)}"]
    with open(os.path.join(repo, "reports", "STATUS.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    json.dump({"redacted": findings}, open(os.path.join(repo, "reports", "redaction_log.json"), "w"))
    print("WRAPUP_DONE redacted:", len(findings))


if __name__ == "__main__":
    rc = main()
    sys.stdout.flush(); sys.stderr.flush()
    os._exit(rc if isinstance(rc, int) else 0)  # hard exit: skip interpreter shutdown (WinML/ORT leave non-daemon threads)
