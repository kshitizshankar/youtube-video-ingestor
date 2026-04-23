import { Link } from "react-router-dom";
import { formatDuration } from "../format";
import type { LongestVideoStat } from "../types";

export interface LongestVideosCardProps {
  videos: LongestVideoStat[];
}

export default function LongestVideosCard({ videos }: LongestVideosCardProps) {
  if (videos.length === 0) return null;
  return (
    <section className="analytics-card" aria-labelledby="ana-longest">
      <div className="ana-head">
        <span className="ana-label" id="ana-longest">Longest videos</span>
        <span className="ana-hint">biggest runtimes</span>
      </div>
      <ul className="ana-row-list">
        {videos.map((v, i) => (
          <li key={v.id} className="ana-row">
            <Link to={`/v/${v.id}`} className="ana-row-link">
              <span className="ana-row-index">{String(i + 1).padStart(2, "0")}</span>
              <span className="ana-row-main">
                <span className="ana-row-title">{v.title ?? v.id}</span>
                {v.channel && <span className="ana-row-sub">{v.channel}</span>}
              </span>
              <span className="ana-row-figure">
                {formatDuration(v.duration_sec) ?? "—"}
              </span>
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}
