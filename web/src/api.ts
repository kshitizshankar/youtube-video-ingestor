import { authFetch, withAuthQuery } from "./auth";
import type { Analysis, Transcript, TranscriptSummary } from "./types";

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

/** Returns parsed analysis.json, or null if it doesn't exist yet (404). */
export async function getAnalysis(id: string): Promise<Analysis | null> {
  const r = await authFetch(`/api/transcripts/${id}/analysis`);
  if (r.status === 404) return null;
  if (!r.ok) throw new Error(`analysis ${id} failed: ${r.status}`);
  return r.json();
}

/** Manually re-run the Claude analysis pipeline for a video. Long-running. */
export async function triggerAnalyze(id: string): Promise<void> {
  const r = await authFetch(`/api/transcripts/${id}/analyze`, { method: "POST" });
  if (!r.ok) {
    const body = await r.text().catch(() => "");
    throw new Error(`analyze ${id} failed: ${r.status} ${body}`);
  }
}

export interface TranscribeOptions {
  diarize?: boolean;
  model?: string;
}

export function openTranscribeStream(
  url: string,
  opts: TranscribeOptions = {},
): EventSource {
  const params = new URLSearchParams({ url });
  if (opts.diarize) params.set("diarize", "true");
  if (opts.model) params.set("model", opts.model);
  return new EventSource(withAuthQuery(`/api/transcribe?${params.toString()}`));
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
  const m = url.match(
    /(?:youtube\.com\/watch\?v=|youtu\.be\/|youtube\.com\/embed\/|youtube\.com\/v\/)([A-Za-z0-9_-]{6,})/,
  );
  return m ? m[1] : null;
}
