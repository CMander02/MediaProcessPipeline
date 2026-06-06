@echo off
chcp 65001 >nul 2>&1
title MediaProcessPipeline Electron

cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-electron.ps1"
