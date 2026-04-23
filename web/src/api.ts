import { authFetch, withAuthQuery } from "./auth";
import type { AiProvider, Analysis, Transcript, TranscriptSummary, VideoMeta } from "./types";

export async function listTranscripts(): Promise<TranscriptSummary[]> {
  const r = await authFetch("/api/transcripts");
  if (!r.ok) throw new Error(`list failed: ${r.status}`);
  return r.json();
}

export async function getTranscript(id: string): Promise<Transcript> {
  const r = await authFetch(`/api/transcripts/${id}`);
  if (!r.ok) throw new Error(`get ${id} failed: ${r.status}`);
  return r.json();
}

export async function getMeta(id: string): Promise<VideoMeta> {
  const r = await authFetch(`/api/transcripts/${id}/meta`);
  if (!r.ok) throw new Error(`meta ${id} failed: ${r.status}`);
  return r.json();
}

export async function patchMeta(id: string, patch: Partial<VideoMeta>): Promise<VideoMeta> {
  const r = await authFetch(`/api/transcripts/${id}/meta`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!r.ok) throw new Error(`meta patch ${id} failed: ${r.status}`);
  return r.json();
}

/** Returns parsed analysis.json, or null if it doesn't exist yet (404). */
export async function getAnalysis(id: string): Promise<Analysis | null> {
  const r = await authFetch(`/api/transcripts/${id}/analysis`);
  if (r.status === 404) return null;
  if (!r.ok) throw new Error(`analysis ${id} failed: ${r.status}`);
  return r.json();
}

/** Enumerate available AI analysis providers and their models. */
export async function listAiProviders(): Promise<AiProvider[]> {
  const r = await authFetch("/api/ai/providers");
  if (!r.ok) throw new Error(`listAiProviders: ${r.status}`);
  return r.json();
}

/** Kick off analysis for a given video. Fire-and-forget — watch
 *  `openAnalysisStream` for live progress. 400 if provider is unavailable. */
export async function triggerAnalyze(
  id: string,
  provider: string = "claude_cli",
  model?: string,
): Promise<{ started: boolean; provider: string; model: string | null }> {
  const r = await authFetch(`/api/transcripts/${id}/analyze`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ provider, model: model ?? null }),
  });
  if (!r.ok) {
    const body = await r.text().catch(() => "");
    try {
      const parsed = JSON.parse(body);
      throw new Error(parsed.detail || `triggerAnalyze: ${r.status}`);
    } catch (e) {
      if (e instanceof Error && e.message && !e.message.startsWith("Unexpected")) throw e;
      throw new Error(`triggerAnalyze: ${r.status}`);
    }
  }
  return r.json();
}

/** Ask the server to stop a running analysis. 404 = nothing in flight. */
export async function cancelAnalysis(analysisId: number): Promise<void> {
  const r = await authFetch(`/api/analyses/${analysisId}/cancel`, { method: "POST" });
  if (!r.ok && r.status !== 404) throw new Error(`cancelAnalysis: ${r.status}`);
}

/** Open an SSE stream that emits `stage`, `usage`, `done`, `error` events
 *  while the chosen provider analyzes the transcript. Caller must .close() it. */
export function openAnalysisStream(
  id: string,
  provider: string = "claude_cli",
  model?: string,
): EventSource {
  const params = new URLSearchParams({ provider });
  if (model) params.set("model", model);
  return new EventSource(
    withAuthQuery(`/api/transcripts/${id}/analyze/stream?${params.toString()}`),
  );
}

export interface TranscribeOptions {
  diarize?: boolean;
  model?: string;
  /** When true (default), uses faster-whisper's BatchedInferencePipeline —
   *  fast overall, but segments arrive in bursts. Set to false for a
   *  steadier "live" feel at the cost of 5-10× more wall time. */
  batched?: boolean;
}

export function openTranscribeStream(
  url: string,
  opts: TranscribeOptions = {},
): EventSource {
  const params = new URLSearchParams({ url });
  if (opts.diarize) params.set("diarize", "true");
  if (opts.model) params.set("model", opts.model);
  if (opts.batched === false) params.set("batched", "false");
  return new EventSource(withAuthQuery(`/api/transcribe?${params.toString()}`));
}

export interface IngestState {
  id: string;
  url: string | null;
  title: string | null;
  duration_sec: number | null;
  phase: string;
  started_at: number;
  last_event_at: number;
  segments: number;
  last_segment_end: number;
  done: boolean;
  error: string | null;
  cancel_requested: boolean;
}

export async function listIngests(): Promise<IngestState[]> {
  const r = await authFetch("/api/ingests");
  if (!r.ok) throw new Error(`list ingests failed: ${r.status}`);
  return r.json();
}

/** Kick a fresh transcription run for a stalled / errored / orphaned ingest.
 *  If `url` is omitted, the server uses the URL stored on the original record. */
export async function retryIngest(id: string, url?: string): Promise<void> {
  const r = await authFetch(`/api/ingests/${id}/retry`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(url ? { url } : {}),
  });
  if (!r.ok) {
    const body = await r.text().catch(() => "");
    throw new Error(`retry failed: ${r.status} ${body}`);
  }
}

/** Remove a zombie entry from the server's ingest registry. */
export async function dropIngest(id: string): Promise<void> {
  const r = await authFetch(`/api/ingests/${id}`, { method: "DELETE" });
  if (!r.ok && r.status !== 404) {
    const body = await r.text().catch(() => "");
    throw new Error(`drop failed: ${r.status} ${body}`);
  }
}

/** Request cooperative cancellation of a running ingest. Server aborts at
 *  the next phase boundary — takes effect within seconds for download /
 *  minutes for transcribe-mid-run. */
export async function cancelIngest(id: string): Promise<void> {
  const r = await authFetch(`/api/ingests/${id}/cancel`, { method: "POST" });
  if (!r.ok && r.status !== 404) {
    const body = await r.text().catch(() => "");
    throw new Error(`cancel failed: ${r.status} ${body}`);
  }
}

export async function listArchive(): Promise<TranscriptSummary[]> {
  const r = await authFetch("/api/archive");
  if (!r.ok) throw new Error(`list archive failed: ${r.status}`);
  return r.json();
}

export async function archiveVideo(id: string): Promise<void> {
  const r = await authFetch(`/api/transcripts/${id}/archive`, { method: "POST" });
  if (!r.ok) throw new Error(`archive failed: ${r.status}`);
}

export async function restoreVideo(id: string): Promise<void> {
  const r = await authFetch(`/api/transcripts/${id}/restore`, { method: "POST" });
  if (!r.ok) throw new Error(`restore failed: ${r.status}`);
}

export async function deleteVideo(id: string): Promise<void> {
  const r = await authFetch(`/api/transcripts/${id}`, { method: "DELETE" });
  if (!r.ok) {
    const body = await r.text().catch(() => "");
    throw new Error(`delete failed: ${r.status} ${body}`);
  }
}

export async function refreshMetadata(id: string): Promise<void> {
  const r = await authFetch(`/api/transcripts/${id}/refresh-metadata`, {
    method: "POST",
  });
  if (!r.ok) {
    const body = await r.text().catch(() => "");
    throw new Error(`refresh-metadata failed: ${r.status} ${body}`);
  }
}

export interface SearchHit {
  video_id: string;
  title: string;
  start: number;
  end: number;
  speaker: string | null;
  text: string;
}
export interface SearchResponse {
  query: string;
  results: SearchHit[];
  truncated: boolean;
}

export async function searchTranscripts(q: string): Promise<SearchResponse> {
  if (!q.trim()) return { query: q, results: [], truncated: false };
  const r = await authFetch(`/api/search?q=${encodeURIComponent(q)}`);
  if (!r.ok) throw new Error(`search failed: ${r.status}`);
  return r.json();
}

/** Build a ws:// URL for the per-video PTY (claude session in `output/<id>/`).
 *  Token is appended as a query param because browsers won't let us send an
 *  Authorization header on a WebSocket handshake. */
export function ptyWebSocketUrl(videoId?: string): string {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  const qs = videoId ? `?video_id=${encodeURIComponent(videoId)}` : "";
  return withAuthQuery(`${proto}//${window.location.host}/api/pty${qs}`);
}

export function extractVideoId(url: string): string | null {
  const trimmed = (url || "").trim();
  const m = trimmed.match(
    /(?:youtube\.com\/watch\?v=|youtu\.be\/|youtube\.com\/embed\/|youtube\.com\/v\/|youtube\.com\/shorts\/|m\.youtube\.com\/watch\?v=|music\.youtube\.com\/watch\?v=)([A-Za-z0-9_-]{11})/,
  );
  if (m) return m[1];
  // Accept a bare 11-character YouTube id so users can paste just the id.
  if (/^[A-Za-z0-9_-]{11}$/.test(trimmed)) return trimmed;
  return null;
}
