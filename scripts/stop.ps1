#requires -Version 5.1
<#
  Stops Babel's Hoard. Finds the process that listens on the app's port and
  answers /api/health as "babels-hoard", plus any python process started
  from this repository with "-m babels_hoard" (on Windows the .venv
  python.exe is a launcher that runs the real interpreter as a child, so
  both are stopped). Never stops python.exe by name alone.

  Options: -Port 8811
#>
param([int]$Port = 8811)

$ErrorActionPreference = "Continue"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$targets = @{}

$isBabel = $false
try {
    $h = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/health" -TimeoutSec 2
    $isBabel = ($h.service -eq "babels-hoard")
} catch { }

if ($isBabel -and (Get-Command Get-NetTCPConnection -ErrorAction SilentlyContinue)) {
    Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        ForEach-Object { $targets[[int]$_.OwningProcess] = "listening on port $Port" }
}

if (Get-Command Get-CimInstance -ErrorAction SilentlyContinue) {
    Get-CimInstance Win32_Process -Filter "Name LIKE 'python%'" -ErrorAction SilentlyContinue | Where-Object {
        $_.CommandLine -and $_.CommandLine -match "-m\s+babels_hoard" -and (
            ($_.ExecutablePath -and $_.ExecutablePath.StartsWith($Root, [System.StringComparison]::OrdinalIgnoreCase)) -or
            ($targets.ContainsKey([int]$_.ProcessId)) -or
            ($targets.ContainsKey([int]$_.ParentProcessId))
        )
    } | ForEach-Object { $targets[[int]$_.ProcessId] = "started from $Root" }
}

if ($targets.Count -eq 0) {
    Write-Host "[Babel's Hoard] not running."
    exit 0
}
foreach ($id in $targets.Keys) {
    Write-Host "[Babel's Hoard] stopping pid $id ($($targets[$id]))"
    Stop-Process -Id $id -Force -ErrorAction SilentlyContinue
}
