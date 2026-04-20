import { Link } from "react-router-dom";
import type { TranscriptSummary } from "../types";

export interface VideoRowProps {
  v: TranscriptSummary;
  highlight?: boolean;
}

function fmtDuration(sec: number | null): string {
  if (!sec) return "—";
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = Math.round(sec % 60);
  return h
    ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`
    : `${m}:${String(s).padStart(2, "0")}`;
}

export default function VideoRow({ v, highlight }: VideoRowProps) {
  const thumb = `https://img.youtube.com/vi/${v.id}/hqdefault.jpg`;
  return (
    <Link to={`/v/${v.id}`} className={`row ${highlight ? "highlight" : ""}`}>
      <div className="thumb">
        <img src={thumb} alt="" loading="lazy" />
        <span className="dur">{fmtDuration(v.duration_sec)}</span>
      </div>
      <div className="title-cell">
        <div className="title">{v.title}</div>
        <div className="meta">
          <YTGlyph />
          <span>YouTube</span>
          <span style={{ color: "var(--ink-4)" }}>·</span>
          <span style={{ fontFamily: "var(--font-mono)", fontSize: 11 }}>{v.id}</span>
          {v.language && (
            <>
              <span style={{ color: "var(--ink-4)" }}>·</span>
              <span style={{ textTransform: "uppercase", fontSize: 10.5, fontFamily: "var(--font-mono)" }}>{v.language}</span>
            </>
          )}
        </div>
      </div>
      <div className="speakers-cell">
        {v.diarized ? (
          <>
            <span className="mini-avatar" style={{ background: "var(--speaker-1)" }}>S1</span>
            <span className="mini-avatar" style={{ background: "var(--speaker-2)" }}>S2</span>
            <span className="extra">Diarized</span>
          </>
        ) : (
          <span className="extra">—</span>
        )}
      </div>
      <div className="status-cell">
        <span className="status-dot" />
        Transcribed
      </div>
      <div className="status-cell" style={{ fontFamily: "var(--font-mono)" }}>
        {v.segment_count} seg.
      </div>
      <div className="more">
        <DotsIcon />
      </div>
    </Link>
  );
}

function YTGlyph() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="#ff0033">
      <path d="M23.498 6.186a3.016 3.016 0 0 0-2.122-2.136C19.505 3.545 12 3.545 12 3.545s-7.505 0-9.377.505A3.017 3.017 0 0 0 .502 6.186C0 8.07 0 12 0 12s0 3.93.502 5.814a3.016 3.016 0 0 0 2.122 2.136c1.871.505 9.376.505 9.376.505s7.505 0 9.377-.505a3.015 3.015 0 0 0 2.122-2.136C24 15.93 24 12 24 12s0-3.93-.502-5.814zM9.545 15.568V8.432L15.818 12l-6.273 3.568z" />
    </svg>
  );
}
function DotsIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
      <circle cx="5" cy="12" r="0.5" /><circle cx="12" cy="12" r="0.5" /><circle cx="19" cy="12" r="0.5" />
    </svg>
  );
}
