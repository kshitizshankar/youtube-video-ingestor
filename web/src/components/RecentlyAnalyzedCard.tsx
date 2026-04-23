import { Link } from "react-router-dom";
import type { RecentAnalysisStat } from "../types";

function relativeTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  const delta = (Date.now() - d.getTime()) / 1000;
  if (delta < 60) return "just now";
  if (delta < 3600) return `${Math.floor(delta / 60)}m ago`;
  if (delta < 86400) return `${Math.floor(delta / 3600)}h ago`;
  if (delta < 86400 * 30) return `${Math.floor(delta / 86400)}d ago`;
  return d.toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric" });
}

/** Map a provider key to a short display label. */
function providerLabel(p: string): string {
  if (p === "claude_cli") return "Claude";
  if (p === "ollama") return "Ollama";
  return p;
}

export interface RecentlyAnalyzedCardProps {
  items: RecentAnalysisStat[];
}

export default function RecentlyAnalyzedCard({ items }: RecentlyAnalyzedCardProps) {
  if (items.length === 0) return null;
  return (
    <section className="analytics-card" aria-labelledby="ana-recent">
      <div className="ana-head">
        <span className="ana-label" id="ana-recent">Recently analyzed</span>
        <span className="ana-hint">latest AI runs</span>
      </div>
      <ul className="ana-row-list">
        {items.map((it) => (
          <li key={`${it.id}-${it.finished_at ?? ""}`} className="ana-row">
            <Link to={`/v/${it.id}`} className="ana-row-link">
              <span className="ana-row-main">
                <span className="ana-row-title">{it.title ?? it.id}</span>
                <span className="ana-row-sub">
                  <span className="ana-provider">{providerLabel(it.provider)}</span>
                  <span className="ana-dot">·</span>
                  <span>{relativeTime(it.finished_at)}</span>
                </span>
              </span>
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}
