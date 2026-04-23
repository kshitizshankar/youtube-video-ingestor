import { useEffect, useState } from "react";

export interface IngestStatusProps {
  busy: boolean;
  phase: string;
  startedAt: number | null;
  lastEventAt: number | null;
  segmentCount: number;
  lastSegmentEnd: number;   // seconds of audio covered so far
  duration: number | null;  // total video duration, sec
}

function fmtElapsed(ms: number): string {
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rem = s % 60;
  return `${m}m ${String(rem).padStart(2, "0")}s`;
}

/**
 * Sticky live indicator rendered above the transcript segments while an
 * ingest is in progress. Shows phase, elapsed time, segments received, and a
 * progress bar. A "stalled?" warning appears if no event has arrived in 12s.
 */
export default function IngestStatus(props: IngestStatusProps) {
  const { busy, phase, startedAt, lastEventAt, segmentCount, lastSegmentEnd, duration } = props;
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!busy) return;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [busy]);

  if (!busy && segmentCount === 0 && !startedAt) return null;

  const elapsed = startedAt ? now - startedAt : 0;
  const sinceLast = lastEventAt ? now - lastEventAt : 0;
  // Before any segments arrive, whisper is doing model-load + VAD + language
  // detect + first-chunk inference. 60-90s of silence is completely normal,
  // especially in sequential (live) mode or with diarization. Only after the
  // first segment do we start expecting steady events.
  const stallThresholdMs = segmentCount === 0 ? 90_000 : 20_000;
  const stalled = busy && sinceLast > stallThresholdMs;
  const pct = duration && duration > 0 ? Math.min(100, (lastSegmentEnd / duration) * 100) : 0;

  return (
    <div className={`ingest-status ${busy ? "is-busy" : ""} ${stalled ? "is-stalled" : ""}`}>
      <div className="is-row-1">
        <span className="is-dot" />
        <span className="is-phase">{phase || (busy ? "working" : "idle")}</span>
        <span className="is-sep">·</span>
        <span className="is-meta">{fmtElapsed(elapsed)} elapsed</span>
        <span className="is-sep">·</span>
        <span className="is-meta">{segmentCount} segments</span>
        {duration ? (
          <>
            <span className="is-sep">·</span>
            <span className="is-meta">{pct.toFixed(0)}%</span>
          </>
        ) : null}
        {stalled && (
          <span className="is-stall">
            {segmentCount === 0
              ? `warming up — ${Math.floor(sinceLast / 1000)}s`
              : `no update for ${Math.floor(sinceLast / 1000)}s`}
          </span>
        )}
      </div>
      {duration ? (
        <div className="is-bar">
          <div className="is-fill" style={{ width: `${pct}%` }} />
        </div>
      ) : null}
    </div>
  );
}
