# reproduce_failure.ps1 - one-shot reproducer for the two key real-machine findings
# 1) AMD EP HIP device-enumeration failure (driver-gated)
# 2) #151 silent CPU fallback with classic providers list
param([string]$Repo = "$PSScriptRoot\..")
$Repo = (Resolve-Path $Repo).Path
$py = Join-Path $Repo 'venv-winml\Scripts\python.exe'

"=== [1] AMD EP end-to-end (installs EP package if missing, then hits HIP 101 on driver 32.0.21030.2001) ==="
& $py (Join-Path $Repo 'runner\phases\m0_ep_discovery.py') --repo $Repo --ep AMDGPUExecutionProvider --models identity_add --batches 1 --download yes
"exit=$LASTEXITCODE"

"=== [2] #151 silent CPU fallback probe ==="
& $py (Join-Path $Repo 'runner\phases\m5_diagnostics.py') --repo $Repo
"exit=$LASTEXITCODE"

"Receipts: $Repo\reports\m0\ , $Repo\reports\m5\"
