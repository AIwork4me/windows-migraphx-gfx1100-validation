"""Atomic JSON ledger: the single source of truth for case states.

Every test case has a unique id. States:
  PENDING -> RUNNING -> PASS | FAIL | BLOCKED_DEPENDENCY | SKIP_NOT_APPLICABLE
             | INCONCLUSIVE | NOT_RUN_TIME_BUDGET | CRASH_NATIVE
resume logic re-runs PENDING/RUNNING(stale) cases only, never terminal ones.
"""
from __future__ import annotations
import json, os, tempfile, datetime, threading

_LOCK = threading.RLock()  # reentrant: upsert_case holds it while calling load_ledger

def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def load_ledger(path: str) -> dict:
    with _LOCK:
        if not os.path.exists(path):
            return {"cases": []}
        with open(path, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                # ledger corrupted mid-write should be impossible (atomic), but recover
                return {"cases": [], "recovered_from_corruption": _now()}

def _atomic_dump(path: str, obj: dict) -> None:
    d = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try: os.remove(tmp)
            except OSError: pass

def get_case(ledger: dict, case_id: str) -> dict | None:
    for c in ledger.get("cases", []):
        if c.get("id") == case_id:
            return c
    return None

def upsert_case(path: str, case: dict) -> dict:
    """Insert or replace a case record; stamps updated_utc."""
    with _LOCK:
        led = load_ledger(path)
        led["updated_utc"] = _now()
        replaced = False
        for i, c in enumerate(led.get("cases", [])):
            if c.get("id") == case["id"]:
                prev = c
                merged = {**prev, **case}
                merged.setdefault("history", []).append({
                    "at": _now(), "status": case.get("status", "?")})
                led["cases"][i] = merged
                replaced = True
                break
        if not replaced:
            case.setdefault("history", [{"at": _now(), "status": case.get("status", "?")}])
            led.setdefault("cases", []).append(case)
        _atomic_dump(path, led)
        return led

def terminal(case: dict) -> bool:
    return case.get("status") in {
        "PASS", "FAIL", "BLOCKED_DEPENDENCY", "SKIP_NOT_APPLICABLE",
        "INCONCLUSIVE", "NOT_RUN_TIME_BUDGET", "CRASH_NATIVE", "DANGEROUS_STOP"}

def pending_cases(ledger: dict) -> list[dict]:
    out = []
    for c in ledger.get("cases", []):
        if not terminal(c):
            out.append(c)
    return out

def phase_summary(ledger: dict) -> dict:
    s: dict[str, dict] = {}
    for c in ledger.get("cases", []):
        p = c.get("phase", "?")
        st = c.get("status", "?")
        s.setdefault(p, {})
        s[p][st] = s[p].get(st, 0) + 1
    return s
