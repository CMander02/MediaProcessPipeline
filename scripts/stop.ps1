<#
.SYNOPSIS
    Stop MPP services

.EXAMPLE
    .\scripts\stop.ps1
#>

$ErrorActionPreference = "SilentlyContinue"

function Write-Status { param($Message) Write-Host "[MPP] " -ForegroundColor Cyan -NoNewline; Write-Host $Message }
function Write-Success { param($Message) Write-Host "[OK] " -ForegroundColor Green -NoNewline; Write-Host $Message }

function Test-ProtectedProcess {
    param([int]$ProcessId)

    if ($ProcessId -eq $PID) {
        return $true
    }

    $ProcessInfo = Get-CimInstance -ClassName Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue
    if ($null -eq $ProcessInfo) {
        return $true
    }

    if ([string]::IsNullOrWhiteSpace($ProcessInfo.Name) -or [string]::IsNullOrWhiteSpace($ProcessInfo.CommandLine)) {
        return $true
    }

    $ProcessIdentity = @(
        $ProcessInfo.Name
        $ProcessInfo.ExecutablePath
        $ProcessInfo.CommandLine
    ) -join " "

    return $ProcessIdentity -match '(?i)(chatgpt|codex|modelcontextprotocol|mcp)'
}

Write-Status "Stopping MediaProcessPipeline services..."

# Kill by port. 18000 is the fixed backend port; 5173 is Vite; 8000 is kept
# for older dev sessions that may still be running.
foreach ($Port in @(18000, 5173, 8000)) {
    $NetStats = netstat -ano | Select-String ":$Port\s.*LISTENING"
    foreach ($Line in $NetStats) {
        $ProcessId = ($Line -split '\s+')[-1]
        if ($ProcessId -match '^\d+$') {
            $ProcessId = [int]$ProcessId
            if (Test-ProtectedProcess -ProcessId $ProcessId) {
                Write-Status "Skipping protected process on port $Port (PID: $ProcessId)"
                continue
            }

            Write-Status "Stopping port $Port (PID: $ProcessId)"
            Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
        }
    }
}

Write-Success "Services stopped."
