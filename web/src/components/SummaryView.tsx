import type { Analysis } from "../types";

export interface SummaryViewProps {
  analysis: Analysis | null;
  loading: boolean;
  onRegenerate?: () => void;
  regenerating?: boolean;
  error?: string | null;
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
      {onRegenerate && (
        <div className="analysis-foot">
          <button className="btn" onClick={onRegenerate} disabled={regenerating}>
            {regenerating ? "Regenerating…" : "↻ Regenerate"}
          </button>
        </div>
      )}
    </div>
  );
}
