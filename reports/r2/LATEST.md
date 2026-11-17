# R2 LATEST — pointers to everything current

> **R2.1 (2026-11-17) is the newest round**: evidence under
> `reports/r2_1/r21_20261117T040739Z/`, corrections in
> `reports/r2_1/r21_20261117T040739Z/R21_ERRATA.md`, final verdicts in
> `R21_FINAL_CN.md` / `R21_FINAL_EN.md` (same directory). The R2 content
> below is the frozen R2 record.

- Run id: `r2_20261117T004504Z` (all R2 evidence under `reports/r2/r2_20261117T004504Z/`)
- Final reports: `R2_FINAL_CN.md` / `R2_FINAL_EN.md` (this directory; see R2.1 errata note at top)
- Verdict board: `docs/R2_GO_NO_GO.md`; device identity: `docs/R2_DEVICE_IDENTITY.md`
- Reproduce: `docs/R2_MODEL_REPRODUCE.md`; entry scripts `scripts/run_r2.ps1` / `resume_r2.ps1`
- Failure catalog: `reports/r2/FAILURE_CATALOG.md`

## Remote verification (updated 2026-11-17 by R2.1)

| ref | SHA | verified via |
|---|---|---|
| main HEAD | `7156b9b504c4c31065eb47c0191f3e2f55f316ed` (R1 merge) | git ls-remote (HTTPS, 2026-11-17T04:07Z) |
| R2 branch `mission/r2-device-identity-real-models` at R2 publication | `f32f4aa1ffce4872608e5e94874a5ee15f08a634` | git ls-remote (verified local==remote==PR head; the earlier `a4688c9` listed here was an interim R2.5 commit — corrected) |
| PR #2 head | = branch HEAD at verification time; live value via `gh api` / R2.1 PR description | gh api |
| Fork PR head | `e3dd9afc27ae47bb343240137b7c22f301dcce81` at R2 publication; R2.1 pushed further commits (see fork PR description) | gh api |

- R2 Draft PR: https://github.com/AIwork4me/windows-migraphx-gfx1100-validation/pull/2
  (Draft, NOT merged; CI: 3/3 cpu-tests green — r1-cpu-tests ×2 + r2-cpu-tests)
- Fork Draft PR: https://github.com/AIwork4me/onnxruntime-ep-amdgpu/pull/1
- R1 PR: https://github.com/AIwork4me/windows-migraphx-gfx1100-validation/pull/1 (merged, `7156b9b`)

> Publication note: host egress to github.com:443 failed for a period
> (proxy node outage; api.github.com and SSH-22 stayed reachable). The
> branch was pushed over SSH using a write deploy key added via the API
> (key `r2-deploy-key`, removable in repo settings when normal HTTPS
> push works again).
