# PowerShell setup script for the project

$ErrorActionPreference = "Stop"

# Get project root (parent of script directory)
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $ProjectRoot

Write-Host "Setting up project..." -ForegroundColor Green
Write-Host "Project root: $ProjectRoot" -ForegroundColor Gray

# Check prerequisites
Write-Host "`nChecking prerequisites..." -ForegroundColor Yellow

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "Error: uv is not installed. Install it from https://docs.astral.sh/uv/" -ForegroundColor Red
    exit 1
}

if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    Write-Host "Error: Node.js is not installed." -ForegroundColor Red
    exit 1
}

Write-Host "Prerequisites OK" -ForegroundColor Green

# Setup backend
Write-Host "`nSetting up backend..." -ForegroundColor Yellow
Push-Location backend

if (-not (Test-Path .env)) {
    Copy-Item .env.example .env
    Write-Host "Created .env file - please update with your API keys" -ForegroundColor Yellow
}

uv sync
Write-Host "Backend dependencies installed" -ForegroundColor Green
Pop-Location

# Setup frontend
Write-Host "`nSetting up frontend..." -ForegroundColor Yellow
Push-Location frontend
npm install
Write-Host "Frontend dependencies installed" -ForegroundColor Green
Pop-Location

Pop-Location  # Return to original directory

Write-Host "`nSetup complete!" -ForegroundColor Green
Write-Host "1. Update backend/.env with your API keys" -ForegroundColor Yellow
Write-Host "2. Run .\script\dev.ps1 to start development servers" -ForegroundColor Yellow
