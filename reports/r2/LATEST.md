# R2 LATEST — pointers to everything current

- Run id: `r2_20261117T004504Z` (all R2 evidence under `reports/r2/r2_20261117T004504Z/`)
- Final reports: `R2_FINAL_CN.md` / `R2_FINAL_EN.md` (this directory)
- Verdict board: `docs/R2_GO_NO_GO.md`; device identity: `docs/R2_DEVICE_IDENTITY.md`
- Reproduce: `docs/R2_MODEL_REPRODUCE.md`; entry scripts `scripts/run_r2.ps1` / `resume_r2.ps1`
- Failure catalog: `reports/r2/FAILURE_CATALOG.md`

## Remote verification (2026-11-17, post-publish)

| ref | SHA | verified via |
|---|---|---|
| main HEAD | `7156b9b504c4c31065eb47c0191f3e2f55f316ed` (R1 merge) | git ls-remote (SSH) |
| R2 branch `mission/r2-device-identity-real-models` | `a4688c9` (full: see ls-remote at publication) | git ls-remote (SSH) |
| PR #2 head | = branch HEAD | gh api |
| Fork PR head | `e3dd9afc27ae47bb343240137b7c22f301dcce81` | gh api |

- R2 Draft PR: https://github.com/AIwork4me/windows-migraphx-gfx1100-validation/pull/2
  (Draft, NOT merged; CI: 3/3 cpu-tests green — r1-cpu-tests ×2 + r2-cpu-tests)
- Fork Draft PR: https://github.com/AIwork4me/onnxruntime-ep-amdgpu/pull/1
- R1 PR: https://github.com/AIwork4me/windows-migraphx-gfx1100-validation/pull/1 (merged, `7156b9b`)

> Publication note: host egress to github.com:443 failed for a period
> (proxy node outage; api.github.com and SSH-22 stayed reachable). The
> branch was pushed over SSH using a write deploy key added via the API
> (key `r2-deploy-key`, removable in repo settings when normal HTTPS
> push works again).
