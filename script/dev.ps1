# PowerShell script to start both backend and frontend

# Get project root (parent of script directory)
$ProjectRoot = Split-Path -Parent $PSScriptRoot

Write-Host "Starting development servers..." -ForegroundColor Green
Write-Host "Project root: $ProjectRoot" -ForegroundColor Gray

# Check if directories exist
if (-not (Test-Path "$ProjectRoot\backend") -or -not (Test-Path "$ProjectRoot\frontend")) {
    Write-Host "Error: backend or frontend directory not found" -ForegroundColor Red
    exit 1
}

# Start backend in new window
Write-Host "Starting backend server..." -ForegroundColor Green
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$ProjectRoot\backend'; uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000"

# Start frontend in new window
Write-Host "Starting frontend server..." -ForegroundColor Green
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$ProjectRoot\frontend'; npm run dev"

Write-Host ""
Write-Host "Servers started in separate windows!" -ForegroundColor Green
Write-Host "Backend:  http://localhost:8000"
Write-Host "Frontend: http://localhost:5173"
