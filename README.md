# windows-migraphx-gfx1100-validation

Real-machine validation of AMD MIGraphX / AMDGPU Execution Providers on
**native Windows 11 (build 26300) + Radeon RX 7900 XTX (gfx1100, DEV_744C)**,
covering Windows ML plugin EPs, ONNX Runtime CPU parity, multi-output
correctness (upstream #150), silent CPU fallback (#151), performance,
stability, and the feasibility of a native ROCm 10.1 Windows build.

> Status: execution in progress. All PASS/FAIL claims in `reports/` are backed
> by command receipts captured on the physical machine. No mock data.

See `reports/STATUS.md` for the live status board and
`reports/OVERNIGHT_FINAL_CN.md` / `OVERNIGHT_FINAL_EN.md` for final verdicts.

## Layout

- `scripts/` — PowerShell entry points (setup, run_overnight, resume, status)
- `runner/` — Python orchestration + per-phase executors
- `fixtures/` — small synthetic ONNX graphs (original, MIT)
- `reports/` — immutable evidence receipts (JSON/CSV), per milestone
- `docs/` — reproduction guides, provenance, limitations, upstream PR candidates

## Quick smoke (on a clean Windows gfx1100 box)

```powershell
powershell -ExecutionPolicy Bypass -File scripts/collect_env.ps1   # preflight
powershell -ExecutionPolicy Bypass -File scripts/setup-winml.ps1   # venvs + wheels
python runner\phases\m0_ep_discovery.py --repo .                    # EP discovery + minimal graphs
```

License: MIT for original code (see LICENSE). No upstream PRs are sent from
this repository.
