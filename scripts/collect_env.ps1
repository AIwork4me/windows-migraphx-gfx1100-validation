# collect_env.ps1 - Full environment preflight collection for WIN-MIGRAPHX-GFX1100
# Writes raw evidence into reports/preflight/. Read-only collection; no system changes.
param(
    [string]$OutDir = "$PSScriptRoot\..\reports\preflight"
)
$ErrorActionPreference = 'Continue'
$OutDir = (Resolve-Path (New-Item -ItemType Directory -Force -Path $OutDir)).Path

function Save-Section {
    param([string]$Name, [scriptblock]$Body)
    $file = Join-Path $OutDir $Name
    try {
        & $Body *>&1 | Out-File -FilePath $file -Encoding utf8
    } catch {
        "EXCEPTION: $($_.Exception.Message)" | Out-File -FilePath $file -Encoding utf8
    }
    Write-Host "collected: $Name"
}

Save-Section 'os_version.txt' { Get-CimInstance Win32_OperatingSystem | Select-Object Caption,Version,BuildNumber,OSArchitecture,InstallDate,LastBootUpTime,SystemDrive | Format-List }
Save-Section 'cpu_ram.txt' {
    Get-CimInstance Win32_Processor | Select-Object Name,NumberOfCores,NumberOfLogicalProcessors,MaxClockSpeed | Format-List
    $cs = Get-CimInstance Win32_ComputerSystem
    "TotalPhysicalRAM_GB: {0:N1}" -f ($cs.TotalPhysicalMemory/1GB)
    $os = Get-CimInstance Win32_OperatingSystem
    "FreePhysicalRAM_GB: {0:N1}" -f ($os.FreePhysicalMemory/1MB)
}
Save-Section 'gpu_adapters.txt' {
    Get-CimInstance Win32_VideoController | Select-Object Name,DriverVersion,DriverDate,AdapterRAM,PNPDeviceID,Status,VideoModeDescription | Format-List
}
Save-Section 'gpu_pnp_hwid.txt' {
    # Hardware IDs give the true gfx arch (gfx1100 = 744C)
    Get-PnpDevice -Class Display -Status OK | ForEach-Object {
        $_.FriendlyName
        ($_ | Get-PnpDeviceProperty -KeyName DEVPKEY_Device_HardwareIds -ErrorAction SilentlyContinue).Data
    }
}
Save-Section 'disk_free.txt' {
    Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3' | Select-Object DeviceID,VolumeName,@{n='FreeGB';e={[math]::Round($_.FreeSpace/1GB,1)}},@{n='SizeGB';e={[math]::Round($_.Size/1GB,1)}} | Format-Table -AutoSize
}
Save-Section 'amdgpu_sw_reg.txt' {
    # AMD software registry (Adrenalin / HIP runtime shipped by driver)
    Get-ChildItem 'HKLM:\SOFTWARE\AMD' -Recurse -ErrorAction SilentlyContinue | Select-Object -First 50 Name
    Get-ItemProperty 'HKLM:\SOFTWARE\AMD\CN' -ErrorAction SilentlyContinue | Select-Object DriverVersion,SuiteVersion | Format-List
}
Save-Section 'uninstall_amd.txt' {
    Get-ItemProperty HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*, HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*, HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\* -ErrorAction SilentlyContinue |
        Where-Object { $_.DisplayName -match 'HIP|ROCm|Radeon|AMD|MIGraphX' } |
        Select-Object DisplayName,DisplayVersion,InstallLocation | Format-List
    "COUNT_CHECK_DONE"
}
Save-Section 'hip_dlls.txt' {
    Get-ChildItem C:\Windows\System32\amdhip64*.dll,C:\Windows\System32\hip*.dll -ErrorAction SilentlyContinue |
        Select-Object Name,Length,LastWriteTime,@{n='FileVersion';e={$_.VersionInfo.FileVersion}} | Format-List
    Get-ChildItem C:\Windows\System32\mig*.dll,C:\Windows\System32\MIG*.dll -ErrorAction SilentlyContinue | Select-Object Name,Length | Format-List
}
Save-Section 'toolchain.txt' {
    "=== where.exe sweep ==="
    where.exe python python3 py git gh cmake ninja cl msbuild hipcc 2>&1
    "=== versions ==="
    git --version 2>&1
    gh --version 2>&1 | Select-Object -First 1
    "=== VS/SDK presence ==="
    Test-Path 'C:\Program Files\Microsoft Visual Studio'
    Test-Path 'C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe'
    Test-Path 'C:\Program Files (x86)\Windows Kits'
    "=== winget ==="
    winget --version 2>&1
}
Save-Section 'python_search.txt' {
    "=== candidate install roots ==="
    foreach ($p in @(
        "$env:LOCALAPPDATA\Programs\Python",
        'C:\Program Files\Python312','C:\Program Files\Python311','C:\Program Files\Python313',
        'C:\Python312','C:\Python311','C:\Python313',
        "$env:USERPROFILE\miniconda3","$env:USERPROFILE\miniforge3"
    )) { if (Test-Path $p) { "EXISTS: $p"; Get-ChildItem $p -ErrorAction SilentlyContinue | Select-Object -First 10 Name } else { "MISSING: $p" } }
}
Save-Section 'windows_ml_state.txt' {
    "=== WindowsML / Windows AI features (best effort) ==="
    Get-AppxPackage *WindowsAI*,*MachineLearning* -ErrorAction SilentlyContinue | Select-Object Name,Version,PackageFullName | Format-List
    Test-Path "$env:WINDIR\System32\Windows.AI.MachineLearning.dll"
    "Get-AppxPackage done"
}
Save-Section 'powershell_dotnet.txt' {
    $PSVersionTable | Format-List
}
Save-Section 'network_github.txt' {
    "=== DNS/HTTP reachability (raw, may contain no secrets) ==="
    try { (Invoke-WebRequest -Uri 'https://api.github.com/rate_limit' -UseBasicParsing -TimeoutSec 15).StatusCode } catch { "HTTP_FAIL: $($_.Exception.Message)" }
    gh auth status 2>&1
    gh api user --jq '.login' 2>&1
}
Save-Section 'eventlog_tdr_recent.txt' {
    # TDR / driver reset events in last 14 days (finite, redacted to IDs only)
    Get-WinEvent -FilterHashtable @{LogName='System'; StartTime=(Get-Date).AddDays(-14); Id=@(4101,117,141,4099)} -MaxEvents 50 -ErrorAction SilentlyContinue |
        Select-Object TimeCreated,Id,ProviderName | Format-Table -AutoSize
    "TDR_QUERY_DONE"
}
Write-Host "PREFLIGHT_COLLECT_COMPLETE: $OutDir"
