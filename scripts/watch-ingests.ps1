# Poll /api/ingests every 2s. Shows the server's live job registry —
# this is the source of truth for "what's actually transcribing right now".
# If this is empty while the UI shows "transcription in progress",
# the job is NOT actually running server-side.

$ErrorActionPreference = "SilentlyContinue"
$user = "kshitizshankar"
$pass = "WolfPackPlayground"
$pair = "${user}:${pass}"
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($pair))
$headers = @{ Authorization = "Basic $b64" }

while ($true) {
    Clear-Host
    Write-Host "=== /api/ingests @ $(Get-Date -Format 'HH:mm:ss') ===" -ForegroundColor Cyan
    try {
        $r = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/ingests" -Headers $headers -TimeoutSec 3
        if ($r.Count -eq 0) {
            Write-Host "(empty — no active jobs tracked)" -ForegroundColor Yellow
        } else {
            foreach ($job in $r) {
                $now = [int](Get-Date -UFormat %s)
                $age = if ($job.last_event_at) { $now - [int]$job.last_event_at } else { "-" }
                $err = if ($job.error) { $job.error } else { "" }
                $line = "{0,-12} {1,-14} {2,3}seg  last_evt={3}s  done={4}  error={5}" -f `
                    $job.id, $job.phase, $job.segments, $age, $job.done, $err
                Write-Host $line
            }
        }
    } catch {
        Write-Host "error: $_" -ForegroundColor Red
    }
    Start-Sleep -Seconds 2
}
