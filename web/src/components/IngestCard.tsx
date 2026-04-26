import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import type { IngestState } from "../api";

export interface IngestCardProps {
  ing: IngestState;
  /** Wall-clock seconds, used for elapsed/stalled labels. Pass a shared
   *  ticking value to keep multiple cards in sync without duplicating timers. */
  now?: number;
}

function fmtMinSec(sec: number): string {
  const s = Math.floor(sec);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rem = s % 60;
  return `${m}m ${String(rem).padStart(2, "0")}s`;
}

export default function IngestCard({ ing, now: nowProp }: IngestCardProps) {
  // Self-ticking when the parent doesn't supply `now`. Cheap (1Hz) and lets
  // a single card drop into pages that don't want to manage a strip-level timer.
  const [now, setNow] = useState(() => nowProp ?? Date.now() / 1000);
  useEffect(() => {
    if (nowProp !== undefined) {
      setNow(nowProp);
      return;
    }
    const id = window.setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(id);
  }, [nowProp]);

  const thumb = `https://img.youtube.com/vi/${ing.id}/hqdefault.jpg`;
  const elapsedSec = Math.max(0, now - ing.started_at);
  const staleSec = Math.max(0, now - ing.last_event_at);
  const pct =
    ing.duration_sec && ing.duration_sec > 0
      ? Math.min(100, (ing.last_segment_end / ing.duration_sec) * 100)
      : 0;
  const stalled = !ing.done && staleSec > 15;
  const cls = ["job-card"];
  if (ing.done) cls.push(ing.error ? "is-error" : "is-done");
  else if (stalled) cls.push("is-stalled");
  else cls.push("is-busy");

  const phaseLabel = ing.error
    ? `Error: ${ing.error}`
    : ing.done
      ? "Done"
      : ing.phase || "working";

  return (
    <Link to={`/v/${ing.id}`} className={cls.join(" ")}>
      <div className="ic-thumb">
        <img src={thumb} alt="" loading="lazy" />
      </div>
      <div className="ic-body">
        <div className="ic-title">{ing.title || ing.id}</div>
        <div className="ic-meta">
          <span className="ic-phase">{phaseLabel}</span>
          <span className="ic-sep">·</span>
          <span>{fmtMinSec(elapsedSec)} elapsed</span>
          {ing.segments > 0 && (
            <>
              <span className="ic-sep">·</span>
              <span>{ing.segments} seg</span>
            </>
          )}
          {ing.duration_sec ? (
            <>
              <span className="ic-sep">·</span>
              <span>{pct.toFixed(0)}%</span>
            </>
          ) : null}
          {stalled && <span className="ic-stall">stalled {Math.floor(staleSec)}s</span>}
        </div>
        <div className="ic-bar">
          <div className="ic-fill" style={{ width: `${pct}%` }} />
        </div>
      </div>
    </Link>
  );
}
