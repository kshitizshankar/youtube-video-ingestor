import { type ReactNode } from "react";
import { Link } from "react-router-dom";
import type { SearchHit } from "../api";

export interface SearchResultsProps {
  query: string;
  hits: SearchHit[];
  loading: boolean;
  error: string | null;
  truncated: boolean;
  elapsedMs: number | null;
}

function fmtTs(sec: number): string {
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = Math.floor(sec % 60);
  return h
    ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`
    : `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

function speakerLabel(s: string | null): string | null {
  if (!s) return null;
  const m = s.match(/SPEAKER_(\d+)/i);
  return m ? `Speaker ${parseInt(m[1], 10) + 1}` : s;
}

/** Extract a ~24-word window around the first match, with ellipses. */
function contextWindow(text: string, query: string, wordsAround = 14): string {
  const idx = text.toLowerCase().indexOf(query.toLowerCase());
  if (idx < 0) return text;
  const before = text.slice(0, idx).split(/\s+/);
  const after = text.slice(idx + query.length).split(/\s+/);
  const left = before.slice(-wordsAround).join(" ");
  const right = after.slice(0, wordsAround).join(" ");
  const prefix = before.length > wordsAround ? "… " : "";
  const suffix = after.length > wordsAround ? " …" : "";
  return `${prefix}${left}${text.slice(idx, idx + query.length)}${right}${suffix}`;
}

function Highlighted({ text, query }: { text: string; query: string }) {
  if (!query) return <>{text}</>;
  const lower = text.toLowerCase();
  const q = query.toLowerCase();
  const parts: ReactNode[] = [];
  let i = 0;
  let key = 0;
  while (i < text.length) {
    const idx = lower.indexOf(q, i);
    if (idx === -1) {
      parts.push(text.slice(i));
      break;
    }
    if (idx > i) parts.push(text.slice(i, idx));
    parts.push(<mark key={key++}>{text.slice(idx, idx + q.length)}</mark>);
    i = idx + q.length;
  }
  return <>{parts}</>;
}

export default function SearchResults({
  query, hits, loading, error, truncated, elapsedMs,
}: SearchResultsProps) {
  const byVideo = new Map<string, { title: string; hits: SearchHit[] }>();
  for (const h of hits) {
    const slot = byVideo.get(h.video_id) ?? { title: h.title, hits: [] };
    slot.hits.push(h);
    byVideo.set(h.video_id, slot);
  }
  const groups = Array.from(byVideo.entries());

  return (
    <section className="search-results">
      <div className="search-stats">
        {loading && <span className="search-stat">Searching…</span>}
        {!loading && !error && (
          <>
            <span className="search-stat">
              <strong>{hits.length}</strong> {hits.length === 1 ? "match" : "matches"}
              {" "} across <strong>{byVideo.size}</strong> video{byVideo.size === 1 ? "" : "s"}
            </span>
            {elapsedMs !== null && (
              <span className="search-stat-secondary">· {elapsedMs}ms</span>
            )}
            {truncated && (
              <span className="search-stat-secondary">· truncated — refine your query</span>
            )}
          </>
        )}
        {error && <span className="search-stat-error">Error: {error}</span>}
      </div>

      {!loading && !error && hits.length === 0 && (
        <div className="search-empty">
          No matches for <em>“{query}”</em>.
        </div>
      )}

      <div className="search-groups">
        {groups.map(([vid, group]) => {
          const thumb = `https://img.youtube.com/vi/${vid}/hqdefault.jpg`;
          return (
            <article key={vid} className="search-group">
              <Link to={`/v/${vid}`} className="search-group-head">
                <img src={thumb} alt="" loading="lazy" />
                <div>
                  <h4>{group.title}</h4>
                  <div className="search-group-meta">
                    {group.hits.length} {group.hits.length === 1 ? "hit" : "hits"} in this video
                  </div>
                </div>
              </Link>
              <ul className="search-hits">
                {group.hits.slice(0, 6).map((h, i) => (
                  <li key={i}>
                    <Link
                      to={`/v/${vid}?t=${Math.floor(h.start)}`}
                      className="search-hit"
                    >
                      <div className="search-hit-meta">
                        {speakerLabel(h.speaker) && (
                          <span className="search-hit-speaker">{speakerLabel(h.speaker)}</span>
                        )}
                        <span className="search-hit-time">{fmtTs(h.start)}</span>
                      </div>
                      <div className="search-hit-text">
                        <Highlighted
                          text={contextWindow(h.text, query)}
                          query={query}
                        />
                      </div>
                    </Link>
                  </li>
                ))}
                {group.hits.length > 6 && (
                  <li className="search-hits-more">
                    +{group.hits.length - 6} more in this video
                  </li>
                )}
              </ul>
            </article>
          );
        })}
      </div>
    </section>
  );
}
