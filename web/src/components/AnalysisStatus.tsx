import { useEffect, useState } from "react";

export interface AnalysisStatusProps {
  /** Running = Claude is generating summary/highlights/chapters right now. */
  running: boolean;
  startedAt: number | null;
  /** Last "analyzing" phase label from the SSE stream, if any. */
  phase?: string;
  error?: string | null;
  /** Live usage counters emitted by the Claude stream-json wrapper. */
  tokensIn?: number;
  tokensOut?: number;
  costUsd?: number;
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

function fmtElapsed(sec: number): string {
  if (sec < 60) return `${sec}s`;
  const m = Math.floor(sec / 60);
  const rem = sec % 60;
  return `${m}m ${String(rem).padStart(2, "0")}s`;
}

/** Sticky pill that appears in the transcript pane header whenever the
 *  Claude analysis pipeline is running — whether from auto post-ingest or a
 *  manual Regenerate click. Visible on every tab so the user sees feedback
 *  even when they're sitting on Summary/Highlights/Chapters. */
export default function AnalysisStatus({
  running, startedAt, phase, error, tokensIn, tokensOut, costUsd,
}: AnalysisStatusProps) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!running) return;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [running]);

  if (error) {
    return (
      <div className="analysis-status is-error" role="status">
        <span className="as-dot" />
        <span className="as-label">Analysis failed</span>
        <span className="as-meta">{error}</span>
      </div>
    );
  }
  if (!running || !startedAt) return null;

  const elapsedSec = Math.max(0, Math.floor((now - startedAt) / 1000));
  const stalled = elapsedSec > 180; // 3 min — claude -p is usually <2 min
  const hasUsage = (tokensIn && tokensIn > 0) || (tokensOut && tokensOut > 0) || (costUsd && costUsd > 0);
  return (
    <div className={`analysis-status ${stalled ? "is-stalled" : "is-running"}`} role="status">
      <span className="as-dot" />
      <span className="as-label">
        {phase && phase.toLowerCase().includes("analyz")
          ? phase
          : phase || "Running Claude analysis"}
      </span>
      <span className="as-meta">{fmtElapsed(elapsedSec)}</span>
      {hasUsage && (
        <>
          <span className="as-sep">·</span>
          <span className="as-meta" title="Input / output tokens">
            {fmtTokens(tokensIn)} in · {fmtTokens(tokensOut)} out
          </span>
          <span className="as-sep">·</span>
          <span className="as-meta as-cost" title="Cost so far (USD)">
            {fmtCost(costUsd)}
          </span>
        </>
      )}
      {stalled && <span className="as-stall">taking longer than usual</span>}
    </div>
  );
}
