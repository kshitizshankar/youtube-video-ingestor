# Watch the output/ folder — shows every video folder and what files have
# landed (audio.mp3, transcript.json, analysis.json, etc.) updated every 2s.
# Useful for seeing whether the pipeline actually reaches each stage.

$repo = "E:\Structured Experiments\youtube-video-ingestor"
$out = "$repo\output"

while ($true) {
    Clear-Host
    Write-Host "=== output/ @ $(Get-Date -Format 'HH:mm:ss') ===" -ForegroundColor Cyan
    Get-ChildItem $out -Directory -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 12 |
        ForEach-Object {
            $id = $_.Name
            $files = Get-ChildItem $_.FullName -File -ErrorAction SilentlyContinue |
                ForEach-Object { "{0}({1:N0}b)" -f $_.Name, $_.Length }
            Write-Host ("{0,-14} {1}" -f $id, ($files -join " "))
        }
    Start-Sleep -Seconds 2
}
