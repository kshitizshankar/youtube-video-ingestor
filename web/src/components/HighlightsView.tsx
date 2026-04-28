import type { Analysis } from "../types";

export interface HighlightsViewProps {
  analysis: Analysis | null;
  loading: boolean;
  onSeek: (seconds: number) => void;
  speakerNames?: Record<string, string>;
}

function fmtTs(sec: number): string {
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = Math.floor(sec % 60);
  return h
    ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`
    : `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

function speakerTone(speaker: string | null, idx: number): string {
  if (!speaker) return "s1";
  const m = speaker.match(/SPEAKER_(\d+)/i);
  const n = m ? parseInt(m[1], 10) : idx;
  return `s${(n % 4) + 1}`;
}

function speakerLabel(speaker: string | null, override?: string): string {
  if (override && override.trim()) return override.trim();
  if (!speaker) return "—";
  const m = speaker.match(/SPEAKER_(\d+)/i);
  return m ? `Speaker ${parseInt(m[1], 10) + 1}` : speaker;
}

export default function HighlightsView({ analysis, loading, onSeek, speakerNames = {} }: HighlightsViewProps) {
  if (loading) {
    return <div className="tr-loading"><span className="dot" /> loading highlights...</div>;
  }
  if (!analysis) {
    return <div className="analysis-empty">no highlights yet.</div>;
  }
  return (
    <div className="analysis-wrap">
      {analysis.highlights.map((h, i) => {
        const tone = speakerTone(h.speaker, i);
        return (
          <article
            className="highlight-card"
            key={i}
            onClick={() => onSeek(h.start)}
            role="button"
            tabIndex={0}
            onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") onSeek(h.start); }}
          >
            <header>
              {h.speaker && <span className={`tr-speaker ${tone}`}>{speakerLabel(h.speaker, speakerNames[h.speaker])}</span>}
              <button
                className="tr-time"
                type="button"
                onClick={(e) => { e.stopPropagation(); onSeek(h.start); }}
              >
                {fmtTs(h.start)}
              </button>
              <span className="hl-dur">{Math.round(h.end - h.start)}s</span>
            </header>
            <blockquote>“{h.quote}”</blockquote>
            <p className="hl-reason">{h.reason}</p>
          </article>
        );
      })}
    </div>
  );
}
