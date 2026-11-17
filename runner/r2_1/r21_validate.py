#!/usr/bin/env python3
"""R2.1 hardened receipt/ledger/manifest validator (CI, CPU-only).

Fixes the R2 false-positive gate: previously `nodes_by_provider` merely had
to be non-empty, so a CPU-only receipt could satisfy the profiler check if
the EP name appeared in session_providers. This module enforces:

  PASS_GPU_EXECUTED
      - exact requested EP present in session_providers, AND
      - nodes_by_provider[ep] is a finite integer > 0 (typed), AND
      - per-output parity detail present, consistent with all_ok, and
        every declared output covered; device_resolution has int LUID > 0
        and canary.passed True with finite max_abs.
  FULL_RUNTIME_PARTITION_COVERAGE (additional, stricter label)
      - PASS_GPU_EXECUTED, AND
      - zero CPU compute node events in the profile, AND
      - no unexpected non-EP compute providers in nodes_by_provider.
        One fused EP node may represent many original ONNX nodes: this is
        observed ORT runtime event coverage, NOT per-ONNX-node proof.

CPU CI cannot attest actual GPU behavior; this gate only rejects receipts
that do not *evidence* GPU execution.

CLI:
  python runner/r2_1/r21_validate.py            # validate all tracked R2 dirs
  python runner/r2_1/r21_validate.py --receipt <json>
  python runner/r2_1/r21_validate.py --run-dir <dir>
Exit 0 = clean, 1 = validation failure (adversarial fixtures must land here).
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
KNOWN_EPS = ("AMDGPUExecutionProvider", "MIGraphXExecutionProvider")
NON_COMPUTE_PROVIDERS = set()  # profile node events are compute nodes by construction


def _is_int_like(v):
    return isinstance(v, int) and not isinstance(v, bool)


def validate_receipt(rec, expected_ep=None):
    """Return (verdict, errors). verdict in {PASS_GPU_EXECUTED_FULL,
    PASS_GPU_EXECUTED, FAIL}. FAIL receipts produce errors list."""
    errs = []
    if not isinstance(rec, dict):
        return "FAIL", ["receipt is not an object"]
    ep = rec.get("ep") or expected_ep
    if not ep or not isinstance(ep, str):
        return "FAIL", ["missing/invalid ep"]

    # model identity
    sha = rec.get("model_sha256")
    if not isinstance(sha, str) or len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha.lower()):
        errs.append("model_sha256 missing/not 64-hex")

    # session providers: exact requested EP must be present
    provs = rec.get("session_providers")
    if not isinstance(provs, list) or not provs or ep not in provs:
        errs.append(f"requested EP {ep!r} not in session_providers {provs!r}")

    # node attribution: typed, finite, positive for the requested EP
    nbp = rec.get("profile", {}).get("nodes_by_provider")
    if not isinstance(nbp, dict) or not nbp:
        errs.append("nodes_by_provider missing/empty")
    else:
        for k, v in nbp.items():
            if not isinstance(k, str) or not k:
                errs.append(f"nodes_by_provider key invalid: {k!r}")
            if not _is_int_like(v):
                errs.append(f"nodes_by_provider[{k!r}] not an int: {v!r} (string/None/bool rejected)")
            elif v < 0 or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
                errs.append(f"nodes_by_provider[{k!r}] negative/NaN/inf: {v!r}")
        ep_nodes = nbp.get(ep)
        if not _is_int_like(ep_nodes) or ep_nodes <= 0:
            errs.append(f"nodes_by_provider[{ep!r}]={ep_nodes!r} is not a positive int "
                        "(CPU-only receipts cannot be GPU PASS)")

    # parity: per-output detail required and consistent
    par = rec.get("parity", {})
    if not isinstance(par, dict):
        errs.append("parity missing/not object")
    else:
        outs = rec.get("output_names")
        if not isinstance(outs, list) or not outs:
            errs.append("output_names missing/empty")
        per = par.get("per_output")
        if not isinstance(per, dict) or not per:
            errs.append("parity.per_output missing/empty (all_ok alone is not evidence)")
        else:
            for o in (outs or []):
                if o not in per:
                    errs.append(f"declared output {o!r} has no per-output parity entry")
            for name, entry in per.items():
                if isinstance(entry, dict):
                    # accepted shapes: {all_ok: true, max_abs} OR the genuine R2
                    # receipt shape {status: "OK", allclose: true, max_abs_diff}
                    ok_flag = entry.get("all_ok") is True or (
                        entry.get("status") == "OK" and entry.get("allclose") is True)
                    if not ok_flag:
                        errs.append(f"parity.per_output[{name!r}] not proven OK "
                                    f"(all_ok/status+allclose): {entry.get('all_ok')!r},"
                                    f"{entry.get('status')!r},{entry.get('allclose')!r}")
                    m = entry.get("max_abs", entry.get("max_abs_diff"))
                    if m is None or isinstance(m, bool) or not isinstance(m, (int, float)) or \
                       (isinstance(m, float) and (math.isnan(m) or math.isinf(m))) or m < 0:
                        errs.append(f"parity.per_output[{name!r}] max_abs(_diff) invalid: {m!r}")
                    se, sa = entry.get("shape_expected"), entry.get("shape_actual")
                    if se is not None and sa is not None and list(se) != list(sa):
                        errs.append(f"parity.per_output[{name!r}] shape mismatch {se} vs {sa}")
                else:
                    errs.append(f"parity.per_output[{name!r}] not an object: {entry!r}")
            if par.get("all_ok") is not True:
                errs.append("parity.all_ok is not True")
        exp = par.get("outputs_expected")
        act = par.get("outputs_actual")
        if isinstance(exp, list) and isinstance(act, list) and sorted(exp) != sorted(act):
            errs.append("parity expected/actual output name sets differ")
        tol = rec.get("tolerance")
        if not isinstance(tol, dict) or "atol" not in tol or "rtol" not in tol:
            errs.append("tolerance atol/rtol not declared")
        if par.get("names_match") is False:
            errs.append("parity.names_match is False")

    # device identity evidence
    dr = rec.get("device_resolution", {})
    if not isinstance(dr, dict):
        errs.append("device_resolution missing/not object")
    else:
        luid = dr.get("luid")
        if not _is_int_like(luid) or luid <= 0:
            errs.append(f"device_resolution.luid invalid: {luid!r}")
        can = dr.get("canary")
        if not isinstance(can, dict) or can.get("passed") is not True:
            errs.append("device_resolution.canary.passed is not True")
        elif not _is_int_like(can.get("parity_max_abs")) and not (
                isinstance(can.get("parity_max_abs"), float)
                and not math.isnan(can["parity_max_abs"]) and not math.isinf(can["parity_max_abs"])):
            errs.append("canary.parity_max_abs missing/non-finite")

    if errs:
        return "FAIL", errs

    # coverage level
    cpu_nodes = nbp.get("CPUExecutionProvider", 0)
    other = {k: v for k, v in nbp.items() if k not in (ep, "CPUExecutionProvider")}
    if cpu_nodes == 0 and not any(v > 0 for v in other.values()):
        return "PASS_GPU_EXECUTED_FULL", []
    return "PASS_GPU_EXECUTED", [f"cpu_nodes={cpu_nodes}, other_providers={other}"]


def validate_ledger_and_matrix(run_dir):
    """Ledger vs OPERATOR_MATRIX identity: no dup/missing/orphan rows."""
    errs = []
    led = os.path.join(run_dir, "ledger.json")
    if not os.path.isfile(led):
        return [f"{led}: missing ledger"]
    data = json.load(open(led, encoding="utf-8"))
    cases = data.get("cases", {})
    mat = os.path.join(run_dir, "OPERATOR_MATRIX.csv")
    if os.path.isfile(mat):
        with open(mat, encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        ids = [r.get("case") for r in rows]
        dups = {i for i in ids if ids.count(i) > 1}
        if dups:
            errs.append(f"OPERATOR_MATRIX duplicate case ids: {sorted(dups)[:5]}")
        missing = set(cases) - set(ids)
        orphan = set(ids) - set(cases)
        if missing:
            errs.append(f"OPERATOR_MATRIX missing {len(missing)} ledger cases")
        if orphan:
            errs.append(f"OPERATOR_MATRIX has {len(orphan)} orphan rows")
        for r in rows:
            c = cases.get(r.get("case"))
            if c and r.get("classification") != c.get("classification"):
                errs.append(f"{r.get('case')}: matrix class {r.get('classification')} != "
                            f"ledger {c.get('classification')}")
    return errs


def verify_manifest(run_dir):
    """Byte-hash every manifest entry; detect falsified files."""
    man_p = os.path.join(run_dir, "manifest.sha256")
    if not os.path.isfile(man_p):
        return [f"{man_p}: not found"]
    errs, n = [], 0
    with open(man_p, encoding="utf-8") as f:
        for line in f:
            parts = line.rstrip("\r\n").split("  ", 1)
            if len(parts) != 2:
                errs.append(f"manifest line malformed: {line[:60]!r}")
                continue
            want, rel = parts
            fp = os.path.join(REPO, rel.replace("/", os.sep))
            n += 1
            if not os.path.isfile(fp):
                errs.append(f"manifest entry missing on disk: {rel}")
                continue
            got = hashlib.sha256(open(fp, "rb").read()).hexdigest()
            if got != want:
                errs.append(f"manifest hash mismatch (possible falsification): {rel}")
    if n == 0:
        errs.append("manifest empty")
    return errs


def validate_run_dir(run_dir):
    errs = []
    led = os.path.join(run_dir, "ledger.json")
    if not os.path.isfile(led):
        return [f"{run_dir}: no ledger.json (skip)"], 0
    data = json.load(open(led, encoding="utf-8"))
    cases = data.get("cases", {})
    n_pass = 0
    for cid, c in sorted(cases.items()):
        cls = c.get("classification", "")
        if not cls.startswith("PASS"):
            continue
        rp = os.path.join(REPO, c.get("receipt", "x").replace("\\", os.sep))
        if not os.path.isfile(rp):
            errs.append(f"{cid}: PASS without receipt on disk")
            continue
        rec = json.load(open(rp, encoding="utf-8"))
        verdict, rerrs = validate_receipt(rec, expected_ep=c.get("key", {}).get("ep"))
        if verdict == "FAIL":
            errs.append(f"{cid}: receipt rejected: {rerrs[:3]}")
        else:
            n_pass += 1
            if verdict == "PASS_GPU_EXECUTED":
                # historical receipts claiming FULL must actually be full
                if cls.endswith("FULL_COVERAGE"):
                    errs.append(f"{cid}: claims FULL_COVERAGE but CPU/other nodes present: {rerrs}")
    errs += validate_ledger_and_matrix(run_dir)
    return errs, n_pass


def main(argv):
    args = argv[1:]
    if args[:1] == ["--receipt"]:
        rec = json.load(open(args[1], encoding="utf-8"))
        v, errs = validate_receipt(rec)
        print(f"receipt verdict: {v}")
        for e in errs:
            print(" -", e)
        return 0 if v != "FAIL" else 1
    if args[:1] == ["--run-dir"]:
        rd = args[1]
        errs, n = validate_run_dir(rd)
        errs += verify_manifest(rd)
        for e in errs:
            print(" -", e)
        print(f"run-dir verdict: {'OK' if not errs else 'FAILED'} ({n} PASS receipts validated)")
        return 0 if not errs else 1
    # default: every tracked reports/r2/r2_* dir + r2_1 projections if present
    total_errs, total_pass = [], 0
    base = os.path.join(REPO, "reports", "r2")
    for d in sorted(os.listdir(base)) if os.path.isdir(base) else []:
        rd = os.path.join(base, d)
        if os.path.isdir(rd) and d.startswith("r2_"):
            errs, n = validate_run_dir(rd)
            errs += verify_manifest(rd)
            total_errs += [f"[{d}] {e}" for e in errs]
            total_pass += n
    for e in total_errs:
        print(" -", e)
    print(f"r21_validate: {'OK' if not total_errs else 'FAILED'} "
          f"({total_pass} GPU PASS receipts evidence-validated, CPU-CI cannot attest hardware)")
    return 0 if not total_errs else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
