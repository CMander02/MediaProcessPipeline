param(
    [switch]$Debug,
    [switch]$NoBundle
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$WebRoot = Join-Path $ProjectRoot "web"
$Profile = if ($Debug) { "debug" } else { "release" }
$SourceExe = Join-Path $WebRoot "src-tauri\target\$Profile\mpp-desktop.exe"
$DestinationExe = Join-Path $ProjectRoot "MPP.exe"
$TemporaryExe = Join-Path $ProjectRoot "MPP.exe.next"

$TauriArgs = @("tauri", "build")
if ($Debug) { $TauriArgs += "--debug" }
if ($NoBundle) { $TauriArgs += "--no-bundle" }

Push-Location $WebRoot
try {
    & npx @TauriArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Tauri build failed with exit code $LASTEXITCODE"
    }
} finally {
    Pop-Location
}

if (-not (Test-Path -LiteralPath $SourceExe)) {
    throw "Built executable was not found: $SourceExe"
}

Copy-Item -LiteralPath $SourceExe -Destination $TemporaryExe -Force

# Windows locks a running executable. Close only the root MPP launcher so the
# freshly built portable binary can take its stable project-root path.
$DestinationFullPath = [System.IO.Path]::GetFullPath($DestinationExe)
$RunningLaunchers = Get-CimInstance -ClassName Win32_Process -ErrorAction SilentlyContinue |
    Where-Object {
        $_.Name -eq "MPP.exe" -and
        $_.ExecutablePath -and
        [System.IO.Path]::GetFullPath($_.ExecutablePath) -eq $DestinationFullPath
    }
foreach ($Launcher in $RunningLaunchers) {
    Stop-Process -Id $Launcher.ProcessId -Force -ErrorAction Stop
    Wait-Process -Id $Launcher.ProcessId -Timeout 10 -ErrorAction SilentlyContinue
}

if (Test-Path -LiteralPath $DestinationExe) {
    Remove-Item -LiteralPath $DestinationExe -Force
}
Move-Item -LiteralPath $TemporaryExe -Destination $DestinationExe

$File = Get-Item -LiteralPath $DestinationExe
Write-Host "[OK] Root executable updated: $($File.FullName)"
Write-Host "[OK] Size: $($File.Length) bytes"
