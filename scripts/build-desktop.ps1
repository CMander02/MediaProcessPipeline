param(
    [switch]$Debug,
    [switch]$NoBundle,
    [ValidateSet("All", "Prepare", "Build")]
    [string]$Phase = "All",
    [switch]$Ci
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$WebRoot = Join-Path $ProjectRoot "web"
$TauriRoot = Join-Path $WebRoot "src-tauri"
$RuntimeRoot = Join-Path $TauriRoot "resources\runtime"
$Profile = if ($Debug) { "debug" } else { "release" }
$SourceExe = Join-Path $TauriRoot "target\$Profile\mpp-desktop.exe"
$DestinationExe = Join-Path $ProjectRoot "MPP.exe"
$TemporaryExe = Join-Path $ProjectRoot "MPP.exe.next"
$AttestationFile = Join-Path $ProjectRoot "MPP.exe.attestation.json"
$ReleaseLockPath = Join-Path $TauriRoot "resources\.release-build.lock"
$PrepareStampPath = Join-Path $WebRoot "node_modules\.mpp-release-prepare.json"
$RuntimeManifestPath = Join-Path $RuntimeRoot "runtime-manifest.json"
$VerifierPath = Join-Path $ProjectRoot "scripts\check-desktop-runtime.py"
$BuildToolContractPath = Join-Path $ProjectRoot "packaging\desktop-build-tools.json"
$TauriConfig = '{"build":{"beforeBuildCommand":""}}'

function Invoke-NativeText {
    param(
        [Parameter(Mandatory = $true)][string]$Command,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [string]$WorkingDirectory = $ProjectRoot
    )

    Push-Location $WorkingDirectory
    try {
        $Output = & $Command @Arguments
        if ($LASTEXITCODE -ne 0) {
            throw "$Command $($Arguments -join ' ') failed with exit code $LASTEXITCODE"
        }
    } finally {
        Pop-Location
    }
    return (($Output | ForEach-Object { "$_" }) -join "`n").Trim()
}

function Invoke-GitText {
    param([string[]]$Arguments)
    return Invoke-NativeText -Command "git" -Arguments $Arguments
}

function Assert-ReleaseGitEnvironment {
    $GitOverrideNames = @(
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_ATTR_NOSYSTEM",
        "GIT_CEILING_DIRECTORIES",
        "GIT_COMMON_DIR",
        "GIT_DIR",
        "GIT_EXEC_PATH",
        "GIT_GLOB_PATHSPECS",
        "GIT_ICASE_PATHSPECS",
        "GIT_INDEX_FILE",
        "GIT_LITERAL_PATHSPECS",
        "GIT_NAMESPACE",
        "GIT_NOGLOB_PATHSPECS",
        "GIT_OBJECT_DIRECTORY",
        "GIT_PREFIX",
        "GIT_REPLACE_REF_BASE",
        "GIT_SHALLOW_FILE",
        "GIT_WORK_TREE"
    )
    $ConfiguredGitOverrides = @(
        $GitOverrideNames |
            Where-Object { [Environment]::GetEnvironmentVariable($_) -ne $null }
        Get-ChildItem Env: |
            Where-Object { $_.Name.StartsWith("GIT_CONFIG_") } |
            ForEach-Object { $_.Name }
    ) | Sort-Object -Unique
    if ($ConfiguredGitOverrides.Count -gt 0) {
        throw (
            "Release source identity requires the repository's own Git metadata. " +
            "Clear these Git override variables: " +
            ($ConfiguredGitOverrides -join ", ")
        )
    }
}

function Get-PythonPath {
    $PythonOutput = Invoke-NativeText -Command "uv" -Arguments @("python", "find")
    $PythonLines = @(
        $PythonOutput -split "`n" |
            ForEach-Object { $_.Trim() } |
            Where-Object { $_ }
    )
    if ($PythonLines.Count -eq 0) {
        throw "uv returned no Python interpreter for release attestation"
    }
    $PythonPath = $PythonLines[-1]
    $PythonVersion = Invoke-NativeText -Command $PythonPath -Arguments @(
        "-I",
        "-S",
        "-B",
        "-c",
        "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
    )
    if ($PythonVersion -notin @("3.11", "3.12")) {
        throw "Desktop release attestation requires Python 3.11 or 3.12; received $PythonVersion"
    }
    return $PythonPath
}

function Assert-DesktopBuildTools {
    if (-not (Test-Path -LiteralPath $BuildToolContractPath -PathType Leaf)) {
        throw "Desktop build tool contract is missing: $BuildToolContractPath"
    }
    $Contract = Get-Content -LiteralPath $BuildToolContractPath -Raw |
        ConvertFrom-Json
    if ($Contract.schema -ne 1) {
        throw "Desktop build tool contract schema must be 1"
    }

    $NodeVersion = (Invoke-NativeText -Command "node" -Arguments @("--version")).TrimStart("v")
    $NodeMajor = [int]($NodeVersion.Split(".")[0])
    if ($NodeMajor -ne [int]$Contract.nodeMajor) {
        throw "Node major version must be $($Contract.nodeMajor); received $NodeVersion"
    }

    $NpmVersion = Invoke-NativeText -Command "npm" -Arguments @("--version")
    $NpmMajor = [int]($NpmVersion.Split(".")[0])
    if ($NpmMajor -ne [int]$Contract.npmMajor) {
        throw "npm major version must be $($Contract.npmMajor); received $NpmVersion"
    }

    $UvOutput = Invoke-NativeText -Command "uv" -Arguments @("--version")
    $UvVersion = [regex]::Match($UvOutput, "^uv\s+([^\s]+)").Groups[1].Value
    if ($UvVersion -ne [string]$Contract.uv) {
        throw "uv version must be $($Contract.uv); received $UvOutput"
    }

    $RustOutput = Invoke-NativeText -Command "rustc" -Arguments @("--version")
    $RustVersion = [regex]::Match($RustOutput, "^rustc\s+([^\s]+)").Groups[1].Value
    if ($RustVersion -ne [string]$Contract.rust) {
        throw "rustc version must be $($Contract.rust); received $RustOutput"
    }

    $CargoOutput = Invoke-NativeText -Command "cargo" -Arguments @("--version")
    $CargoVersion = [regex]::Match($CargoOutput, "^cargo\s+([^\s]+)").Groups[1].Value
    if ($CargoVersion -ne [string]$Contract.rust) {
        throw "Cargo version must be $($Contract.rust); received $CargoOutput"
    }

    $PackageLock = Get-Content -LiteralPath (Join-Path $WebRoot "package-lock.json") -Raw |
        ConvertFrom-Json
    $LockedTauriVersion = $PackageLock.packages.'node_modules/@tauri-apps/cli'.version
    if ($LockedTauriVersion -ne [string]$Contract.tauriCli) {
        throw (
            "package-lock.json must pin @tauri-apps/cli $($Contract.tauriCli); " +
            "received $LockedTauriVersion"
        )
    }
    return $Contract
}

function Assert-InstalledTauriCli {
    param([Parameter(Mandatory = $true)]$BuildToolContract)

    $TauriVersionOutput = Invoke-NativeText -Command "npx" -Arguments @(
        "--no-install",
        "tauri",
        "--version"
    ) -WorkingDirectory $WebRoot
    $TauriVersion = [regex]::Match(
        $TauriVersionOutput,
        "tauri-cli\s+([^\s]+)"
    ).Groups[1].Value
    if (-not $TauriVersion) {
        $TauriVersion = $TauriVersionOutput.Trim()
    }
    if ($TauriVersion -ne [string]$BuildToolContract.tauriCli) {
        throw (
            "Installed Tauri CLI must be $($BuildToolContract.tauriCli); " +
            "received $TauriVersionOutput"
        )
    }
}

function Acquire-ReleaseLock {
    $LockParent = Split-Path -Parent $ReleaseLockPath
    if (-not (Test-Path -LiteralPath $LockParent -PathType Container)) {
        throw "Release lock parent is missing: $LockParent"
    }
    try {
        $Stream = [System.IO.File]::Open(
            $ReleaseLockPath,
            [System.IO.FileMode]::CreateNew,
            [System.IO.FileAccess]::Write,
            [System.IO.FileShare]::None
        )
    } catch [System.IO.IOException] {
        throw (
            "Desktop release build is already locked: $ReleaseLockPath. " +
            "After confirming no build is active, remove the stale lock."
        )
    }
    $Payload = [System.Text.Encoding]::UTF8.GetBytes(
        "{`"pid`":$PID,`"startedAt`":`"$([DateTime]::UtcNow.ToString("O"))`"}`n"
    )
    $Stream.Write($Payload, 0, $Payload.Length)
    $Stream.Flush($true)
    return $Stream
}

function Get-ReleaseSourceIdentity {
    param(
        [Parameter(Mandatory = $true)][string]$PythonPath,
        [Parameter(Mandatory = $true)][string]$SourceCommit
    )

    $Output = Invoke-NativeText -Command $PythonPath -Arguments @(
        "-I",
        "-S",
        "-B",
        $VerifierPath,
        "--build-input-digest-only",
        "--expected-source-commit",
        $SourceCommit,
        "--json"
    )
    $Identity = $Output | ConvertFrom-Json
    if (-not $Identity.ok) {
        throw (
            "Release source attestation failed: " +
            (($Identity.errors | ForEach-Object { "$_" }) -join "; ")
        )
    }
    if (
        $Identity.sourceTree -notmatch "^[0-9a-f]{40,64}$" -or
        $Identity.buildInputDigest -notmatch "^[0-9a-f]{64}$"
    ) {
        throw "Release source attestation returned an invalid tree or digest"
    }
    return $Identity
}

function Invoke-RuntimeVerification {
    param(
        [Parameter(Mandatory = $true)][string]$PythonPath,
        [Parameter(Mandatory = $true)][string]$SourceCommit,
        [Parameter(Mandatory = $true)][string]$Version,
        [Parameter(Mandatory = $true)][string]$BuildInputDigest
    )

    [void](Invoke-NativeText -Command $PythonPath -Arguments @(
        "-I",
        "-S",
        "-B",
        $VerifierPath,
        $RuntimeRoot,
        "--verify-tools",
        "--expected-source-commit",
        $SourceCommit,
        "--expected-app-version",
        $Version,
        "--expected-build-input-digest",
        $BuildInputDigest,
        "--require-clean-source"
    ))
}

function Test-FileContainsAscii {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Value
    )

    $Haystack = [System.IO.File]::ReadAllBytes($Path)
    $Needle = [System.Text.Encoding]::ASCII.GetBytes($Value)
    if ($Needle.Length -eq 0 -or $Haystack.Length -lt $Needle.Length) {
        return $false
    }
    for ($Index = 0; $Index -le $Haystack.Length - $Needle.Length; $Index++) {
        $Matched = $true
        for ($Offset = 0; $Offset -lt $Needle.Length; $Offset++) {
            if ($Haystack[$Index + $Offset] -ne $Needle[$Offset]) {
                $Matched = $false
                break
            }
        }
        if ($Matched) {
            return $true
        }
    }
    return $false
}

function Write-PrepareStamp {
    param(
        [Parameter(Mandatory = $true)][string]$SourceCommit,
        [Parameter(Mandatory = $true)][string]$BuildInputDigest
    )

    $Stamp = [ordered]@{
        schema = 1
        sourceCommit = $SourceCommit
        buildInputDigest = $BuildInputDigest
        packageLockSha256 = (
            Get-FileHash -LiteralPath (Join-Path $WebRoot "package-lock.json") -Algorithm SHA256
        ).Hash.ToLowerInvariant()
    }
    $Json = $Stamp | ConvertTo-Json -Depth 3
    [System.IO.File]::WriteAllText(
        $PrepareStampPath,
        "$Json`n",
        [System.Text.UTF8Encoding]::new($false)
    )
}

function Assert-PrepareStamp {
    param(
        [Parameter(Mandatory = $true)][string]$SourceCommit,
        [Parameter(Mandatory = $true)][string]$BuildInputDigest
    )

    if (-not (Test-Path -LiteralPath $PrepareStampPath -PathType Leaf)) {
        throw "Desktop online prepare stamp is missing; run -Phase Prepare or -Phase All"
    }
    $Stamp = Get-Content -LiteralPath $PrepareStampPath -Raw | ConvertFrom-Json
    $PackageLockSha256 = (
        Get-FileHash -LiteralPath (Join-Path $WebRoot "package-lock.json") -Algorithm SHA256
    ).Hash.ToLowerInvariant()
    if (
        $Stamp.schema -ne 1 -or
        $Stamp.sourceCommit -ne $SourceCommit -or
        $Stamp.buildInputDigest -ne $BuildInputDigest -or
        $Stamp.packageLockSha256 -ne $PackageLockSha256
    ) {
        throw "Desktop online prepare stamp differs from the current release inputs"
    }
}

function Install-RootExecutable {
    if (-not (Test-Path -LiteralPath $SourceExe -PathType Leaf)) {
        throw "Built executable was not found: $SourceExe"
    }
    Copy-Item -LiteralPath $SourceExe -Destination $TemporaryExe -Force

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
}

$ReleaseLock = $null
try {
    $BuildToolContract = Assert-DesktopBuildTools
    $PythonPath = Get-PythonPath
    $env:MPP_BUILD_PYTHON = $PythonPath

    if (-not $Debug) {
        Assert-ReleaseGitEnvironment
    }
    $SourceCommit = Invoke-GitText @("rev-parse", "HEAD")
    if ($SourceCommit -notmatch "^[0-9a-f]{40}$") {
        throw "Git returned an invalid release source commit: $SourceCommit"
    }
    foreach ($Name in @("MPP_SOURCE_COMMIT", "GITHUB_SHA")) {
        $ConfiguredCommit = [Environment]::GetEnvironmentVariable($Name)
        if (
            $ConfiguredCommit -and
            $ConfiguredCommit.Trim().ToLowerInvariant() -ne $SourceCommit
        ) {
            throw "$Name differs from repository HEAD $SourceCommit"
        }
    }
    $SourceIdentity = Get-ReleaseSourceIdentity -PythonPath $PythonPath `
        -SourceCommit $SourceCommit
    $BuildInputDigest = [string]$SourceIdentity.buildInputDigest
    $SourceTree = [string]$SourceIdentity.sourceTree
    $Version = (Get-Content -LiteralPath (Join-Path $ProjectRoot "VERSION") -Raw).Trim()
    if (-not $Version) {
        throw "VERSION must be non-empty"
    }

    $env:MPP_SOURCE_COMMIT = $SourceCommit
    $env:MPP_SOURCE_TREE = $SourceTree
    $env:MPP_BUILD_INPUT_SHA256 = $BuildInputDigest
    $ReleaseLock = Acquire-ReleaseLock

    if ($Phase -in @("All", "Prepare")) {
        Write-Host "[prepare] Online dependency preparation from lockfiles"
        Remove-Item Env:CARGO_NET_OFFLINE -ErrorAction SilentlyContinue
        Remove-Item Env:npm_config_offline -ErrorAction SilentlyContinue
        Remove-Item Env:UV_OFFLINE -ErrorAction SilentlyContinue
        [void](Invoke-NativeText -Command "npm" -Arguments @("ci") -WorkingDirectory $WebRoot)
        [void](Invoke-NativeText -Command "cargo" -Arguments @(
            "fetch",
            "--locked",
            "--manifest-path",
            (Join-Path $TauriRoot "Cargo.toml")
        ))
        Assert-InstalledTauriCli -BuildToolContract $BuildToolContract
        Write-PrepareStamp -SourceCommit $SourceCommit `
            -BuildInputDigest $BuildInputDigest
    }

    if ($Phase -eq "Prepare") {
        Write-Host "[OK] Desktop release dependencies prepared"
        return
    }

    Write-Host "[build] Offline staged runtime and Tauri compilation"
    Assert-PrepareStamp -SourceCommit $SourceCommit `
        -BuildInputDigest $BuildInputDigest
    Assert-InstalledTauriCli -BuildToolContract $BuildToolContract
    $env:CARGO_NET_OFFLINE = "true"
    $env:npm_config_offline = "true"
    $env:UV_OFFLINE = "1"
    $env:MPP_BUNDLED_UV = (Get-Command uv -ErrorAction Stop).Source

    [void](Invoke-NativeText -Command "node" -Arguments @(
        (Join-Path $WebRoot "scripts\prepare-desktop-runtime.mjs")
    ))
    Invoke-RuntimeVerification -PythonPath $PythonPath `
        -SourceCommit $SourceCommit `
        -Version $Version `
        -BuildInputDigest $BuildInputDigest
    $InitialManifestHash = (
        Get-FileHash -LiteralPath $RuntimeManifestPath -Algorithm SHA256
    ).Hash.ToLowerInvariant()

    $TauriArgs = @(
        "--no-install",
        "tauri",
        "build",
        "--ci",
        "--config",
        $TauriConfig
    )
    if ($Debug) {
        $TauriArgs += "--debug"
    }
    if ($NoBundle) {
        $TauriArgs += "--no-bundle"
    }
    $TauriArgs += @("--", "--locked", "--offline")
    [void](Invoke-NativeText -Command "npx" -Arguments $TauriArgs -WorkingDirectory $WebRoot)

    $FinalIdentity = Get-ReleaseSourceIdentity -PythonPath $PythonPath `
        -SourceCommit $SourceCommit
    if (
        $FinalIdentity.sourceTree -ne $SourceTree -or
        $FinalIdentity.buildInputDigest -ne $BuildInputDigest
    ) {
        throw "Release source identity changed during desktop compilation"
    }
    Invoke-RuntimeVerification -PythonPath $PythonPath `
        -SourceCommit $SourceCommit `
        -Version $Version `
        -BuildInputDigest $BuildInputDigest
    $FinalManifestHash = (
        Get-FileHash -LiteralPath $RuntimeManifestPath -Algorithm SHA256
    ).Hash.ToLowerInvariant()
    if ($FinalManifestHash -ne $InitialManifestHash) {
        throw "Runtime manifest changed during desktop compilation"
    }
    if (-not (Test-Path -LiteralPath $SourceExe -PathType Leaf)) {
        throw "Built executable was not found: $SourceExe"
    }
    if (-not (Test-FileContainsAscii -Path $SourceExe -Value $SourceCommit)) {
        throw "Built executable does not contain the attested Git commit identity"
    }
    if (-not (Test-FileContainsAscii -Path $SourceExe -Value $FinalManifestHash)) {
        throw "Built executable does not contain the attested runtime manifest identity"
    }

    Install-RootExecutable
    $Artifact = Get-Item -LiteralPath $DestinationExe
    $ArtifactHash = (
        Get-FileHash -LiteralPath $DestinationExe -Algorithm SHA256
    ).Hash.ToLowerInvariant()
    $WebIndexHash = (
        Get-FileHash -LiteralPath (Join-Path $RuntimeRoot "web\dist\index.html") `
            -Algorithm SHA256
    ).Hash.ToLowerInvariant()
    $Attestation = [ordered]@{
        schema = 1
        appVersion = $Version
        sourceCommit = $SourceCommit
        sourceTree = $SourceTree
        buildInputDigest = $BuildInputDigest
        runtimeManifestSha256 = $FinalManifestHash
        webIndexSha256 = $WebIndexHash
        artifact = [ordered]@{
            path = "MPP.exe"
            size = $Artifact.Length
            sha256 = $ArtifactHash
        }
        bundleProduced = -not [bool]$NoBundle
    }
    $AttestationJson = $Attestation | ConvertTo-Json -Depth 5
    [System.IO.File]::WriteAllText(
        $AttestationFile,
        "$AttestationJson`n",
        [System.Text.UTF8Encoding]::new($false)
    )

    $HandoffIdentity = Get-ReleaseSourceIdentity -PythonPath $PythonPath `
        -SourceCommit $SourceCommit
    if (
        $HandoffIdentity.sourceTree -ne $SourceTree -or
        $HandoffIdentity.buildInputDigest -ne $BuildInputDigest
    ) {
        throw "Release source identity changed during artifact handoff"
    }

    Write-Host "[OK] Root executable updated: $($Artifact.FullName)"
    Write-Host "[OK] Size: $($Artifact.Length) bytes"
    Write-Host "[OK] SHA-256: $ArtifactHash"
    Write-Host "[OK] Attestation: $AttestationFile"
    if (-not $NoBundle) {
        Write-Host "[B5 TODO] Bundle installation, signing, and immutable snapshot verification"
    }
} finally {
    if ($ReleaseLock) {
        $ReleaseLock.Dispose()
        if (Test-Path -LiteralPath $ReleaseLockPath) {
            Remove-Item -LiteralPath $ReleaseLockPath -Force
        }
    }
    if (Test-Path -LiteralPath $TemporaryExe) {
        Remove-Item -LiteralPath $TemporaryExe -Force
    }
}
