$ErrorActionPreference = 'Stop'
$project = Split-Path $PSScriptRoot -Parent
$logs = Join-Path $project 'Saved/Checkpoints'
New-Item -ItemType Directory -Force $logs | Out-Null
$uv = 'F:/GeoVerse/AgentTools/python/bin/uv.exe'
if (-not (Test-Path $uv)) { $uv = (Get-Command uv -ErrorAction Stop).Source }
$env:UV_PYTHON = 'C:/Program Files/Epic Games/UE_5.8/Engine/Binaries/ThirdParty/Python3/Win64/python.exe'
$env:UV_CACHE_DIR = Join-Path $env:LOCALAPPDATA 'uv/human-touch-checkpoints'
$env:COPRESENCE_HUB_URL = 'http://100.84.28.59:8791'
Set-Location $project
# Windows PowerShell treats native stderr as ErrorRecord; preserve output and exit code.
$ErrorActionPreference = 'Continue'
& $uv run --script Tools/checkpoint.py @args >> "$logs/checkpoint.log" 2>&1
exit $LASTEXITCODE
