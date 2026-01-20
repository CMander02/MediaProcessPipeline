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

# Kill by port
foreach ($Port in @(8000, 5173)) {
    $NetStat = netstat -ano | Select-String ":$Port\s.*LISTENING"
    if ($NetStat) {
        $Pid = ($NetStat -split '\s+')[-1]
        if ($Pid -match '^\d+$') {
            Write-Status "Stopping port $Port (PID: $Pid)"
            Stop-Process -Id $Pid -Force -ErrorAction SilentlyContinue
        }
    }
}

Write-Success "Services stopped."
