#requires -Version 5.1
<#
  Starts Babel's Hoard on Windows. Double-click "Iniciar Babel's Hoard.cmd"
  instead of running this directly, unless you know you want a console.
#>
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot\..

$venvPython = ".\.venv\Scripts\python.exe"

if (-not (Test-Path $venvPython)) {
    Write-Host "Creating virtual environment..."
    python -m venv .venv
}

Write-Host "Installing dependencies..."
& $venvPython -m pip install --disable-pip-version-check -q -r requirements-lock.txt

if (-not (Test-Path ".\frontend\dist\index.html")) {
    Write-Host "Building the frontend (first run only)..."
    Push-Location frontend
    npm ci
    npm run build
    Pop-Location
}

Write-Host "Starting Babel's Hoard on http://127.0.0.1:8811 ..."
& $venvPython -m babels_hoard
