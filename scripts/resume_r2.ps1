<# R2 resume entry: continues an existing run (ledger skips DONE cases). #>
param(
  [Parameter(Mandatory = $true)][string]$RunId,
  [switch]$RetryFailed,
  [double]$MaxMinutes = 120,
  [double]$DiskFloorGB = 30
)
& (Join-Path $PSScriptRoot "run_r2.ps1") -RunId $RunId -Resume `
    -MaxMinutes $MaxMinutes -DiskFloorGB $DiskFloorGB `
    $(if ($RetryFailed) { "-RetryFailed" })
