@echo off
chcp 65001 >nul 2>&1
title MediaProcessPipeline Tauri

cd /d "%~dp0"

set "BUILD_LOCK=%~dp0web\src-tauri\resources\.release-build.lock"
set "APP_EXE=%~dp0MPP.exe"
set "APP_RUNTIME_MANIFEST=%~dp0runtime\runtime-manifest.json"

if exist "%BUILD_LOCK%" (
    echo Desktop build is in progress. Start MPP again after it completes.
    exit /b 1
)

if exist "%APP_EXE%" if exist "%APP_RUNTIME_MANIFEST%" (
    start "" "%APP_EXE%"
    exit /b 0
)

echo Desktop artifacts are incomplete. MPP.exe and runtime\runtime-manifest.json are required.
echo Build it with: cd web ^&^& npm run tauri:build:portable
exit /b 1
