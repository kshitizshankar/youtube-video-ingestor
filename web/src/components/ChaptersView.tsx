import type { Analysis } from "../types";

export interface ChaptersViewProps {
  analysis: Analysis | null;
  loading: boolean;
  currentTime: number;
  onSeek: (seconds: number) => void;
}

function fmtTs(sec: number): string {
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = Math.floor(sec % 60);
  return h
    ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`
    : `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

export default function ChaptersView({ analysis, loading, currentTime, onSeek }: ChaptersViewProps) {
  if (loading) {
    return <div className="tr-loading"><span className="dot" /> Loading chapters…</div>;
  }
  if (!analysis) {
    return <div className="analysis-empty">No chapters yet.</div>;
  }
  // Find the chapter that contains currentTime: the last one with start <= currentTime.
  const chapters = analysis.chapters;
  let activeIdx = -1;
  for (let i = 0; i < chapters.length; i++) {
    if (chapters[i].start <= currentTime) activeIdx = i;
    else break;
  }
  return (
    <ol className="chapter-list">
      {chapters.map((c, i) => (
        <li
          key={i}
          className={`chapter-item ${i === activeIdx ? "is-current" : ""}`}
          onClick={() => onSeek(c.start)}
        >
          <span className="ch-num">{String(i + 1).padStart(2, "0")}</span>
          <span className="ch-title">{c.title}</span>
          <span className="ch-time">{fmtTs(c.start)}</span>
        </li>
      ))}
    </ol>
  );
}
