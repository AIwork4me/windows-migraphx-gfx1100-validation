# R2.1 — Adversarial CI negative tests (A4 evidence)

Validator under test: `runner/r2_1/r21_validate.py` @ commit `b56d890`
(cross-drive test fix + workflow quoting fix @ `61ace45`/`ff178e2`),
test file `tests/test_r21_validator.py`, 19 cases, run by
`python -m unittest tests.test_r21_validator -v` inside the
`r2-cpu-tests` workflow (windows-latest, Python 3.12).

## Why this gate exists

R2's `runner/r2/ci_r2_check.py` only required `nodes_by_provider` to be
non-empty, so a forged receipt with `{"CPUExecutionProvider": 5587}` nodes
and the EP name merely *listed* in `session_providers` satisfied the
profiler gate (the exact SSD pattern would have false-PASSed as GPU work).

## Negative fixtures — every one MUST fail validation (red-team gate)

| # | forged receipt mutation | expected rejection |
|---|---|---|
| 1 | EP in session_providers, nodes `{CPU: 5587}` only | FAIL — the R2 false-positive pattern itself |
| 2 | `{EP: 0, CPU: 100}` | FAIL — zero EP nodes |
| 3 | declared output without per-output parity entry | FAIL — missing output evidence |
| 4 | `parity.all_ok=true` but `per_output[Y].all_ok=false` | FAIL — forged aggregate contradiction |
| 5 | `outputs_actual=["W"]` ≠ expected | FAIL — output name mismatch |
| 6 | `parity.names_match=false` | FAIL |
| 7 | LUID ∈ {0, −5, "72346", None, 72346.0} | FAIL — non-identity LUID (typed) |
| 8 | `canary.passed=false` | FAIL |
| 9 | `nodes_by_provider[ep]` = string `"12"` / NaN / bool `True` | FAIL — poisoned provider map |
| 10 | `nodes_by_provider` key `None` | FAIL |
| 11 | `model_sha256` = "deadbeef" / absent | FAIL — no model identity |
| 12 | parity `max_abs` = NaN / +inf | FAIL |
| 13 | EP dropped from `session_providers` | FAIL |
| 14 | tolerance missing rtol | FAIL |
| 15 | OPERATOR_MATRIX duplicate case ids | FAIL (ledger↔matrix identity) |
| 16 | matrix missing a ledger case (orphan/missing) | FAIL |
| 17 | manifest hash ≠ file bytes | FAIL (falsified manifest) |
| 18 | per-output shape_expected ≠ shape_actual | FAIL |

## Positive fixtures — must NOT over-reject

| # | valid shape | expected |
|---|---|---|
| P1 | single fused EP node (`{EP: 1}`), full-coverage semantics | PASS_GPU_EXECUTED_FULL |
| P2 | EP 9 nodes + CPU 2 nodes | PASS_GPU_EXECUTED (partial, not full) |
| P3 | genuine R2 receipt (`cases/abs__AMDGPU__b1__ALL.json`) | PASS_* — 234/234 real receipts validate via CLI |

## Local run (venv-cpu, 2026-11-17)

```
$ python -m unittest tests.test_r21_validator -v
Ran 19 tests in 0.019s
OK
$ python runner/r2_1/r21_validate.py --run-dir reports/r2/r2_20261117T004504Z
run-dir verdict: OK (234 PASS receipts validated)
```

## CI run on exact commit

- `r2-cpu-tests` push run 44019060449 (commit ff178e2): SUCCESS, step
  "R2.1 hardened evidence gate" log line:
  `r21_validate: OK (234 GPU PASS receipts evidence-validated, CPU-CI cannot attest hardware)`
- PR-event run 44019068997 (same head): SUCCESS.
- History note (kept for honesty): the first two push runs of the new
  workflow (44018481765, 44018732262) failed in 0s — the step NAME
  contained an unquoted `adversarial: ` which broke YAML parsing; fixed by
  quoting; and the first PR run exposed a real cross-drive
  (`os.path.relpath` C:↔D:) test bug, also fixed. Both defects are part of
  the CI evidence trail, not hidden.

CPU CI cannot attest GPU behavior; the gate only ensures published
receipts carry proof-shaped evidence. Hardware claims remain grounded in
the physical-host receipts.
