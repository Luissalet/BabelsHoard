#requires -Version 5.1
<#
  Starts Babel's Hoard. Double-click "Iniciar Babel's Hoard.cmd" instead of
  running this directly, unless you want to pass options.

  First run: creates .venv with Python 3.11+ (3.13 preferred), installs
  requirements-lock.txt, builds the web UI if Node.js is available, then
  starts the app with the repository root as working directory (Faustus
  reads faustus-plugin.json from there) and waits for /api/health.
  Later runs only reinstall when requirements-lock.txt changed.

  Options: -Port 8811  -Demo  -NoBrowser
#>
param(
    [int]$Port = 8811,
    [switch]$Demo,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location -LiteralPath $Root
$OnWindows = ($env:OS -eq "Windows_NT")
$AppUrl = "http://127.0.0.1:$Port"

function Write-Step([string]$Message) { Write-Host "[Babel's Hoard] $Message" }

function Invoke-Checked([string]$What, [scriptblock]$Command) {
    & $Command
    if ($LASTEXITCODE -ne 0) { throw "$What failed (exit code $LASTEXITCODE)." }
}

function Test-Health {
    try {
        $h = Invoke-RestMethod -Uri "$AppUrl/api/health" -TimeoutSec 2
        return ($h.service -eq "babels-hoard")
    } catch { return $false }
}

function Find-BasePython {
    # Returns @(exe, extra-args) for a Python >= 3.11, preferring 3.13.
    $candidates = @()
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $candidates += , @("py", @("-3.13")); $candidates += , @("py", @("-3"))
    }
    foreach ($p in @("C:\Python313\python.exe", "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe")) {
        if ($p -and (Test-Path -LiteralPath $p)) { $candidates += , @($p, @()) }
    }
    foreach ($name in @("python", "python3")) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd) { $candidates += , @($cmd.Source, @()) }
    }
    foreach ($c in $candidates) {
        try {
            $ver = & $c[0] @($c[1]) -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
            if ($LASTEXITCODE -eq 0 -and $ver -and ([version]$ver -ge [version]"3.11")) { return $c }
        } catch { }
    }
    return $null
}

if (Test-Health) {
    Write-Step "already running at $AppUrl"
    if (-not $NoBrowser) { Start-Process $AppUrl }
    exit 0
}

if ($OnWindows) { $VenvPython = Join-Path $Root ".venv\Scripts\python.exe" }
else { $VenvPython = Join-Path $Root ".venv/bin/python" }

if (-not (Test-Path -LiteralPath $VenvPython)) {
    $base = Find-BasePython
    if (-not $base) { throw "Python 3.11 or newer was not found. Install Python 3.13 from python.org and run this again." }
    Write-Step "creating the virtual environment with $($base[0]) $($base[1] -join ' ')"
    Invoke-Checked "Creating .venv" { & $base[0] @($base[1]) -m venv (Join-Path $Root ".venv") }
}

# Install the lock only when it changed since the last successful install.
$Lock = Join-Path $Root "requirements-lock.txt"
$Stamp = Join-Path (Join-Path $Root ".venv") "babel-lock.sha256"
$LockHash = (Get-FileHash -LiteralPath $Lock -Algorithm SHA256).Hash
$Installed = ""
if (Test-Path -LiteralPath $Stamp) { $Installed = (Get-Content -LiteralPath $Stamp -Raw).Trim() }
if ($Installed -ne $LockHash) {
    Write-Step "installing dependencies from requirements-lock.txt"
    Invoke-Checked "pip install" { & $VenvPython -m pip install --disable-pip-version-check -q -r $Lock }
    Set-Content -LiteralPath $Stamp -Value $LockHash -Encoding ascii
}

# Web UI (optional: the API and MCP tools work without it).
if (-not (Test-Path -LiteralPath (Join-Path $Root "frontend\dist\index.html"))) {
    if (Get-Command npm -ErrorAction SilentlyContinue) {
        Write-Step "building the web interface (first run only)"
        Push-Location -LiteralPath (Join-Path $Root "frontend")
        try {
            Invoke-Checked "npm ci" { npm ci --no-audit --no-fund }
            Invoke-Checked "npm run build" { npm run build }
        } finally { Pop-Location }
    } else {
        Write-Step "Node.js/npm not found: starting without the web interface (the assistant tools still work)"
    }
}

# TypeScript compiler API for indexing node_modules (optional).
$Probes = Join-Path $Root "babels_hoard\probes"
if ((Get-Command npm -ErrorAction SilentlyContinue) -and -not (Test-Path -LiteralPath (Join-Path $Probes "node_modules\typescript"))) {
    Write-Step "installing the TypeScript probe"
    Push-Location -LiteralPath $Probes
    try { npm ci --no-audit --no-fund | Out-Null } catch { Write-Step "TypeScript probe not installed: $_" } finally { Pop-Location }
}

$ArgList = @("-m", "babels_hoard", "--no-browser", "--port", "$Port")
if ($Demo) { $ArgList += "--demo" }
Write-Step "starting on $AppUrl"
$proc = Start-Process -FilePath $VenvPython -ArgumentList $ArgList -WorkingDirectory $Root -NoNewWindow -PassThru

$ready = $false
for ($i = 0; $i -lt 120; $i++) {
    if ($proc.HasExited) { throw "Babel's Hoard exited during start-up (exit code $($proc.ExitCode)). See data\logs\app.log." }
    if (Test-Health) { $ready = $true; break }
    Start-Sleep -Milliseconds 500
}
if (-not $ready) { throw "Babel's Hoard did not answer $AppUrl/api/health within 60 seconds." }
Write-Step "ready at $AppUrl (close this window or run 'Detener Babel's Hoard.cmd' to stop)"
if (-not $NoBrowser) { Start-Process $AppUrl }
Wait-Process -Id $proc.Id
