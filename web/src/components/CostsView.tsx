import type { Analysis, Transcript } from "../types";

export interface CostsViewProps {
  transcript: Transcript | null;
  analysis: Analysis | null;
  onRegenerate?: () => void;
  regenerating?: boolean;
}

function fmtTokens(n: number | undefined | null): string {
  if (!n || n <= 0) return "0";
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(1)}k`;
  return `${(n / 1_000_000).toFixed(2)}M`;
}

function fmtCost(usd: number | undefined | null): string {
  if (!usd || usd <= 0) return "$0";
  if (usd < 0.01) return `$${usd.toFixed(4)}`;
  if (usd < 1) return `$${usd.toFixed(3)}`;
  return `$${usd.toFixed(2)}`;
}

function fmtMs(ms: number | null | undefined): string | null {
  if (!ms || ms <= 0) return null;
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(1)}s`;
  const m = Math.floor(s / 60);
  const rem = Math.floor(s % 60);
  return `${m}m ${String(rem).padStart(2, "0")}s`;
}

function fmtSec(s: number | null | undefined): string | null {
  if (!s || s <= 0) return null;
  if (s < 60) return `${s.toFixed(1)}s`;
  const m = Math.floor(s / 60);
  const rem = Math.floor(s % 60);
  return `${m}m ${String(rem).padStart(2, "0")}s`;
}

function fmtDuration(sec: number | null | undefined): string {
  if (!sec) return "—";
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = Math.floor(sec % 60);
  return h
    ? `${h}h ${m}m ${String(s).padStart(2, "0")}s`
    : `${m}m ${String(s).padStart(2, "0")}s`;
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

/** Observability breakdown for a single video: transcription + analysis
 *  compute, time, token counts, and dollar cost. */
export default function CostsView({
  transcript, analysis, onRegenerate, regenerating,
}: CostsViewProps) {
  if (!transcript) {
    return <div className="tr-loading"><span className="dot" /> Loading…</div>;
  }

  const elapsed = fmtSec(transcript.transcription_elapsed_sec ?? null);
  const rt = transcript.transcription_realtime_factor ?? null;
  const meta = analysis?._meta;
  const cost = meta?.cost_usd ?? 0;

  return (
    <div className="costs-view">
      {/* ------------------------- TRANSCRIPTION ------------------------- */}
      <section className="costs-section">
        <header>
          <h4>Transcription</h4>
          <span className="costs-badge">Local · free</span>
        </header>
        <dl>
          <div><dt>Model</dt><dd className="mono">{transcript.model || "—"}</dd></div>
          <div><dt>Compute type</dt><dd className="mono">{transcript.compute_type || "—"}</dd></div>
          <div><dt>Mode</dt><dd>
            {transcript.batched === false ? "Sequential (live stream)" :
             transcript.batched === true ? `Batched · size ${transcript.batch_size ?? "?"}` :
             transcript.diarized ? "WhisperX (with diarization)" : "—"}
          </dd></div>
          <div><dt>Diarized</dt><dd>{transcript.diarized ? "Yes (pyannote)" : "No"}</dd></div>
          <div><dt>Audio length</dt><dd className="mono">{fmtDuration(transcript.duration_sec)}</dd></div>
          <div><dt>Time on GPU</dt><dd className="mono">{elapsed ?? "—"}</dd></div>
          <div>
            <dt>Realtime factor</dt>
            <dd className="mono">
              {rt ? <span className="costs-accent">{rt.toFixed(1)}×</span> : "—"}
              {rt && <span className="costs-note"> · {(rt / 1).toFixed(0)}s of audio per 1s compute</span>}
            </dd>
          </div>
          <div><dt>Segments</dt><dd className="mono">{transcript.segments.length.toLocaleString()}</dd></div>
          <div><dt>Language</dt><dd className="mono">
            {transcript.language?.toUpperCase() || "—"}
            {transcript.language_probability != null && (
              <span className="costs-note"> · {(transcript.language_probability * 100).toFixed(1)}% confidence</span>
            )}
          </dd></div>
        </dl>
      </section>

      {/* ----------------------- CLAUDE ANALYSIS ------------------------- */}
      <section className="costs-section">
        <header>
          <h4>Claude analysis</h4>
          {meta ? (
            <span className="costs-badge costs-cost">{fmtCost(cost)}</span>
          ) : (
            <span className="costs-badge muted">Not generated</span>
          )}
        </header>
        {!analysis ? (
          <div className="costs-empty">
            <p>
              Summary, highlights, and chapters haven't been generated for this
              video yet.
            </p>
            {onRegenerate && (
              <button className="btn btn-primary" onClick={onRegenerate} disabled={regenerating}>
                {regenerating ? "Generating…" : "Generate now"}
              </button>
            )}
          </div>
        ) : !meta ? (
          <div className="costs-empty">
            <p>
              Analysis exists, but this video predates Vidan's cost tracking —
              the original token usage wasn't captured. Regenerate to get a
              fresh breakdown (will use a small amount of Claude Code quota).
            </p>
            {onRegenerate && (
              <button className="btn" onClick={onRegenerate} disabled={regenerating}>
                {regenerating ? "Regenerating…" : "↻ Regenerate to capture costs"}
              </button>
            )}
          </div>
        ) : (
          <dl>
            <div><dt>Generated</dt><dd>{relativeTime(meta.generated_at) || "—"}</dd></div>
            <div><dt>Wall time</dt><dd className="mono">{fmtMs(meta.duration_ms) || "—"}</dd></div>
            {meta.duration_api_ms != null && meta.duration_api_ms > 0 && (
              <div><dt>API time</dt><dd className="mono">{fmtMs(meta.duration_api_ms)}</dd></div>
            )}
            {meta.num_turns != null && meta.num_turns > 0 && (
              <div><dt>Turns</dt><dd className="mono">{meta.num_turns}</dd></div>
            )}
            <div><dt>Input tokens</dt><dd className="mono">{fmtTokens(meta.tokens_in)}</dd></div>
            <div><dt>Output tokens</dt><dd className="mono">{fmtTokens(meta.tokens_out)}</dd></div>
            {meta.cache_read_tokens != null && meta.cache_read_tokens > 0 && (
              <div>
                <dt>Cache read</dt>
                <dd className="mono">
                  {fmtTokens(meta.cache_read_tokens)}
                  <span className="costs-note"> · cheaper than input</span>
                </dd>
              </div>
            )}
            {meta.cache_creation_tokens != null && meta.cache_creation_tokens > 0 && (
              <div>
                <dt>Cache creation</dt>
                <dd className="mono">{fmtTokens(meta.cache_creation_tokens)}</dd>
              </div>
            )}
            <div className="costs-total">
              <dt>Cost</dt>
              <dd className="mono costs-accent">{fmtCost(cost)}</dd>
            </div>
          </dl>
        )}
      </section>

      {/* ------------------------- TOTAL FOR THIS VIDEO ------------------ */}
      <section className="costs-section costs-total-section">
        <header>
          <h4>Total · this video</h4>
          <span className="costs-badge costs-cost">{fmtCost(cost)}</span>
        </header>
        <p className="costs-note-block">
          Transcription runs locally on your GPU — no dollar cost, only GPU
          time. Only Claude analysis incurs usage against your Claude Code
          account.
        </p>
        {meta && onRegenerate && (
          <div style={{ textAlign: "right" }}>
            <button className="btn" onClick={onRegenerate} disabled={regenerating}>
              {regenerating ? "Regenerating…" : "↻ Regenerate analysis"}
            </button>
          </div>
        )}
      </section>
    </div>
  );
}
