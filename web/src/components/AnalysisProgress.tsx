import { useEffect, useRef, useState } from "react";
import { cancelAnalysis, openAnalysisStream } from "../api";
import type { Analysis, AnalysisStage, AnalysisUsage } from "../types";

export interface AnalysisProgressProps {
  videoId: string;
  provider: string;
  model?: string;
  /** Fires on the `done` SSE event with the full analysis result. */
  onDone: (result: Analysis, meta: { durationMs: number; usage: AnalysisUsage }) => void;
  /** Fires on the `error` SSE event, with a human-readable message. */
  onError: (message: string) => void;
  /** Fires when the user clicks "Try again" in the error banner. The parent
   *  typically reopens the trigger modal. */
  onRetry?: () => void;
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

function fmtRel(ms: number): string {
  if (ms < 1000) return `0.${Math.floor(ms / 100)}s`;
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(1)}s`;
  const m = Math.floor(s / 60);
  const rem = Math.floor(s % 60);
  return `${m}m${String(rem).padStart(2, "0")}s`;
}

/** Self-contained live telemetry surface for a running analysis.
 *
 *  Owns the SSE subscription end-to-end: stage timeline, usage strip, cancel
 *  control, done/error terminal states. The parent only reacts to
 *  `onDone` / `onError` to update its stored `Analysis`. */
export default function AnalysisProgress({
  videoId,
  provider,
  model,
  onDone,
  onError,
  onRetry,
}: AnalysisProgressProps) {
  const [stages, setStages] = useState<AnalysisStage[]>([]);
  const [usage, setUsage] = useState<AnalysisUsage>({});
  const [analysisId, setAnalysisId] = useState<number | null>(null);
  const [status, setStatus] = useState<"running" | "cancelling" | "done" | "error">("running");
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [summary, setSummary] = useState<{ durationMs: number; tokensIn: number; tokensOut: number; cost: number } | null>(null);

  const startedAt = useRef<number>(Date.now());
  const [now, setNow] = useState<number>(Date.now());
  const esRef = useRef<EventSource | null>(null);
  // Shadow `usage` in a ref so the SSE `done` handler reads the latest
  // values without re-subscribing the stream on every usage update.
  const usageRef = useRef<AnalysisUsage>({});
  useEffect(() => { usageRef.current = usage; }, [usage]);

  // Tick wall-clock for the running label.
  useEffect(() => {
    if (status !== "running" && status !== "cancelling") return;
    const id = window.setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(id);
  }, [status]);

  // Subscribe to SSE on mount. Re-run if provider/model/videoId changes, which
  // only happens when the parent remounts this component for a fresh attempt.
  useEffect(() => {
    startedAt.current = Date.now();
    setStages([]);
    setUsage({});
    setAnalysisId(null);
    setStatus("running");
    setErrorMsg(null);
    setSummary(null);

    const es = openAnalysisStream(videoId, provider, model);
    esRef.current = es;

    const applyUsage = (d: Record<string, unknown>) => {
      setUsage((prev) => ({
        tokens_in: typeof d.tokens_in === "number" ? d.tokens_in : prev.tokens_in,
        tokens_out: typeof d.tokens_out === "number" ? d.tokens_out : prev.tokens_out,
        cached_tokens:
          typeof d.cached_tokens === "number" ? d.cached_tokens : prev.cached_tokens,
        cost_usd: typeof d.cost_usd === "number" ? d.cost_usd : prev.cost_usd,
      }));
    };
    const captureAnalysisId = (d: Record<string, unknown>) => {
      if (typeof d.analysis_id === "number") setAnalysisId(d.analysis_id);
    };

    const onStage = (ev: MessageEvent) => {
      try {
        const d = JSON.parse(ev.data);
        captureAnalysisId(d);
        const label = typeof d.stage === "string" ? d.stage : null;
        if (!label) return;
        setStages((prev) => {
          // De-duplicate consecutive identical stages.
          if (prev.length > 0 && prev[prev.length - 1].stage === label) return prev;
          return [...prev, { stage: label, ts: Date.now() }];
        });
      } catch {
        /* ignore */
      }
    };
    const onUsage = (ev: MessageEvent) => {
      try {
        const d = JSON.parse(ev.data);
        captureAnalysisId(d);
        applyUsage(d);
      } catch {
        /* ignore */
      }
    };
    // Back-compat: the old server emitted `progress` with a `message` field.
    // If that shape ever resurfaces, surface it as a stage so nothing is lost.
    const onProgress = (ev: MessageEvent) => {
      try {
        const d = JSON.parse(ev.data);
        captureAnalysisId(d);
        applyUsage(d);
        if (typeof d.message === "string") {
          setStages((prev) => {
            if (prev.length > 0 && prev[prev.length - 1].stage === d.message) return prev;
            return [...prev, { stage: d.message, ts: Date.now() }];
          });
        }
      } catch {
        /* ignore */
      }
    };
    const onDoneEv = (ev: MessageEvent) => {
      try {
        const d = JSON.parse(ev.data);
        captureAnalysisId(d);
        const durationMs =
          typeof d.duration_ms === "number"
            ? d.duration_ms
            : Date.now() - startedAt.current;
        const lastUsage = usageRef.current;
        const tokensIn = typeof d.tokens_in === "number" ? d.tokens_in : (lastUsage.tokens_in ?? 0);
        const tokensOut = typeof d.tokens_out === "number" ? d.tokens_out : (lastUsage.tokens_out ?? 0);
        const cost = typeof d.cost_usd === "number" ? d.cost_usd : (lastUsage.cost_usd ?? 0);
        const result: Analysis = d.result ?? {
          summary: "", takeaways: [], chapters: [], highlights: [],
        };
        setSummary({ durationMs, tokensIn, tokensOut, cost });
        setStatus("done");
        es.close();
        esRef.current = null;
        onDone(result, {
          durationMs,
          usage: {
            tokens_in: tokensIn,
            tokens_out: tokensOut,
            cached_tokens: typeof d.cached_tokens === "number" ? d.cached_tokens : lastUsage.cached_tokens,
            cost_usd: cost,
          },
        });
      } catch (e) {
        const msg = `done-event parse failed: ${e}`;
        setErrorMsg(msg);
        setStatus("error");
        es.close();
        esRef.current = null;
        onError(msg);
      }
    };
    const onErrorEv = (ev: MessageEvent) => {
      let msg = "analysis failed";
      if (ev.data) {
        try {
          const d = JSON.parse(ev.data);
          msg = d.error_message || d.message || msg;
          captureAnalysisId(d);
        } catch {
          /* ignore */
        }
      }
      setErrorMsg(msg);
      setStatus("error");
      es.close();
      esRef.current = null;
      onError(msg);
    };

    es.addEventListener("stage", onStage as EventListener);
    es.addEventListener("usage", onUsage as EventListener);
    es.addEventListener("progress", onProgress as EventListener);
    es.addEventListener("done", onDoneEv as EventListener);
    es.addEventListener("error", onErrorEv as EventListener);

    return () => {
      es.removeEventListener("stage", onStage as EventListener);
      es.removeEventListener("usage", onUsage as EventListener);
      es.removeEventListener("progress", onProgress as EventListener);
      es.removeEventListener("done", onDoneEv as EventListener);
      es.removeEventListener("error", onErrorEv as EventListener);
      es.close();
      if (esRef.current === es) esRef.current = null;
    };
    // We intentionally don't track `usage` in the deps — we only re-open the
    // stream on a genuine run change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [videoId, provider, model]);

  const handleCancel = async () => {
    if (analysisId == null) {
      // No id yet — just close the stream locally.
      esRef.current?.close();
      esRef.current = null;
      setStatus("error");
      setErrorMsg("Cancelled before the worker reported an id.");
      onError("cancelled");
      return;
    }
    setStatus("cancelling");
    try {
      await cancelAnalysis(analysisId);
    } catch (e) {
      // Surface but don't tear down — the worker will emit an error/done
      // event which drives the real terminal state.
      setErrorMsg(`cancel failed: ${e}`);
    }
  };

  const hasUsage =
    (usage.tokens_in ?? 0) > 0 ||
    (usage.tokens_out ?? 0) > 0 ||
    (usage.cost_usd ?? 0) > 0;

  // -------- Done (collapsed one-liner) --------
  if (status === "done" && summary) {
    return (
      <div className="analysis-progress is-done" role="status">
        <div className="progress-summary">
          <span className="ps-check" aria-hidden>+</span>
          <span className="ps-label">done in {fmtRel(summary.durationMs)}</span>
          <span className="ps-sep">·</span>
          <span className="ps-meta">
            {fmtTokens(summary.tokensIn)} in · {fmtTokens(summary.tokensOut)} out
          </span>
          <span className="ps-sep">·</span>
          <span className="ps-meta ps-cost">{fmtCost(summary.cost)}</span>
        </div>
      </div>
    );
  }

  // -------- Error --------
  if (status === "error") {
    return (
      <div className="analysis-progress is-error" role="alert">
        <div className="progress-error">
          <div className="pe-head">
            <span className="pe-icon" aria-hidden>!</span>
            <span className="pe-title">analysis failed.</span>
          </div>
          <div className="error-message">{errorMsg ?? "unknown error"}</div>
          {onRetry && (
            <button type="button" className="btn pe-retry" onClick={onRetry}>
              try again
            </button>
          )}
        </div>
      </div>
    );
  }

  // -------- Running / cancelling --------
  const elapsedMs = now - startedAt.current;
  return (
    <div className="analysis-progress is-running" role="status" aria-live="polite">
      <div className="progress-head">
        <span className="progress-pulse" aria-hidden>
          <span className="pulse-core" />
          <span className="pulse-ring" />
        </span>
        <span className="progress-title">
          {status === "cancelling" ? "cancelling..." : "analyzing transcript"}
        </span>
        <span className="progress-elapsed">{fmtRel(elapsedMs)}</span>
      </div>

      <ul className="progress-stages" aria-label="Analysis stages">
        {stages.length === 0 && (
          <li className="progress-stage is-waiting">
            <span className="stage-time">-</span>
            <span className="stage-label">waking up the model...</span>
          </li>
        )}
        {stages.map((s, i) => {
          const isLatest = i === stages.length - 1;
          const rel = s.ts - startedAt.current;
          return (
            <li
              key={`${i}-${s.ts}`}
              className={`progress-stage ${isLatest ? "is-latest" : ""}`}
            >
              <span className="stage-time">{fmtRel(rel)}</span>
              <span className="stage-label">{s.stage}</span>
            </li>
          );
        })}
      </ul>

      {hasUsage && (
        <div className="progress-usage" aria-label="Token usage so far">
          <span className="pu-item">
            <span className="pu-label">in</span>
            <span className="pu-value">{fmtTokens(usage.tokens_in)}</span>
          </span>
          <span className="pu-item">
            <span className="pu-label">out</span>
            <span className="pu-value">{fmtTokens(usage.tokens_out)}</span>
          </span>
          {(usage.cached_tokens ?? 0) > 0 && (
            <span className="pu-item">
              <span className="pu-label">cached</span>
              <span className="pu-value">{fmtTokens(usage.cached_tokens)}</span>
            </span>
          )}
          <span className="pu-item pu-cost">
            <span className="pu-label">cost</span>
            <span className="pu-value">{fmtCost(usage.cost_usd)}</span>
          </span>
        </div>
      )}

      <div className="progress-actions">
        <button
          type="button"
          className="btn-cancel-analysis"
          onClick={handleCancel}
          disabled={status === "cancelling"}
        >
          {status === "cancelling" ? "cancelling..." : "cancel"}
        </button>
      </div>
    </div>
  );
}
