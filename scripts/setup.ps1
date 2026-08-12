<#
.SYNOPSIS
    Initialize MPP development environment

.PARAMETER Extra
    Optional backend dependency extra, e.g. asr-api-vad, local-asr, uvr,
    hf-local-inference, local-models.

.EXAMPLE
    .\scripts\setup.ps1

.EXAMPLE
    .\scripts\setup.ps1 -Extra local-models
#>

param(
    [string]$Extra = "",
    [switch]$InstallSherpaModels,
    [string]$SherpaModelRoot = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot

function Write-Status { param($Message) Write-Host "[MPP] " -ForegroundColor Cyan -NoNewline; Write-Host $Message }
function Write-Success { param($Message) Write-Host "[OK] " -ForegroundColor Green -NoNewline; Write-Host $Message }

Write-Status "Setting up MediaProcessPipeline..."

# Create data directories
foreach ($Dir in @("data/inbox", "data/processing", "data/outputs", "data/archive")) {
    $Path = Join-Path $ProjectRoot $Dir
    if (-not (Test-Path $Path)) {
        New-Item -ItemType Directory -Path $Path -Force | Out-Null
        Write-Status "Created: $Dir"
    }
}

# Setup backend
$BackendPath = Join-Path $ProjectRoot "backend"
Write-Status "Installing backend dependencies..."
Push-Location $ProjectRoot
if ($Extra) {
    uv sync --extra $Extra
} else {
    uv sync
}
Pop-Location
Write-Success "Backend ready"

if (($Extra -in @("local-asr", "local-models")) -and (Get-Command nvidia-smi -ErrorAction SilentlyContinue)) {
    Write-Status "Installing sherpa-onnx CUDA 12 runtime..."
    Push-Location $BackendPath
    uv pip install "sherpa-onnx==1.13.4+cuda12.cudnn9" -f https://k2-fsa.github.io/sherpa/onnx/cuda.html
    Pop-Location
    Write-Success "sherpa-onnx CUDA runtime ready"
}

if ($InstallSherpaModels) {
    Write-Status "Installing default sherpa-onnx model set..."
    Push-Location $ProjectRoot
    if ($SherpaModelRoot) {
        uv run python scripts/install_sherpa_models.py --model-root $SherpaModelRoot
    } else {
        uv run python scripts/install_sherpa_models.py
    }
    Pop-Location
    Write-Success "Sherpa model set ready"
}

# Setup frontend
$FrontendPath = Join-Path $ProjectRoot "web"
if (Test-Path (Join-Path $FrontendPath "package.json")) {
    Write-Status "Installing frontend dependencies..."
    Push-Location $FrontendPath
    npm install
    Pop-Location
    Write-Success "Frontend ready"
}

# Copy .env.example
$EnvExample = Join-Path $BackendPath ".env.example"
$EnvFile = Join-Path $BackendPath ".env"
if ((Test-Path $EnvExample) -and -not (Test-Path $EnvFile)) {
    Copy-Item $EnvExample $EnvFile
    Write-Status "Created .env from .env.example"
}

Write-Host ""
Write-Success "Setup complete!"
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Yellow
Write-Host "  1. Edit backend/.env with your API keys"
Write-Host "  2. Run .\scripts\dev.ps1 to start"
