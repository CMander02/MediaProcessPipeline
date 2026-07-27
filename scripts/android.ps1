param(
    [Parameter(Position = 0)]
    [ValidateSet("doctor", "test", "build", "install", "run", "logcat")]
    [string]$Command = "doctor"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$androidRoot = Join-Path $projectRoot "android"

function Resolve-JavaHome {
    $candidates = @(
        $env:JAVA_HOME,
        "C:\Program Files\Android\Android Studio\jbr"
    ) | Where-Object { $_ }

    foreach ($candidate in $candidates) {
        $java = Join-Path $candidate "bin\java.exe"
        $javac = Join-Path $candidate "bin\javac.exe"
        $releaseFile = Join-Path $candidate "release"
        if ((Test-Path -LiteralPath $java) -and (Test-Path -LiteralPath $javac)) {
            $versionOutput = if (Test-Path -LiteralPath $releaseFile) {
                Get-Content -LiteralPath $releaseFile -Raw
            } else {
                ""
            }
            if ($versionOutput -match 'JAVA_VERSION="(?:1\.)?(?<major>\d+)') {
                if ([int]$Matches.major -ge 17) {
                    return (Resolve-Path -LiteralPath $candidate).Path
                }
            }
        }
    }

    throw "未找到 JDK 17。请安装 Android Studio，或设置 JAVA_HOME。"
}

function Resolve-AndroidHome {
    $candidates = @(
        $env:ANDROID_HOME,
        $env:ANDROID_SDK_ROOT,
        (Join-Path $env:LOCALAPPDATA "Android\Sdk")
    ) | Where-Object { $_ }

    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }

    throw "未找到 Android SDK。请在 Android Studio 的 SDK Manager 中安装 SDK。"
}

function Initialize-AndroidEnvironment {
    $script:javaHome = Resolve-JavaHome
    $script:androidHome = Resolve-AndroidHome
    $env:JAVA_HOME = $script:javaHome
    $env:ANDROID_HOME = $script:androidHome
    $env:ANDROID_SDK_ROOT = $script:androidHome
    $script:adb = Join-Path $script:androidHome "platform-tools\adb.exe"
    $script:gradleWrapper = Join-Path $androidRoot "gradlew.bat"
}

function Invoke-Gradle {
    param([string[]]$Tasks)

    Push-Location $androidRoot
    try {
        & $script:gradleWrapper @Tasks
        if ($LASTEXITCODE -ne 0) {
            throw "Gradle 执行失败，退出码：$LASTEXITCODE"
        }
    } finally {
        Pop-Location
    }
}

Initialize-AndroidEnvironment

switch ($Command) {
    "doctor" {
        Write-Host "Java:       $javaHome"
        & (Join-Path $javaHome "bin\java.exe") -version
        Write-Host "Android SDK: $androidHome"
        if (Test-Path -LiteralPath $adb) {
            Write-Host "ADB:         $adb"
            & $adb version
            & $adb devices
        } else {
            throw "Android SDK Platform-Tools 尚未安装。"
        }
        $platformJar = Join-Path $androidHome "platforms\android-36.1\android.jar"
        if (-not (Test-Path -LiteralPath $platformJar)) {
            throw "Android SDK Platform 36.1 尚未安装。"
        }
        $buildTools = Join-Path $androidHome "build-tools\36.0.0"
        if (-not (Test-Path -LiteralPath $buildTools)) {
            throw "Android SDK Build-Tools 36.0.0 尚未安装。"
        }
        Write-Host "SDK Platform: Android 36.1"
        Write-Host "Build Tools:  36.0.0"
    }
    "test" {
        Invoke-Gradle @("test")
    }
    "build" {
        Invoke-Gradle @("test", "assembleDebug")
        Write-Host "APK: $androidRoot\app\build\outputs\apk\debug\app-debug.apk"
    }
    "install" {
        Invoke-Gradle @("installDebug")
    }
    "run" {
        Invoke-Gradle @("installDebug")
        & $adb shell am start -n "com.mpp.remote/.MainActivity"
    }
    "logcat" {
        & $adb logcat | Select-String -Pattern "com.mpp.remote|AndroidRuntime"
    }
}
