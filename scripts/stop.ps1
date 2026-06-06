<#
.SYNOPSIS
    Stop MPP services

.EXAMPLE
    .\scripts\stop.ps1
#>

$ErrorActionPreference = "SilentlyContinue"

function Write-Status { param($Message) Write-Host "[MPP] " -ForegroundColor Cyan -NoNewline; Write-Host $Message }
function Write-Success { param($Message) Write-Host "[OK] " -ForegroundColor Green -NoNewline; Write-Host $Message }

Write-Status "Stopping MediaProcessPipeline services..."

# Kill by port. 18000 is the fixed backend port; 5173 is Vite; 8000 is kept
# for older dev sessions that may still be running.
foreach ($Port in @(18000, 5173, 8000)) {
    $NetStats = netstat -ano | Select-String ":$Port\s.*LISTENING"
    foreach ($Line in $NetStats) {
        $Pid = ($Line -split '\s+')[-1]
        if ($Pid -match '^\d+$') {
            Write-Status "Stopping port $Port (PID: $Pid)"
            Stop-Process -Id ([int]$Pid) -Force -ErrorAction SilentlyContinue
        }
    }
}

Write-Success "Services stopped."
