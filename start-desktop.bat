@echo off
chcp 65001 >nul 2>&1
title MPP Desktop
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-desktop.ps1" %*
if errorlevel 1 pause
exit /b %ERRORLEVEL%
