@echo off
chcp 65001 >nul 2>&1
title MediaProcessPipeline
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-web.ps1" %*
exit /b %ERRORLEVEL%
