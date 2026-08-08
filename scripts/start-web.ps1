<#
.SYNOPSIS
    Build and start the MPP Web application.

.EXAMPLE
    .\scripts\start-web.ps1

.EXAMPLE
    .\scripts\start-web.ps1 -Server

.EXAMPLE
    .\scripts\start-web.ps1 -NoBrowser -Host localhost -Port 18000
#>

[CmdletBinding()]
param(
    [switch]$Server,
    [switch]$NoBrowser,
    [Alias("Host")]
    [string]$BindAddress = "",
    [ValidateRange(1, 65535)]
    [int]$Port = 18000,
    [ValidateRange(5, 600)]
    [int]$HealthTimeoutSeconds = 60
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$BackendPath = Join-Path $ProjectRoot "backend"
$WebPath = Join-Path $ProjectRoot "web"
$DistIndex = Join-Path $WebPath "dist\index.html"
$BackendProcess = $null

function Write-MppStatus {
    param([string]$Message)
    Write-Host "[MPP] " -ForegroundColor Cyan -NoNewline
    Write-Host $Message
}

function Test-LoopbackAddress {
    param([string]$Address)
    $Normalized = $Address.Trim().ToLowerInvariant().Trim([char[]]"[]")
    return $Normalized -in @("localhost", "127.0.0.1", "::1")
}

function Test-ApiTokenConfigured {
    $ConfigPath = Join-Path $ProjectRoot "config.json"
    if (-not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) {
        return $false
    }
    try {
        $Config = Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
        return -not [string]::IsNullOrWhiteSpace([string]$Config.api_token)
    } catch {
        throw "Unable to read ${ConfigPath}: $($_.Exception.Message)"
    }
}

function Test-WebBuildCurrent {
    if (-not (Test-Path -LiteralPath $DistIndex -PathType Leaf)) {
        return $false
    }

    $BuildTime = (Get-Item -LiteralPath $DistIndex).LastWriteTimeUtc
    $Inputs = @(
        (Join-Path $WebPath "index.html"),
        (Join-Path $WebPath "package.json"),
        (Join-Path $WebPath "package-lock.json"),
        (Join-Path $WebPath "vite.config.ts")
    )
    foreach ($InputPath in $Inputs) {
        if ((Test-Path -LiteralPath $InputPath) -and (Get-Item -LiteralPath $InputPath).LastWriteTimeUtc -gt $BuildTime) {
            return $false
        }
    }

    foreach ($SourceRoot in @((Join-Path $WebPath "src"), (Join-Path $WebPath "public"))) {
        $NewerFile = Get-ChildItem -LiteralPath $SourceRoot -File -Recurse -ErrorAction SilentlyContinue |
            Where-Object { $_.LastWriteTimeUtc -gt $BuildTime } |
            Select-Object -First 1
        if ($NewerFile) {
            return $false
        }
    }
    return $true
}

function Ensure-WebBuild {
    if (Test-WebBuildCurrent) {
        Write-MppStatus "Web build is ready."
        return
    }

    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
        throw "The Web build is stale. Install Node.js and npm first."
    }

    Write-MppStatus "Building the Web frontend..."
    Push-Location $WebPath
    try {
        if (-not (Test-Path -LiteralPath (Join-Path $WebPath "node_modules") -PathType Container)) {
            & npm ci
            if ($LASTEXITCODE -ne 0) { throw "npm ci failed." }
        }
        & npm run build
        if ($LASTEXITCODE -ne 0) { throw "The production Web build failed." }
    } finally {
        Pop-Location
    }
}

function Stop-BackendTree {
    if (-not $BackendProcess) { return }
    try {
        $BackendProcess.Refresh()
        if (-not $BackendProcess.HasExited) {
            Write-MppStatus "Stopping the backend..."
            & taskkill.exe /PID $BackendProcess.Id /T /F *> $null
        }
    } catch {
        # The process may have completed between Refresh and taskkill.
    }
}

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "uv was not found. Install it from https://docs.astral.sh/uv/"
}

if ([string]::IsNullOrWhiteSpace($BindAddress)) {
    $BindAddress = if ($Server) { "0.0.0.0" } else { "127.0.0.1" }
}
if ($Server) {
    $NoBrowser = $true
}

$RemoteBind = -not (Test-LoopbackAddress $BindAddress)
if ($RemoteBind -and -not (Test-ApiTokenConfigured)) {
    throw "Server mode requires an API Token. Configure it in Settings > Access Control, then start server mode again."
}

Ensure-WebBuild

$ProbeHost = if ($BindAddress -in @("0.0.0.0", "::")) { "localhost" } else { $BindAddress.Trim([char[]]"[]") }
$ProbeAuthority = if ($ProbeHost.Contains(":")) { "[$ProbeHost]" } else { $ProbeHost }
$HealthUrl = "http://${ProbeAuthority}:$Port/health"
$OpenUrl = "http://localhost:$Port"
$Arguments = @("run", "python", "-m", "app.cli", "serve", "--host", $BindAddress, "--port", [string]$Port)

try {
    Write-MppStatus "Starting backend on $BindAddress`:$Port"
    $BackendProcess = Start-Process -FilePath "uv" -ArgumentList $Arguments -WorkingDirectory $BackendPath -NoNewWindow -PassThru

    $Deadline = [DateTime]::UtcNow.AddSeconds($HealthTimeoutSeconds)
    $Healthy = $false
    while ([DateTime]::UtcNow -lt $Deadline) {
        $BackendProcess.Refresh()
        if ($BackendProcess.HasExited) {
            throw "Backend startup failed with exit code $($BackendProcess.ExitCode)."
        }
        try {
            $Response = Invoke-WebRequest -UseBasicParsing -Uri $HealthUrl -TimeoutSec 2
            if ($Response.StatusCode -eq 200) {
                $Healthy = $true
                break
            }
        } catch {
            Start-Sleep -Milliseconds 350
        }
    }
    if (-not $Healthy) {
        throw "Backend health check timed out: $HealthUrl"
    }

    Write-MppStatus "Web is ready: $OpenUrl"
    if (-not $NoBrowser) {
        Start-Process $OpenUrl
    }
    Write-Host "Press Ctrl+C to stop the service." -ForegroundColor DarkGray

    while (-not $BackendProcess.HasExited) {
        Wait-Process -Id $BackendProcess.Id -Timeout 1 -ErrorAction SilentlyContinue
        $BackendProcess.Refresh()
    }
    exit $BackendProcess.ExitCode
} finally {
    Stop-BackendTree
}
