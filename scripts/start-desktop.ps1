[CmdletBinding()]
param([switch]$Build)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path $PSScriptRoot -Parent
$DesktopDirectory = Join-Path $ProjectRoot 'desktop'

if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    throw 'Node.js 22.12+ is required to start the desktop app from source.'
}

Push-Location $DesktopDirectory
try {
    if (-not (Test-Path -LiteralPath (Join-Path $DesktopDirectory 'node_modules/electron/dist/electron.exe'))) {
        & npm ci
        if ($LASTEXITCODE -ne 0) { throw 'Desktop dependency installation failed.' }
    }
    if ($Build -or -not (Test-Path -LiteralPath (Join-Path $ProjectRoot 'web/dist/index.html'))) {
        Push-Location (Join-Path $ProjectRoot 'web')
        try {
            if (-not (Test-Path -LiteralPath 'node_modules')) {
                & npm ci
                if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
            }
            & npm run build
            if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
        } finally { Pop-Location }
    }
    # Node spawn preserves spaces/Unicode in the project path and detaches the UI
    # so the batch launcher can close its console immediately after startup.
    & node (Join-Path $DesktopDirectory 'scripts/start.cjs')
    if ($LASTEXITCODE -ne 0) { throw 'Desktop launch failed.' }
} finally { Pop-Location }
