<#
.SYNOPSIS
    Start MPP development servers

.EXAMPLE
    .\scripts\dev.ps1
#>

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot

function Write-Status { param($Message) Write-Host "[MPP] " -ForegroundColor Cyan -NoNewline; Write-Host $Message }
function Write-Success { param($Message) Write-Host "[OK] " -ForegroundColor Green -NoNewline; Write-Host $Message }

Write-Status "Starting MediaProcessPipeline..."
Write-Status "Project root: $ProjectRoot"

# Start backend
$BackendPath = Join-Path $ProjectRoot "backend"
Write-Status "Starting backend on port 8000..."
Start-Process pwsh -ArgumentList "-NoExit", "-Command", "cd '$BackendPath'; uv run uvicorn app.main:app --reload --port 8000" -WindowStyle Normal

Start-Sleep -Milliseconds 500

# Start frontend
$FrontendPath = Join-Path $ProjectRoot "frontend"
if (Test-Path $FrontendPath) {
    Write-Status "Starting frontend on port 5173..."
    Start-Process pwsh -ArgumentList "-NoExit", "-Command", "cd '$FrontendPath'; npm run dev" -WindowStyle Normal
}

Write-Success "Services started!"
Write-Host ""
Write-Host "Endpoints:" -ForegroundColor Yellow
Write-Host "  Backend:  http://localhost:8000"
Write-Host "  Frontend: http://localhost:5173"
Write-Host "  API Docs: http://localhost:8000/docs"
Write-Host ""
Write-Host "Use .\scripts\stop.ps1 to stop" -ForegroundColor Gray
