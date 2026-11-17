# R2 LATEST — pointers to everything current

- Run id: `r2_20261117T004504Z` (all R2 evidence under `reports/r2/r2_20261117T004504Z/`)
- Final reports: `R2_FINAL_CN.md` / `R2_FINAL_EN.md` (this directory)
- Verdict board: `docs/R2_GO_NO_GO.md`; device identity: `docs/R2_DEVICE_IDENTITY.md`
- Reproduce: `docs/R2_MODEL_REPRODUCE.md`; entry scripts `scripts/run_r2.ps1` / `resume_r2.ps1`
- Failure catalog: `reports/r2/FAILURE_CATALOG.md`
- R1 (merged): PR #1 merge commit `7156b9b` on `main`
- R2 branch: `mission/r2-device-identity-real-models` — Draft PR URL is filled
  in below after remote verification

## Remote verification block (filled after push)

- main HEAD: `7156b9b3…` (R1 merge; verified 2026-11-17 via ls-remote)
- R2 branch HEAD: see `git ls-remote origin refs/heads/mission/r2-device-identity-real-models`
- R2 Draft PR: pending network recovery (see note)
- Fork PR: https://github.com/AIwork4me/onnxruntime-ep-amdgpu/pull/1
  head `e3dd9afc27ae47bb343240137b7c22f301dcce81` (verified via gh before outage)

> NOTE: host egress failed (all direct + proxied HTTPS dead, proxy port
> listening) while publishing; commits are local on the R2 branch and push
> as soon as connectivity returns. This file is updated with the verified
> SHAs/URLs at that moment.
