@echo off
chcp 65001 >nul 2>&1
title MediaProcessPipeline

cd /d "%~dp0backend"
echo Starting MPP on http://localhost:18000 ...
start "" http://localhost:18000
uv run python -m app.cli serve
