# status.ps1 - console view of ledger + heartbeat
param([string]$Repo = "$PSScriptRoot\..")
$Repo = (Resolve-Path $Repo).Path
$led = Join-Path $Repo 'reports\ledger.json'
$hb  = Join-Path $Repo 'reports\supervisor_heartbeat.json'
"=== heartbeat ==="
if (Test-Path $hb) { Get-Content $hb } else { "no heartbeat yet" }
"=== phase summary ==="
$py = Join-Path $Repo 'venv-winml\Scripts\python.exe'
if (-not (Test-Path $py)) { $py = 'python' }
& $py -c "import sys; sys.path.insert(0, r'$Repo\runner'); import ledger, json; l = ledger.load_ledger(r'$led'); import pprint; pprint.pprint(ledger.phase_summary(l)); print('pending:', len(ledger.pending_cases(l)))"
"=== last 10 cases ==="
& $py -c "import sys; sys.path.insert(0, r'$Repo\runner'); import ledger; l = ledger.load_ledger(r'$led'); [print(c.get('phase'), c.get('id'), c.get('status')) for c in l.get('cases', [])[-10:]]"
