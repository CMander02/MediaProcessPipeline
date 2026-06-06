<#
.SYNOPSIS
    Build and start the MPP Electron frontend from the project root.

.EXAMPLE
    .\scripts\start-electron.ps1
#>

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$WebPath = Join-Path $ProjectRoot "web"

function Write-Status { param($Message) Write-Host "[MPP] " -ForegroundColor Cyan -NoNewline; Write-Host $Message }
function Write-Success { param($Message) Write-Host "[OK] " -ForegroundColor Green -NoNewline; Write-Host $Message }

if (-not (Test-Path (Join-Path $WebPath "package.json"))) {
    throw "web/package.json not found. Run this script from a valid MediaProcessPipeline checkout."
}

Write-Status "Project root: $ProjectRoot"
Write-Status "Building and starting Electron frontend..."

Push-Location $WebPath
try {
    if (-not (Test-Path (Join-Path $WebPath "node_modules"))) {
        Write-Status "node_modules not found; running npm install first..."
        npm install
    }

    npm run electron
}
finally {
    Pop-Location
}

Write-Success "Electron exited."
