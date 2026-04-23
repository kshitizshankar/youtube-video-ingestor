# Run FastAPI with verbose logging, tee to logs/server.log.
# Run this in its own PowerShell terminal.

$ErrorActionPreference = "Stop"
$repo = "E:\Structured Experiments\youtube-video-ingestor"
Set-Location $repo

# Ensure logs dir exists.
$null = New-Item -ItemType Directory -Force -Path "$repo\logs"
$log = "$repo\logs\server-$(Get-Date -Format 'yyyyMMdd-HHmmss').log"
Write-Host "Logging to $log"

# Activate venv.
& "$repo\.venv\Scripts\Activate.ps1"

# Unbuffered Python so logs flush immediately.
$env:PYTHONUNBUFFERED = "1"

# Fresh stdout + tee to file. --log-level info shows request lines + our logger output.
# --reload would auto-restart on code changes but conflicts with worker threads;
# restart manually after edits.
python -m uvicorn server.main:app `
    --host 127.0.0.1 --port 8000 `
    --log-level info --access-log `
    2>&1 | Tee-Object -FilePath $log
