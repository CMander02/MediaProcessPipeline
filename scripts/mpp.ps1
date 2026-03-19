# mpp — CLI wrapper for MediaProcessPipeline
# Usage: .\scripts\mpp.ps1 serve|run|status|list|show|cancel|config
Set-Location "$PSScriptRoot\..\backend"
uv run python -m app.cli $args
