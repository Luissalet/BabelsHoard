#requires -Version 5.1
<#
  Stops Babel's Hoard by finding the python.exe process running our own
  virtual environment and bound to the app's default port. Never kills
  python.exe by name alone - only the one launched from this repo's .venv.
#>
$ErrorActionPreference = "SilentlyContinue"
Set-Location -Path $PSScriptRoot\..
$venvPython = (Resolve-Path ".\.venv\Scripts\python.exe").Path

Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" | Where-Object {
    $_.ExecutablePath -eq $venvPython -and $_.CommandLine -match "babels_hoard"
} | ForEach-Object {
    Write-Host "Stopping Babel's Hoard (pid $($_.ProcessId))..."
    Stop-Process -Id $_.ProcessId -Force
}
