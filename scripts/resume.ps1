# resume.ps1 - continue from ledger (idempotent: skips terminal cases)
param([string]$Repo = "$PSScriptRoot\..")
$Repo = (Resolve-Path $Repo).Path
$py = Join-Path $Repo 'venv-winml\Scripts\python.exe'
if (-not (Test-Path $py)) { $py = Join-Path $Repo 'venv-cpu\Scripts\python.exe' }
& $py (Join-Path $Repo 'runner\supervisor.py') --repo $Repo --resume-only
exit $LASTEXITCODE
