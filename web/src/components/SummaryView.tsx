import type { Analysis } from "../types";

export interface SummaryViewProps {
  analysis: Analysis | null;
  loading: boolean;
  onRegenerate?: () => void;
  regenerating?: boolean;
  error?: string | null;
}

function fmtTokens(n: number | undefined): string {
  if (!n || n <= 0) return "0";
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(1)}k`;
  return `${(n / 1_000_000).toFixed(2)}M`;
}

function fmtCost(usd: number | undefined): string {
  if (!usd || usd <= 0) return "$0";
  if (usd < 0.01) return `$${usd.toFixed(4)}`;
  return `$${usd.toFixed(3)}`;
}

function fmtMs(ms: number | null | undefined): string | null {
  if (!ms || ms <= 0) return null;
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(1)}s`;
  const m = Math.floor(s / 60);
  const rem = Math.floor(s % 60);
  return `${m}m ${String(rem).padStart(2, "0")}s`;
}

function relativeTime(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  const deltaSec = (Date.now() - d.getTime()) / 1000;
  if (deltaSec < 60) return "just now";
  if (deltaSec < 3600) return `${Math.floor(deltaSec / 60)} min ago`;
  if (deltaSec < 86400) return `${Math.floor(deltaSec / 3600)} hr ago`;
  return `${Math.floor(deltaSec / 86400)} days ago`;
}

export default function SummaryView({
  analysis, loading, onRegenerate, regenerating, error,
}: SummaryViewProps) {
  if (loading) {
    return <div className="tr-loading"><span className="dot" /> Loading analysis…</div>;
  }
  if (!analysis) {
    return (
      <div className="analysis-empty">
        <p>No summary yet.</p>
        {onRegenerate && (
          <button className="btn btn-primary" onClick={onRegenerate} disabled={regenerating}>
            {regenerating ? "Generating…" : "Generate now"}
          </button>
        )}
        {error && <div className="analysis-err">{error}</div>}
      </div>
    );
  }
  const meta = analysis._meta;
  const elapsed = fmtMs(meta?.duration_ms);
  const genAgo = relativeTime(meta?.generated_at);
  return (
    <div className="analysis-wrap">
      <div className="summary-card">
        <h4>Summary · generated</h4>
        <p>{analysis.summary}</p>
      </div>
      <div className="summary-card">
        <h4>Key takeaways</h4>
        {analysis.takeaways.map((t, i) => (
          <div className="takeaway" key={i}>
            <span className="n">{String(i + 1).padStart(2, "0")}</span>
            <span>{t}</span>
          </div>
        ))}
      </div>
      <div className="analysis-foot">
        {meta && (
          <div className="analysis-meta" title={meta.generated_at || ""}>
            {genAgo && <span>Generated {genAgo}</span>}
            {elapsed && (<><span className="sep">·</span><span>{elapsed}</span></>)}
            {typeof meta.num_turns === "number" && meta.num_turns > 0 && (
              <><span className="sep">·</span><span>{meta.num_turns} turn{meta.num_turns === 1 ? "" : "s"}</span></>
            )}
            <span className="sep">·</span>
            <span title="Input / output tokens">
              {fmtTokens(meta.tokens_in)} in · {fmtTokens(meta.tokens_out)} out
            </span>
            {meta.cache_read_tokens && meta.cache_read_tokens > 0 && (
              <><span className="sep">·</span><span title="Cached input tokens (cheaper)">{fmtTokens(meta.cache_read_tokens)} cached</span></>
            )}
            <span className="sep">·</span>
            <span className="analysis-cost" title="Cost in USD">{fmtCost(meta.cost_usd)}</span>
          </div>
        )}
        {onRegenerate && (
          <button className="btn" onClick={onRegenerate} disabled={regenerating}>
            {regenerating ? "Regenerating…" : "↻ Regenerate"}
          </button>
        )}
      </div>
    </div>
  );
}
