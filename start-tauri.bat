@echo off
chcp 65001 >nul 2>&1
title MediaProcessPipeline Tauri

cd /d "%~dp0"

set "APP_EXE=%~dp0MPP.exe"
if not exist "%APP_EXE%" set "APP_EXE=%~dp0web\src-tauri\target\release\mpp-desktop.exe"

if exist "%APP_EXE%" (
    start "" "%APP_EXE%"
    exit /b 0
)

echo Tauri executable not found.
echo Build it with: cd web ^&^& npm run tauri:build:portable
exit /b 1
