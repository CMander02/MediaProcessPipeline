param(
    [Parameter(Position = 0)]
    [ValidateSet("sync", "debug", "apk", "aab", "check", "open")]
    [string]$Command = "check"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$webRoot = Join-Path $projectRoot "web"

Push-Location $webRoot
try {
    & npm run "android:$Command"
    if ($LASTEXITCODE -ne 0) {
        throw "Android $Command 执行失败，退出码：$LASTEXITCODE"
    }
} finally {
    Pop-Location
}
