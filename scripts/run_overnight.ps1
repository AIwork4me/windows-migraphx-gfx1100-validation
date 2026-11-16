# run_overnight.ps1 - one-shot persistent supervisor launcher
# Launches runner/supervisor.py detached from the calling session (survives parent
# exit), records PID, and verifies heartbeat. No admin rights required.
param(
    [string]$Repo = "$PSScriptRoot\..",
    [datetime]$DeadlineLocal = [datetime]::Parse("2026-11-17 08:30:00"),
    [int]$MaxDownloadGB = 15,
    [int]$MaxDiskGB = 35,
    [switch]$SkipBuild,
    [string]$PythonExe = ""
)
$ErrorActionPreference = 'Stop'
$Repo = (Resolve-Path $Repo).Path
if (-not $PythonExe) {
    $PythonExe = Join-Path $Repo 'venv-winml\Scripts\python.exe'
}
if (-not (Test-Path $PythonExe)) { throw "python not found: $PythonExe (run setup-winml.ps1 first)" }

$sup = Join-Path $Repo 'runner\supervisor.py'
$hb  = Join-Path $Repo 'reports\supervisor_heartbeat.json'
$arg = "`"$sup`" --repo `"$Repo`" --deadline `"$($DeadlineLocal.ToString('yyyy-MM-ddTHH:mm:ss'))`" --max-download-gb $MaxDownloadGB --max-disk-gb $MaxDiskGB"
if ($SkipBuild) { $arg += " --skip-build" }

# Detached via WMI Win32_Process.Create: not a child of this console, survives exit.
$cmd = "`"$PythonExe`" $arg"
$proc = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $cmd; CurrentDirectory = $Repo }
if ($proc.ReturnValue -ne 0) { throw "Win32_Process.Create failed: $($proc.ReturnValue)" }
$pid2 = $proc.ProcessId
"SUPERVISOR_PID=$pid2"
Start-Sleep -Seconds 5
$alive = Get-Process -Id $pid2 -ErrorAction SilentlyContinue
"ALIVE_AFTER_5S=$([bool]$alive)"
# wait for first heartbeat
$ok = $false
foreach ($i in 1..12) {
    Start-Sleep -Seconds 5
    if (Test-Path $hb) { $ok = $true; break }
}
"HEARTBEAT_SEEN=$ok"
if ($ok) { Get-Content $hb }
