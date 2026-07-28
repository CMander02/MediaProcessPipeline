from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_desktop_build_hands_off_executable_and_matching_runtime():
    script = (ROOT / "scripts" / "build-desktop.ps1").read_text(encoding="utf-8")

    assert '$DestinationRuntime = Join-Path $ProjectRoot "runtime"' in script
    assert '$TemporaryRuntime = Join-Path $ProjectRoot "runtime.next"' in script
    assert (
        "Copy-Item -LiteralPath $RuntimeRoot -Destination $TemporaryRuntime -Recurse"
        in script
    )
    assert "-RuntimePath $TemporaryRuntime" in script
    assert "Root runtime staging changed the attested runtime manifest" in script
    assert (
        "Move-Item -LiteralPath $TemporaryRuntime -Destination $DestinationRuntime"
        in script
    )
    assert (
        "Move-Item -LiteralPath $PreviousRuntime -Destination $DestinationRuntime"
        in script
    )
    assert "$InstalledRuntime -and" in script
    assert "$InstalledExe -and" in script


def test_root_launcher_requires_complete_desktop_artifact_pair():
    launcher = (ROOT / "start-tauri.bat").read_text(encoding="utf-8")

    assert r"runtime\runtime-manifest.json" in launcher
    assert r"resources\.release-build.lock" in launcher
    assert (
        'if exist "%APP_EXE%" if exist "%APP_RUNTIME_MANIFEST%" (' in launcher
    )
