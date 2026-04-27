# Local dev startup for youtube-video-ingestor.
#
# Port is baked here so the URL is stable across restarts and doesn't
# collide with the user's other local apps (FlowVoice on 8765, stray
# instances on 8000, etc.). 57882 was rolled randomly inside the IANA
# dynamic port range (49152-65535); change it if it ever collides.

$Port = 57882
$BindHost = "127.0.0.1"
$Hostname = "my-vidan.com"   # added to C:\Windows\System32\drivers\etc\hosts

Set-Location $PSScriptRoot
Write-Host "Starting youtube-video-ingestor:" -ForegroundColor Cyan
Write-Host "  http://${Hostname}:${Port}" -ForegroundColor Green
Write-Host "  http://${BindHost}:${Port}" -ForegroundColor DarkGray
uv run uvicorn server.main:app --host $BindHost --port $Port
