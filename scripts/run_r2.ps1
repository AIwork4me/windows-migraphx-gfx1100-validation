<#
R2 single-entry runner (mission R2.5).

Usage examples:
  powershell -File scripts\run_r2.ps1 -RunId r2_20261117T004504Z            # full flow
  powershell -File scripts\run_r2.ps1 -RunId r2_x -Resume                    # resume ledger
  powershell -File scripts\run_r2.ps1 -RunId r2_x -SkipModels -SkipSoak      # docs/tests only

Idempotent resume: case identity = (model_sha, ep, batch, opt, ort, device
fingerprint). Cases already DONE with an existing receipt are skipped;
FAILED cases re-run only with -RetryFailed (after a deliberate fix).
#>
param(
  [Parameter(Mandatory = $true)][string]$RunId,
  [switch]$Resume,
  [switch]$SkipIdentity,
  [switch]$SkipModels,
  [switch]$SkipSoak,
  [switch]$SkipPerf,
  [switch]$RetryFailed,
  [double]$MaxMinutes = 240,
  [double]$DiskFloorGB = 30
)

$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $PSScriptRoot
Set-Location $Repo
$Py = Join-Path $Repo "venv-cpu\Scripts\python.exe"
$PyW = Join-Path $Repo "venv-winml\Scripts\python.exe"
$Out = Join-Path $Repo "reports\r2\$RunId"
New-Item -ItemType Directory -Force -Path $Out | Out-Null

$disk = (Get-PSDrive C).Free / 1GB
if ($disk -lt $DiskFloorGB) {
  Write-Host "DISK FLOOR VIOLATION: $disk GB free < $DiskFloorGB GB; aborting GPU phases"
  exit 2
}
Write-Host "[run_r2] run_id=$RunId disk_free=$([math]::Round($disk,1))GB"

# 0) environment fingerprint
& $Py runner\r2\env_fingerprint.py --out (Join-Path $Out "env_fingerprint.json")
if ($LASTEXITCODE -ne 0) { Write-Host "WARN: env fingerprint rc=$LASTEXITCODE" }

# 1) local unit tests (CPU) -- gate everything else
& $Py -m compileall -q runner tests
if ($LASTEXITCODE -ne 0) { throw "compileall failed" }
& $Py -m unittest discover -s tests -q
if ($LASTEXITCODE -ne 0) { throw "unit tests failed" }

# 2) device identity (non-destructive host audit)
if (-not $SkipIdentity) {
  & $Py runner\r2\device_identity_supervisor.py --run-id $RunId
  if ($LASTEXITCODE -ne 0) { Write-Host "WARN: identity supervisor rc=$LASTEXITCODE" }
}

# 3) model matrix (GPU, serialized subprocesses, resume-safe)
if (-not $SkipModels) {
  $caseList = Join-Path $env:TEMP "r2_case_list_$RunId.json"
  & $Py runner\r2\build_case_list.py --out $caseList
  $extra = @()
  if ($RetryFailed) { $extra += "--retry-failed" }
  & $Py runner\r2\model_matrix_supervisor.py --run-id $RunId --case-list $caseList `
      --max-minutes $MaxMinutes @extra
}

# 4) YOLO end-to-end (GPU)
if (-not $SkipModels) {
  foreach ($ep in "AMDGPUExecutionProvider", "MIGraphXExecutionProvider") {
    $tag = $ep -replace "ExecutionProvider", ""
    & $PyW -I runner\r2\yolo_e2e_child.py --ep $ep `
        --out (Join-Path $Out "cases\yolo26n_e2e__$tag.json")
    Write-Host "[run_r2] yolo e2e $ep rc=$LASTEXITCODE"
  }
}

# 5) perf + soak for GPU-correct models only (inputs fixed in code)
if (-not $SkipPerf) {
  & $Py runner\r2\perf_soak_driver.py --run-id $RunId --max-minutes $MaxMinutes
}
if (-not $SkipSoak) {
  & $Py runner\r2\perf_soak_driver.py --run-id $RunId --soak --max-minutes $MaxMinutes
}

# 6) aggregate matrices + manifest + redaction audit
& $Py runner\r2\finalize_receipts.py --run-id $RunId
Write-Host "[run_r2] DONE run_id=$RunId"
